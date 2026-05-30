"""Optional FastAPI surface."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from ..application.factory import build_adapters, build_query_pipeline
from ..infra.config import get_settings
from ..infra.logging import configure_logging


class AskRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings)
    adapters = build_adapters(settings)
    app.state.adapters = adapters
    app.state.pipeline = build_query_pipeline(adapters)
    try:
        yield
    finally:
        await adapters.aclose()


app = FastAPI(title="chat-legal-vn", lifespan=lifespan)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/ask")
async def ask(req: AskRequest) -> dict[str, Any]:
    pipeline = app.state.pipeline
    try:
        result = await pipeline.run(req.query)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {
        "answer": result.answer.model_dump(mode="json"),
        "plan": result.plan.model_dump(mode="json"),
        "timings": result.timings,
        "retrieved": [c.chunk_id for c in result.retrieved[:20]],
        "retained": [c.chunk_id for c in result.expanded[: pipeline._settings.reranker_top_k]],
    }
