"""ConfigService: bridge between the SystemConfig DB row and the RAG core.

Responsibilities:
- Ensure a singleton SystemConfig row exists (seeded from the env defaults).
- Build an effective ``chat_legal_vn`` ``Settings`` by overlaying non-null
  DB overrides on top of the base environment configuration.
- Trigger an atomic hot-reload of the :class:`AdapterRegistry` whenever the
  admin updates the config.

Base (environment) settings always provide the store credentials and any
field the admin has not overridden. The DB only carries the *tunable* subset
defined by ``TUNABLE_FIELDS``.
"""

from __future__ import annotations

from typing import Any

from chat_legal_vn.infra.config import Settings, get_settings
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.logging import get_logger
from ..models.system_config import SINGLETON_ID, TUNABLE_FIELDS, SystemConfig
from .adapter_registry import AdapterRegistry

log = get_logger(__name__)


async def get_or_create_config(session: AsyncSession) -> SystemConfig:
    row = await session.get(SystemConfig, SINGLETON_ID)
    if row is None:
        row = SystemConfig(id=SINGLETON_ID)
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return row


def build_effective_settings(config: SystemConfig) -> Settings:
    """Overlay non-null DB overrides onto the base env settings.

    ``get_settings()`` is lru-cached and reflects the process ``.env``. We
    dump it to a dict, apply overrides, and re-validate so the result is a
    fully-typed, validated ``Settings`` instance (range checks included).
    """
    base = get_settings()
    data: dict[str, Any] = base.model_dump()

    for field in TUNABLE_FIELDS:
        value = getattr(config, field, None)
        if value is None:
            continue
        # Empty strings are treated as "not set" for secret/url fields so an
        # admin clearing a box doesn't blank out a working endpoint.
        if isinstance(value, str) and value.strip() == "":
            continue
        data[field] = value

    # Re-validate through the model (coerces types, enforces [0,1] ranges).
    return Settings.model_validate(data)


async def apply_and_reload(
    session: AsyncSession,
    registry: AdapterRegistry,
    config: SystemConfig,
) -> int:
    """Persist + hot-reload. Returns the new revision number."""
    config.revision = (config.revision or 0) + 1
    session.add(config)
    await session.commit()
    await session.refresh(config)

    settings = build_effective_settings(config)
    await registry.reload(settings, revision=config.revision)
    log.info("config_service.applied", revision=config.revision)
    return config.revision


async def bootstrap_registry(
    session: AsyncSession,
    registry: AdapterRegistry,
) -> None:
    """Initialize the registry from the persisted config at startup."""
    config = await get_or_create_config(session)
    settings = build_effective_settings(config)
    await registry.initialize(settings, revision=config.revision or 0)
