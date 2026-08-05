"""Chat service: turn a QueryPipeline stream into an SSE event stream.

Wire protocol (Server-Sent Events). Each event has an ``event:`` name and a
JSON ``data:`` payload, so the frontend can branch on the name without parsing
a custom token framing:

    event: sources        # emitted ONCE, before any text
    data: {"sources": [SourceCard, ...]}

    event: text-delta     # emitted many times
    data: {"delta": "..."}

    event: citations      # emitted ONCE, after text completes
    data: {"citations": [Citation, ...], "warnings": [...]}

    event: done
    data: {"timings": {...}, "revision": N}

    event: error          # emitted instead of `done` on failure
    data: {"message": "..."}

A ``SourceCard`` is a flattened, display-ready view of a retained
``RetrievalCandidate`` + its parent article, with the fields the UI needs to
render a reference card (document number, hierarchical path, snippet,
effective status, rerank score).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from chat_legal_vn.application.query_pipeline import QueryPipeline, StreamHandle
from chat_legal_vn.domain.entities import ParentChunk, RetrievalCandidate
from chat_legal_vn.services.citation import parse_citations

from ..core.logging import get_logger

log = get_logger(__name__)


def _sse(event: str, data: dict[str, Any]) -> str:
    """Format a single SSE event. ``ensure_ascii=False`` keeps Vietnamese
    text readable on the wire; newlines in data are escaped by json.dumps."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _snippet(text: str, limit: int = 320) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def source_card(
    cand: RetrievalCandidate,
    parents: dict[str, ParentChunk],
    index: int,
) -> dict[str, Any]:
    parent = parents.get(cand.parent_chunk_id)
    meta = cand.metadata or {}
    return {
        "index": index,
        "chunk_id": cand.chunk_id,
        "document_id": cand.document_id,
        "loai_van_ban": meta.get("loai_van_ban"),
        "so_ky_hieu": meta.get("so_ky_hieu"),
        "co_quan_ban_hanh": meta.get("co_quan_ban_hanh"),
        "hierarchical_path": cand.hierarchical_path,
        "heading": parent.heading if parent else None,
        "effective_status": meta.get("effective_status"),
        "ngay_ban_hanh": meta.get("ngay_ban_hanh"),
        "score": round(float(cand.score), 4),
        "source": cand.source,
        "snippet": _snippet(cand.body or (parent.body if parent else "")),
    }


async def stream_chat_sse(
    pipeline: QueryPipeline,
    query: str,
    *,
    revision: int = 0,
) -> AsyncIterator[str]:
    """Yield SSE-formatted strings for one question.

    Resilient by design: retrieval failures surface as an ``error`` event so
    the client always receives a terminal event.
    """
    try:
        handle: StreamHandle = await pipeline.run_stream(query)
    except Exception as exc:
        log.error("chat_service.retrieval_failed", error=str(exc))
        yield _sse("error", {"message": "Không thể truy hồi tài liệu cho câu hỏi này."})
        return

    # 1) Emit the retrieved sources immediately so the UI can render cards
    #    before the answer starts streaming.
    cards = [
        source_card(c, handle.parents, i)
        for i, c in enumerate(handle.retained)
    ]
    yield _sse("sources", {"sources": cards})

    # 2) Stream the answer text, accumulating it for citation extraction.
    accumulated: list[str] = []
    try:
        async for delta in handle.answer_deltas():
            accumulated.append(delta)
            yield _sse("text-delta", {"delta": delta})
    except Exception as exc:
        log.error("chat_service.stream_failed", error=str(exc))
        yield _sse("error", {"message": "Lỗi khi tạo câu trả lời."})
        return

    full_text = "".join(accumulated)

    # 3) Parse citations from the accumulated answer text and emit them.
    citations = [c.model_dump(mode="json") for c in parse_citations(full_text)]
    yield _sse("citations", {"citations": citations, "warnings": []})

    # 4) Terminal event with timings + the config revision that served this.
    yield _sse(
        "done",
        {
            "timings": {k: round(v, 3) for k, v in handle.timings.items()},
            "revision": revision,
            "answer_chars": len(full_text),
        },
    )


def assistant_message_meta(
    handle_cards: list[dict[str, Any]],
    citations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the ``meta`` JSON persisted on an assistant Message."""
    return {"sources": handle_cards, "citations": citations}
