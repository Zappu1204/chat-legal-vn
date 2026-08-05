"""vLLM-served Qwen3-Reranker-8B adapter.

The model is served by vLLM as `Qwen3ForSequenceClassification` with
`classifier_from_token=["no","yes"]`, which gives us a single relevance
score in [0, 1] per (query, document) pair. vLLM exposes both:

- `POST /v1/score`   — single pair (`text_1`, `text_2`)
- `POST /v1/rerank`  — Cohere/Jina-compatible batch interface

We prefer `/v1/rerank` for batch efficiency and fall back to `/v1/score`.

The Qwen3 reranker is also "Instruction Aware": the query is wrapped with a
task-specific instruction prefix on the **client** side. The model card
shows the format `<Instruct>: <task>\\n<Query>: <q>` for the query side and
`<Document>: <doc>` for the document side, but the vLLM
`Qwen3ForSequenceClassification` server applies its own chat template
internally. We therefore prepend the instruction to the query string only —
the server formats the rest.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ...domain.ports import RerankerAdapter
from ...infra.config import Settings
from ...infra.logging import get_logger
from ...infra.tokens import estimate_tokens, max_chars_for_tokens

log = get_logger(__name__)


class VLLMQwen3RerankerAdapter(RerankerAdapter):
    """Cohere-compatible (`/v1/rerank`) reranker over vLLM."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._base = settings.reranker_base_url.rstrip("/")
        api_key = settings.reranker_api_key.get_secret_value()
        self._headers = (
            {"Authorization": f"Bearer {api_key}"} if api_key and api_key != "sk-dummy-key" else {}
        )
        self._client = httpx.AsyncClient(
            timeout=settings.reranker_timeout_seconds,
            headers={"Content-Type": "application/json", **self._headers},
        )

    @property
    def model_name(self) -> str:
        return self._settings.reranker_model_name

    def _format_query(self, query: str) -> str:
        instr = self._settings.reranker_task_instruction
        return f"Instruct: {instr}\nQuery:{query}"

    async def aclose(self) -> None:
        await self._client.aclose()

    async def rerank(
        self,
        *,
        query: str,
        documents: Sequence[str],
        top_n: int | None = None,
    ) -> list[tuple[int, float]]:
        if not documents:
            return []

        wrapped_query = self._format_query(query)
        bs = self._settings.reranker_batch_size

        # The reranker scores (query, document) PAIRS, so the budget must
        # cover BOTH the wrapped query and the document plus the model's
        # template/special tokens. We compute the per-document character
        # budget from the calibrated chars/token ratio (~2.6 for VN legal
        # text), subtract what the query already consumes, and reserve a
        # margin for the chat template the server injects.
        query_tokens = estimate_tokens(wrapped_query)
        template_reserve_tokens = 256
        doc_token_budget = (
            self._settings.reranker_max_input_tokens
            - query_tokens
            - template_reserve_tokens
        )
        doc_token_budget = max(128, doc_token_budget)
        char_budget_per_doc = max_chars_for_tokens(doc_token_budget)
        truncated_docs = [
            d[:char_budget_per_doc] if isinstance(d, str) else ""
            for d in documents
        ]

        all_scores: list[tuple[int, float]] = []
        for offset in range(0, len(truncated_docs), bs):
            batch = list(truncated_docs[offset : offset + bs])
            scores = await self._rerank_batch(wrapped_query, batch)
            for local_idx, score in scores:
                all_scores.append((offset + local_idx, score))

        # Stable ordering. The vLLM reranker is NOT bit-exact run-to-run:
        # GPU kernels + continuous batching introduce float noise on the
        # order of 1e-3 in the relevance score. Two near-tied documents
        # therefore swap places between identical calls, which propagates
        # all the way to a different final answer. We quantise the score
        # to a fixed precision so sub-`rank_score_round` noise no longer
        # changes the ordering, then break exact ties by the original
        # document index (deterministic given a fixed candidate list).
        ndigits = max(0, int(self._settings.reranker_score_round))
        all_scores.sort(key=lambda x: (-round(x[1], ndigits), x[0]))
        if top_n is not None:
            all_scores = all_scores[:top_n]
        return all_scores

    async def _rerank_batch(
        self, query: str, documents: Sequence[str]
    ) -> list[tuple[int, float]]:
        # Try /v1/rerank first
        url = f"{self._base}/rerank"
        body: dict[str, Any] = {
            "model": self._settings.reranker_model_name,
            "query": query,
            "documents": list(documents),
        }
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=1, max=10),
            retry=retry_if_exception_type((httpx.HTTPError,)),
            reraise=True,
        ):
            with attempt:
                resp = await self._client.post(url, json=body)
                if resp.status_code == 404:
                    return await self._fallback_score(query, documents)
                if resp.status_code == 400:
                    # vLLM raises 400 when a (query, doc) pair exceeds
                    # max-model-len, even on a single oversized doc. Fall
                    # back to a per-pair scorer with hard truncation so
                    # one bad doc cannot poison the entire batch.
                    log.warning(
                        "reranker.batch_400_falling_back_per_pair",
                        batch_size=len(documents),
                        body=resp.text[:200],
                    )
                    return await self._fallback_score(query, documents)
                resp.raise_for_status()
                data = resp.json()
                results = data.get("results") or []
                pairs: list[tuple[int, float]] = []
                for r in results:
                    idx = int(r.get("index"))
                    score = float(r.get("relevance_score", r.get("score", 0.0)))
                    pairs.append((idx, score))
                return pairs
        return []

    async def _fallback_score(
        self, query: str, documents: Sequence[str]
    ) -> list[tuple[int, float]]:
        out: list[tuple[int, float]] = []
        for i, doc in enumerate(documents):
            url = f"{self._base}/score"
            body = {
                "model": self._settings.reranker_model_name,
                "text_1": query,
                "text_2": doc,
            }
            try:
                resp = await self._client.post(url, json=body)
                resp.raise_for_status()
                data = resp.json()
                score = float(
                    data.get("score") or data.get("data", [{}])[0].get("score", 0.0)
                )
                out.append((i, score))
            except httpx.HTTPError as exc:
                # Per-pair failure: log and assign neutral score so the
                # candidate is not silently dropped.
                log.warning(
                    "reranker.score_failed",
                    index=i,
                    error=str(exc),
                )
                out.append((i, 0.0))
        return out
