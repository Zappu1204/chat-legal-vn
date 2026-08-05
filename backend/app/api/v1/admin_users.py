"""Admin: user management (list, change role, activate/deactivate)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from ...models.user import User
from ...schemas.auth import RoleUpdate, UserPublic
from ..deps import AdminUser, DbSession

router = APIRouter(prefix="/admin/users", tags=["admin"])


@router.get("", response_model=list[UserPublic])
async def list_users(admin: AdminUser, session: DbSession) -> list[User]:
    rows = await session.scalars(select(User).order_by(User.created_at.asc()))
    return list(rows)


@router.put("/{user_id}/role", response_model=UserPublic)
async def set_role(
    user_id: uuid.UUID, body: RoleUpdate, admin: AdminUser, session: DbSession
) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy.")
    if user.id == admin.id and body.role.value != "admin":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Không thể tự hạ quyền chính mình.",
        )
    user.role = body.role
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


@router.put("/{user_id}/active", response_model=UserPublic)
async def set_active(
    user_id: uuid.UUID, is_active: bool, admin: AdminUser, session: DbSession
) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy.")
    if user.id == admin.id and not is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Không thể tự khoá chính mình.",
        )
    user.is_active = is_active
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user
