"""vLLM-served Qwen3-Embedding-8B adapter.

Throughput notes
================
The adapter sends documents in **micro-batches** of
``settings.embedding_batch_size`` items each, and runs up to
``settings.embedding_concurrency`` batches in flight. vLLM's continuous
batcher coalesces concurrent requests into a single GPU step, so adding
client-side parallelism is the canonical way to saturate the server: 4–8
parallel requests typically deliver 4–6× the throughput of serial calls
without any quality impact.

Failures are retried with exponential back-off; transient ``ConnectError``
/ ``ReadError`` / 5xx responses no longer abort the entire ingest run.

Reference: https://huggingface.co/Qwen/Qwen3-Embedding-8B
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from openai import APIConnectionError, APIError, AsyncOpenAI, RateLimitError
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ...domain.ports import EmbeddingAdapter
from ...infra.config import Settings
from ...infra.logging import get_logger

log = get_logger(__name__)


_RETRYABLE_EXCEPTIONS: tuple[type[Exception], ...] = (
    APIConnectionError,
    APIError,
    RateLimitError,
    TimeoutError,
    ConnectionError,
)


def format_query(*, query: str, task_instruction: str) -> str:
    """Apply the Qwen3 query instruction prefix.

    Format mandated by the Qwen team:
        Instruct: <task description>\nQuery:<query>
    """
    return f"Instruct: {task_instruction}\nQuery:{query}"


class VLLMQwen3EmbeddingAdapter(EmbeddingAdapter):
    """OpenAI-compatible embedding client targeting vLLM."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = AsyncOpenAI(
            base_url=settings.embedding_base_url,
            api_key=settings.embedding_api_key.get_secret_value(),
            timeout=settings.embedding_timeout_seconds,
            max_retries=0,
        )
        # Cap in-flight requests so we don't open dozens of sockets if the
        # caller pushes a very large batch through embed_documents.
        self._semaphore = asyncio.Semaphore(
            max(1, settings.embedding_concurrency)
        )

    @property
    def dim(self) -> int:
        return self._settings.embedding_dim

    @property
    def model_name(self) -> str:
        return self._settings.embedding_model_name

    async def _embed_one_batch(
        self, inputs: Sequence[str]
    ) -> list[list[float]]:
        if not inputs:
            return []
        async with self._semaphore:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(self._settings.embedding_max_retries),
                wait=wait_exponential(multiplier=1, min=1, max=20),
                retry=retry_if_exception_type(_RETRYABLE_EXCEPTIONS),
                reraise=True,
            ):
                with attempt:
                    resp = await self._client.embeddings.create(
                        model=self._settings.embedding_model_name,
                        input=list(inputs),
                        encoding_format="float",
                    )
                    return [d.embedding for d in resp.data]
        return []

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed many documents with bounded concurrency.

        Splits the input into ``embedding_batch_size`` chunks and dispatches
        them to ``_embed_one_batch`` via ``asyncio.gather``. Order is
        preserved so the caller's chunk-to-vector alignment stays valid.
        """
        if not texts:
            return []
        bs = self._settings.embedding_batch_size
        batches = [list(texts[i : i + bs]) for i in range(0, len(texts), bs)]
        results = await asyncio.gather(
            *(self._embed_one_batch(b) for b in batches),
            return_exceptions=False,
        )
        out: list[list[float]] = []
        for r in results:
            out.extend(r)
        return out

    async def embed_query(self, text: str) -> list[float]:
        wrapped = format_query(
            query=text, task_instruction=self._settings.embedding_task_instruction
        )
        vecs = await self._embed_one_batch([wrapped])
        return vecs[0] if vecs else []

    async def embed_queries(self, texts: Sequence[str]) -> list[list[float]]:
        wrapped = [
            format_query(
                query=t, task_instruction=self._settings.embedding_task_instruction
            )
            for t in texts
        ]
        return await self.embed_documents(wrapped)
