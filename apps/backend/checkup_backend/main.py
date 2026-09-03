from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

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
        version="0.4.1",
        description="医院体检智能排序系统的多医院 Backend API",
        lifespan=lifespan,
    )
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.add_middleware(GZipMiddleware, minimum_size=1000)

    allowed_origins = [item.strip() for item in os.getenv("ALLOWED_ORIGINS", "").split(",") if item.strip()]
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

