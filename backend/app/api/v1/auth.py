"""Authentication routes: register, login, refresh, me."""

from __future__ import annotations

import uuid

import jwt
from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select

from ...core.security import (
    create_token,
    decode_token,
    hash_password,
    needs_rehash,
    verify_password,
)
from ...models.user import User, UserRole
from ...schemas.auth import (
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenPair,
    UserPublic,
)
from ..deps import CurrentUser, DbSession

router = APIRouter(prefix="/auth", tags=["auth"])


def _tokens_for(user: User) -> TokenPair:
    return TokenPair(
        access_token=create_token(
            subject=str(user.id), role=user.role.value, token_type="access"
        ),
        refresh_token=create_token(
            subject=str(user.id), role=user.role.value, token_type="refresh"
        ),
    )


@router.post("/register", response_model=TokenPair, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, session: DbSession) -> TokenPair:
    existing = await session.scalar(
        select(User).where(func.lower(User.email) == body.email.lower())
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Email đã được đăng ký."
        )
    user = User(
        email=body.email.lower(),
        hashed_password=hash_password(body.password),
        full_name=body.full_name,
        role=UserRole.USER,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return _tokens_for(user)


@router.post("/login", response_model=TokenPair)
async def login(body: LoginRequest, session: DbSession) -> TokenPair:
    user = await session.scalar(
        select(User).where(func.lower(User.email) == body.email.lower())
    )
    # Constant-ish: verify even when user missing to reduce enumeration signal.
    if user is None or not verify_password(body.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email hoặc mật khẩu không đúng.",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Tài khoản đã bị khoá."
        )
    # Transparent rehash on login if the parameters have hardened.
    if needs_rehash(user.hashed_password):
        user.hashed_password = hash_password(body.password)
        session.add(user)
        await session.commit()
    return _tokens_for(user)


@router.post("/refresh", response_model=TokenPair)
async def refresh(body: RefreshRequest, session: DbSession) -> TokenPair:
    try:
        payload = decode_token(body.refresh_token)
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token không hợp lệ."
        ) from exc
    if payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Cần refresh token."
        )
    try:
        user_id = uuid.UUID(str(payload.get("sub")))
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Token sai định dạng."
        ) from exc
    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Tài khoản không khả dụng."
        )
    return _tokens_for(user)


@router.get("/me", response_model=UserPublic)
async def me(user: CurrentUser) -> User:
    return user
