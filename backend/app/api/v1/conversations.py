"""Conversation history routes (per-user scoped)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import delete, select

from ...models.conversation import Conversation, Message
from ...schemas.chat import ConversationDetail, ConversationPublic, MessagePublic
from ..deps import CurrentUser, DbSession

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.get("", response_model=list[ConversationPublic])
async def list_conversations(user: CurrentUser, session: DbSession) -> list[Conversation]:
    rows = await session.scalars(
        select(Conversation)
        .where(Conversation.user_id == user.id)
        .order_by(Conversation.updated_at.desc())
    )
    return list(rows)


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: uuid.UUID, user: CurrentUser, session: DbSession
) -> ConversationDetail:
    convo = await session.get(Conversation, conversation_id)
    if convo is None or convo.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy.")
    msgs = await session.scalars(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
    )
    return ConversationDetail(
        id=convo.id,
        title=convo.title,
        created_at=convo.created_at,
        updated_at=convo.updated_at,
        messages=[MessagePublic.model_validate(m, from_attributes=True) for m in msgs],
    )


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: uuid.UUID, user: CurrentUser, session: DbSession
) -> None:
    convo = await session.get(Conversation, conversation_id)
    if convo is None or convo.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy.")
    await session.execute(
        delete(Message).where(Message.conversation_id == conversation_id)
    )
    await session.delete(convo)
    await session.commit()
