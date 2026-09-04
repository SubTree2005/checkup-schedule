from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import inspect, text
from sqlalchemy.engine import URL

from . import models  # noqa: F401 - register SQLAlchemy metadata
from .agent_api import router as patient_agent_router
from .api import router
from .database import Base, build_engine, build_session_factory
from .middleware import SecurityBoundaryMiddleware
from .patient_api import router as patient_router
from .reminder_api import internal_reminder_router, patient_reminder_router


def ensure_compatible_columns(engine) -> None:
    """Add small, backward-compatible profile columns for existing deployments."""
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    with engine.begin() as connection:
        if "hospital_settings" in tables:
            existing = {column["name"] for column in inspector.get_columns("hospital_settings")}
            additions = {
                "hospitalLevel": "VARCHAR(50) NOT NULL DEFAULT '未定级'",
                "positioning": "VARCHAR(100) NOT NULL DEFAULT '综合医疗机构'",
            }
            for name, declaration in additions.items():
                if name not in existing:
                    connection.execute(text(f"ALTER TABLE hospital_settings ADD COLUMN {name} {declaration}"))
        if "user_info" in tables:
            user_columns = {column["name"] for column in inspector.get_columns("user_info")}
            if "avatarUrl" not in user_columns:
                declaration = "LONGTEXT" if engine.dialect.name == "mysql" else "TEXT"
                connection.execute(text(f"ALTER TABLE user_info ADD COLUMN avatarUrl {declaration}"))
        if "queue_snapshot" in tables:
            queue_table = Base.metadata.tables["queue_snapshot"]
            queue_index = next(
                index
                for index in queue_table.indexes
                if index.name == "ix_queue_snapshot_item_valid_created"
            )
            queue_index.create(bind=connection, checkfirst=True)
        if "exam_plan" in tables:
            plan_columns = {column["name"] for column in inspector.get_columns("exam_plan")}
            appointment_added = "appointmentAt" not in plan_columns
            if appointment_added:
                connection.execute(text("ALTER TABLE exam_plan ADD COLUMN appointmentAt DATETIME NULL"))
            if appointment_added and "user_status_info" in tables and "recordID" in plan_columns:
                legacy_rows = connection.execute(
                    text(
                        "SELECT p.planID, s.profileData FROM exam_plan AS p "
                        "LEFT JOIN user_status_info AS s ON s.recordID = p.recordID "
                        "WHERE p.appointmentAt IS NULL"
                    )
                ).mappings()
                for row in legacy_rows:
                    raw_profile = row["profileData"]
                    if isinstance(raw_profile, (bytes, bytearray)):
                        raw_profile = raw_profile.decode("utf-8", errors="replace")
                    try:
                        profile = json.loads(raw_profile) if isinstance(raw_profile, str) else raw_profile
                    except (TypeError, ValueError):
                        continue
                    appointment_value = profile.get("appointmentAt") if isinstance(profile, dict) else None
                    if not isinstance(appointment_value, str) or not appointment_value:
                        continue
                    try:
                        appointment = datetime.fromisoformat(appointment_value.replace("Z", "+00:00"))
                    except ValueError:
                        continue
                    if appointment.tzinfo is not None:
                        appointment = appointment.astimezone(UTC).replace(tzinfo=None)
                    connection.execute(
                        text("UPDATE exam_plan SET appointmentAt = :appointment WHERE planID = :plan_id"),
                        {"appointment": appointment, "plan_id": row["planID"]},
                    )
            plan_table = Base.metadata.tables["exam_plan"]
            plan_index = next(
                index
                for index in plan_table.indexes
                if index.name == "ix_plan_hospital_appointment_status"
            )
            plan_index.create(bind=connection, checkfirst=True)


def resolve_database_url(explicit_url: str | None = None) -> str:
    if explicit_url:
        return explicit_url

    configured_url = os.getenv("DATABASE_URL")
    if configured_url:
        return configured_url

    mysql_values = {
        "MYSQL_ADDRESS": os.getenv("MYSQL_ADDRESS"),
        "MYSQL_USERNAME": os.getenv("MYSQL_USERNAME"),
        "MYSQL_PASSWORD": os.getenv("MYSQL_PASSWORD"),
    }
    if any(mysql_values.values()):
        missing = [name for name, value in mysql_values.items() if not value]
        if missing:
            raise RuntimeError(f"incomplete MySQL configuration: missing {', '.join(missing)}")

        address = mysql_values["MYSQL_ADDRESS"] or ""
        host, separator, port_text = address.rpartition(":")
        if not separator or not host or not port_text.isdigit():
            raise RuntimeError("MYSQL_ADDRESS must use the host:port format")
        return URL.create(
            "mysql+pymysql",
            username=mysql_values["MYSQL_USERNAME"],
            password=mysql_values["MYSQL_PASSWORD"],
            host=host,
            port=int(port_text),
            database=os.getenv("MYSQL_DATABASE", "checkup_schedule"),
            query={"charset": "utf8mb4"},
        ).render_as_string(hide_password=False)

    return "sqlite:///./checkup.db"


def resolve_allowed_origins(raw_value: str | None = None) -> list[str]:
    raw = os.getenv("ALLOWED_ORIGINS", "") if raw_value is None else raw_value
    result: list[str] = []
    for item in raw.split(","):
        origin = item.strip().rstrip("/")
        if not origin:
            continue
        parsed = urlsplit(origin)
        if (
            origin == "*"
            or parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise RuntimeError("ALLOWED_ORIGINS must contain exact HTTP(S) origins without paths or wildcards")
        if origin not in result:
            result.append(origin)
    return result


def create_app(database_url: str | None = None, static_dir: str | Path | None = None) -> FastAPI:
    resolved_database_url = resolve_database_url(database_url)
    engine = build_engine(resolved_database_url)
    session_factory = build_session_factory(engine)
    admin_dir = Path(static_dir) if static_dir else Path(__file__).resolve().parents[2] / "admin-web"

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        Base.metadata.create_all(bind=engine)
        ensure_compatible_columns(engine)
        yield
        engine.dispose()

    app = FastAPI(
        title="Checkup Schedule API",
        version="0.4.2",
        description="医院体检智能排序系统的多医院 Backend API",
        lifespan=lifespan,
    )
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    app.add_middleware(SecurityBoundaryMiddleware)

    allowed_origins = resolve_allowed_origins()
    if allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=allowed_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["Content-Type", "Authorization"],
        )

    app.include_router(router)
    app.include_router(patient_router)
    app.include_router(patient_agent_router)
    app.include_router(patient_reminder_router)
    app.include_router(internal_reminder_router)
    if (admin_dir / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=admin_dir / "assets"), name="admin-assets")

    @app.get("/", include_in_schema=False)
    def admin_index() -> FileResponse:
        return FileResponse(admin_dir / "index.html")

    return app


app = create_app()

