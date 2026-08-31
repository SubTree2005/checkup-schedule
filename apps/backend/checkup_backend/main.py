from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import models  # noqa: F401 - register SQLAlchemy metadata
from .api import router
from .database import Base, build_engine, build_session_factory
from .patient_api import router as patient_router


def create_app(database_url: str | None = None, static_dir: str | Path | None = None) -> FastAPI:
    resolved_database_url = database_url or os.getenv("DATABASE_URL", "sqlite:///./checkup.db")
    engine = build_engine(resolved_database_url)
    session_factory = build_session_factory(engine)
    admin_dir = Path(static_dir) if static_dir else Path(__file__).resolve().parents[2] / "admin-web"

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        Base.metadata.create_all(bind=engine)
        yield
        engine.dispose()

    app = FastAPI(
        title="Checkup Schedule API",
        version="0.2.0",
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
    if (admin_dir / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=admin_dir / "assets"), name="admin-assets")

    @app.get("/", include_in_schema=False)
    def admin_index() -> FileResponse:
        return FileResponse(admin_dir / "index.html")

    return app


app = create_app()

