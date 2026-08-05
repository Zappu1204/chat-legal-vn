"""Streaming chat endpoint.

``POST /chat/stream`` returns an SSE stream. The pipeline is fetched from the
hot-reloadable registry, so a config change between requests is picked up
without a restart. The conversation + messages are persisted in a background
task after the stream finishes so streaming latency is unaffected.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse

from ...db.session import get_sessionmaker
from ...models.conversation import Conversation, Message
from ...schemas.chat import ChatRequest
from ...services.chat_service import stream_chat_sse
from ..deps import CurrentUser, DbSession, Registry

router = APIRouter(prefix="/chat", tags=["chat"])

# SSE response headers: disable proxy buffering so deltas flush immediately.
_SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


async def _resolve_conversation(
    session: DbSession, user_id: uuid.UUID, conversation_id: uuid.UUID | None, query: str
) -> Conversation:
    if conversation_id is not None:
        convo = await session.get(Conversation, conversation_id)
        if convo is None or convo.user_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Không tìm thấy cuộc trò chuyện.",
            )
        return convo
    title = query.strip()[:60] or "Cuộc trò chuyện mới"
    convo = Conversation(user_id=user_id, title=title)
    session.add(convo)
    await session.commit()
    await session.refresh(convo)
    return convo


@router.post("/stream")
async def chat_stream(
    body: ChatRequest,
    user: CurrentUser,
    session: DbSession,
    registry: Registry,
) -> StreamingResponse:
    convo = await _resolve_conversation(session, user.id, body.conversation_id, body.query)

    # Persist the user's message up front.
    session.add(
        Message(conversation_id=convo.id, role="user", content=body.query)
    )
    await session.commit()

    pipeline = await registry.get_pipeline()
    revision = await registry.current_revision()

    async def event_generator() -> AsyncIterator[str]:
        # Send the conversation id first so the client can associate the stream.
        yield f"event: meta\ndata: {json.dumps({'conversation_id': str(convo.id)})}\n\n"

        sources_payload: list = []
        citations_payload: list = []
        text_parts: list[str] = []

        async for event in stream_chat_sse(pipeline, body.query, revision=revision):
            # Tee the payloads we need to persist out of the SSE strings.
            if event.startswith("event: sources"):
                sources_payload = _extract_data(event).get("sources", [])
            elif event.startswith("event: text-delta"):
                text_parts.append(_extract_data(event).get("delta", ""))
            elif event.startswith("event: citations"):
                citations_payload = _extract_data(event).get("citations", [])
            yield event

        # Persist the assistant message once the stream is complete.
        await _persist_assistant(
            convo_id=convo.id,
            content="".join(text_parts),
            sources=sources_payload,
            citations=citations_payload,
        )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


def _extract_data(sse_event: str) -> dict:
    """Pull the JSON payload out of a formatted SSE event string."""
    for line in sse_event.splitlines():
        if line.startswith("data:"):
            try:
                return json.loads(line[len("data:") :].strip())
            except json.JSONDecodeError:
                return {}
    return {}


async def _persist_assistant(
    *,
    convo_id: uuid.UUID,
    content: str,
    sources: list,
    citations: list,
) -> None:
    """Open a fresh session (the request session is consumed by streaming)."""
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        session.add(
            Message(
                conversation_id=convo_id,
                role="assistant",
                content=content,
                meta={"sources": sources, "citations": citations},
            )
        )
        convo = await session.get(Conversation, convo_id)
        if convo is not None:
            from datetime import datetime

            convo.updated_at = datetime.now(tz=UTC)
            session.add(convo)
        await session.commit()
