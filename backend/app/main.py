"""FastAPI application factory and lifespan.

Lifespan responsibilities:
1. Ensure DB schema exists (dev/test convenience; production uses Alembic).
2. Seed the bootstrap admin if no admin user exists.
3. Initialize the hot-reloadable AdapterRegistry from the persisted config.
4. Create the in-process JobManager for pipeline runs.
On shutdown, the registry's adapters are closed cleanly.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from chat_legal_vn.infra.config import get_settings
from chat_legal_vn.infra.logging import configure_logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select
from sqlmodel import SQLModel

from . import models  # noqa: F401 — registers tables on SQLModel.metadata
from .core.logging import get_logger
from .core.security import hash_password
from .core.settings import get_web_settings
from .db.session import get_engine, get_sessionmaker
from .models.user import User, UserRole
from .services.adapter_registry import AdapterRegistry
from .services.config_service import bootstrap_registry
from .services.pipeline_jobs import JobManager

log = get_logger(__name__)


async def _create_schema() -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)


async def _seed_admin() -> None:
    settings = get_web_settings()
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        admin_count = await session.scalar(
            select(func.count()).select_from(User).where(User.role == UserRole.ADMIN)
        )
        if admin_count and admin_count > 0:
            return
        admin = User(
            email=settings.bootstrap_admin_email.lower(),
            hashed_password=hash_password(
                settings.bootstrap_admin_password.get_secret_value()
            ),
            full_name="Administrator",
            role=UserRole.ADMIN,
        )
        session.add(admin)
        await session.commit()
        log.warning(
            "bootstrap_admin.created",
            email=settings.bootstrap_admin_email,
            note="change the bootstrap password immediately",
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    web_settings = get_web_settings()
    configure_logging(get_settings())  # core structlog setup

    await _create_schema()
    await _seed_admin()

    registry = AdapterRegistry()
    app.state.registry = registry
    app.state.jobs = JobManager()

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        await bootstrap_registry(session, registry)

    log.info("backend.started", project=web_settings.project_name)
    try:
        yield
    finally:
        await registry.aclose()
        await get_engine().dispose()


def create_app() -> FastAPI:
    web_settings = get_web_settings()
    app = FastAPI(
        title=f"{web_settings.project_name} API",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=web_settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Routers (imported here to keep import-time side effects contained).
    from .api.v1 import (
        admin_config,
        admin_pipeline,
        admin_users,
        auth,
        chat,
        conversations,
    )

    prefix = web_settings.api_prefix
    for module in (auth, chat, conversations, admin_config, admin_users, admin_pipeline):
        app.include_router(module.router, prefix=prefix)

    @app.get("/healthz", tags=["health"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
