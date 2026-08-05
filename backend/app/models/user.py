"""User model with role-based access control."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlmodel import Field, SQLModel

from ._types import tz_column, utcnow


class UserRole(str, enum.Enum):  # noqa: UP042 — pydantic + JSON serialization friendly
    USER = "user"
    ADMIN = "admin"


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    email: str = Field(index=True, unique=True, max_length=320)
    hashed_password: str
    full_name: str | None = Field(default=None, max_length=200)
    role: UserRole = Field(default=UserRole.USER)
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=utcnow, sa_column=tz_column())
    updated_at: datetime = Field(default_factory=utcnow, sa_column=tz_column())
