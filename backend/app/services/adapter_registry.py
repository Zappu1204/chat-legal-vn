"""Hot-reloadable adapter registry.

The RAG core builds a set of live adapters (LLM, embedding, reranker, stores)
plus a :class:`QueryPipeline` from an immutable ``Settings`` snapshot. To
change configuration at runtime *without restarting the server*, we keep the
current adapter bundle behind an ``asyncio.Lock``-guarded reference and swap
it atomically:

1. Build a brand-new bundle from the new ``Settings`` (this does not touch
   the live one).
2. Acquire the lock, swap the reference, release.
3. Close the *old* bundle after a short drain delay so any in-flight request
   that already captured it can finish.

Readers call :meth:`get_pipeline` which takes the reference under the lock and
returns it; because the bundle is immutable once published, the request can
use it safely even if a reload swaps in a newer one mid-request.
"""

from __future__ import annotations

import asyncio

from chat_legal_vn.application.factory import (
    LiveAdapters,
    build_adapters,
    build_query_pipeline,
)
from chat_legal_vn.application.query_pipeline import QueryPipeline
from chat_legal_vn.infra.config import Settings

from ..core.logging import get_logger

log = get_logger(__name__)


class _Bundle:
    __slots__ = ("adapters", "pipeline", "revision")

    def __init__(self, adapters: LiveAdapters, pipeline: QueryPipeline, revision: int):
        self.adapters = adapters
        self.pipeline = pipeline
        self.revision = revision


class AdapterRegistry:
    """Owns the current live adapter bundle and supports atomic reload."""

    def __init__(self, *, drain_seconds: float = 2.0) -> None:
        self._lock = asyncio.Lock()
        self._bundle: _Bundle | None = None
        self._drain_seconds = drain_seconds

    async def initialize(self, settings: Settings, *, revision: int = 0) -> None:
        adapters = build_adapters(settings)
        pipeline = build_query_pipeline(adapters)
        self._bundle = _Bundle(adapters, pipeline, revision)
        log.info("adapter_registry.initialized", revision=revision)

    async def get_pipeline(self) -> QueryPipeline:
        async with self._lock:
            if self._bundle is None:
                msg = "AdapterRegistry not initialized"
                raise RuntimeError(msg)
            return self._bundle.pipeline

    async def current_revision(self) -> int:
        async with self._lock:
            return self._bundle.revision if self._bundle else -1

    async def reload(self, settings: Settings, *, revision: int) -> None:
        """Build a new bundle and swap it in atomically, draining the old one."""
        new_adapters = build_adapters(settings)
        new_pipeline = build_query_pipeline(new_adapters)
        new_bundle = _Bundle(new_adapters, new_pipeline, revision)

        async with self._lock:
            old = self._bundle
            self._bundle = new_bundle
        log.info("adapter_registry.reloaded", revision=revision)

        if old is not None:
            # Close the old bundle after a short drain so in-flight requests
            # that already hold the old pipeline can complete.
            asyncio.create_task(self._drain_close(old))  # noqa: RUF006

    async def _drain_close(self, bundle: _Bundle) -> None:
        await asyncio.sleep(self._drain_seconds)
        try:
            await bundle.adapters.aclose()
            log.info("adapter_registry.old_bundle_closed", revision=bundle.revision)
        except Exception as exc:
            log.warning("adapter_registry.close_failed", error=str(exc))

    async def aclose(self) -> None:
        async with self._lock:
            bundle = self._bundle
            self._bundle = None
        if bundle is not None:
            await bundle.adapters.aclose()
