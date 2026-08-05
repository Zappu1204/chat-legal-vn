"""Password hashing (argon2id) and JWT token handling.

We use argon2id — the current OWASP-recommended password hash — via
``argon2-cffi`` rather than bcrypt, and hand-rolled JWT via ``PyJWT`` rather
than a heavyweight auth framework so the RBAC and token lifecycle stay fully
under our control. Tokens carry the subject (user id), role, and a ``type``
claim distinguishing access from refresh tokens.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from .settings import get_web_settings

_ph = PasswordHasher()

TokenType = Literal["access", "refresh"]


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return _ph.verify(hashed, password)
    except (VerifyMismatchError, InvalidHashError, ValueError):
        return False


def needs_rehash(hashed: str) -> bool:
    try:
        return _ph.check_needs_rehash(hashed)
    except (InvalidHashError, ValueError):
        return True


def _now() -> datetime:
    return datetime.now(tz=UTC)


def create_token(
    *,
    subject: str,
    role: str,
    token_type: TokenType,
) -> str:
    settings = get_web_settings()
    if token_type == "access":
        expires = _now() + timedelta(minutes=settings.access_token_ttl_minutes)
    else:
        expires = _now() + timedelta(days=settings.refresh_token_ttl_days)
    payload: dict[str, Any] = {
        "sub": subject,
        "role": role,
        "type": token_type,
        "iat": int(_now().timestamp()),
        "exp": int(expires.timestamp()),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(
        payload,
        settings.jwt_secret.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )


def decode_token(token: str) -> dict[str, Any]:
    """Decode and validate a JWT. Raises ``jwt.PyJWTError`` on failure."""
    settings = get_web_settings()
    return jwt.decode(
        token,
        settings.jwt_secret.get_secret_value(),
        algorithms=[settings.jwt_algorithm],
    )
