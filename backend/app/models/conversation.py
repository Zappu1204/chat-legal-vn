"""Conversation and Message models for chat history persistence."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from ._types import tz_column, utcnow

# Portable JSON column: JSONB on Postgres (indexable, binary), plain JSON
# elsewhere (e.g. SQLite in tests).
_JSON_VARIANT = JSON().with_variant(JSONB, "postgresql")


class Conversation(SQLModel, table=True):
    __tablename__ = "conversations"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(index=True, foreign_key="users.id")
    title: str = Field(default="Cuộc trò chuyện mới", max_length=300)
    created_at: datetime = Field(default_factory=utcnow, sa_column=tz_column())
    updated_at: datetime = Field(default_factory=utcnow, sa_column=tz_column())


class Message(SQLModel, table=True):
    __tablename__ = "messages"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    conversation_id: uuid.UUID = Field(index=True, foreign_key="conversations.id")
    role: str = Field(max_length=16)  # "user" | "assistant"
    content: str
    # Sources / citations / warnings attached to an assistant message.
    # JSONB on Postgres; SQLModel maps dict via the SA column type.
    meta: dict | None = Field(default=None, sa_column=Column(_JSON_VARIANT))
    created_at: datetime = Field(default_factory=utcnow, sa_column=tz_column(index=True))
