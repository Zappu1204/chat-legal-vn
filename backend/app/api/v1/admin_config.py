"""Admin: read and update the dynamic system configuration (hot-reload)."""

from __future__ import annotations

from fastapi import APIRouter

from ...schemas.config import SystemConfigRead, SystemConfigUpdate
from ...services.config_service import apply_and_reload, get_or_create_config
from ..deps import AdminUser, DbSession, Registry

router = APIRouter(prefix="/admin/config", tags=["admin"])


@router.get("", response_model=SystemConfigRead)
async def read_config(admin: AdminUser, session: DbSession) -> SystemConfigRead:
    config = await get_or_create_config(session)
    return SystemConfigRead.from_model(config)


@router.put("", response_model=SystemConfigRead)
async def update_config(
    body: SystemConfigUpdate,
    admin: AdminUser,
    session: DbSession,
    registry: Registry,
) -> SystemConfigRead:
    config = await get_or_create_config(session)

    # Apply only the fields explicitly provided (exclude_unset) so omitted
    # fields keep their stored value and secrets aren't blanked accidentally.
    updates = body.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(config, field, value)
    config.updated_by = admin.email

    await apply_and_reload(session, registry, config)
    return SystemConfigRead.from_model(config)


@router.get("/revision")
async def current_revision(admin: AdminUser, registry: Registry) -> dict[str, int]:
    return {"revision": await registry.current_revision()}
