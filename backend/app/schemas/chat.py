"""Chat + conversation schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    conversation_id: uuid.UUID | None = None


class ConversationPublic(BaseModel):
    id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime


class MessagePublic(BaseModel):
    id: uuid.UUID
    role: str
    content: str
    meta: dict[str, Any] | None = None
    created_at: datetime


class ConversationDetail(ConversationPublic):
    messages: list[MessagePublic] = []
