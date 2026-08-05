"""Offline indexing pipeline.

Walks the parquet corpus → normalizes metadata → parses HTML →
parent/child chunking → batch-embeds children → writes to Qdrant
(vector store), Elasticsearch (lexical store), and the parent doc store.

Resume / Skip semantics
=======================
The pipeline is **idempotent at the chunk level** because every
`chunk_id`/`parent_chunk_id` is a deterministic UUID5 derived from
``(version, document_id, dieu, khoan, diem, split_index)`` — re-ingesting
the same corpus simply overwrites existing points. That makes Ctrl-C and
restart safe, but it still wastes embedding compute on documents that
were already indexed in a prior run.

When ``skip_existing=True`` (the default), we run a one-shot Qdrant
scroll at startup to materialise the set of ``document_id`` values
already indexed. Documents whose ID is in that set are streamed past
without parsing or embedding. With a corpus of 150k docs this set fits in
a few MB and the scroll completes in seconds, so the cost is negligible
compared to the savings (one round-trip total instead of one per doc).

Progress UI
===========
A Rich ``Progress`` instance reports:
  - documents seen / total (from parquet metadata)
  - documents skipped (already indexed)
  - children pushed / failures
  - throughput, ETA
  - ASCII bar that survives ``tee`` to a log file

All adapters' calls inside ``flush()`` use ``asyncio.gather(...,
return_exceptions=True)`` so a transient store failure is logged with the
real exception type instead of silently truncating the message.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable
from dataclasses import dataclass, field
from typing import Any, Protocol

from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)

from ..adapters.docstore.qdrant_doc_store import QdrantParentDocStore
from ..domain.entities import ChildChunk, ParentChunk
from ..domain.ports import (
    ChunkerAdapter,
    DocStoreAdapter,
    EmbeddingAdapter,
    LexicalStoreAdapter,
    VectorStoreAdapter,
)
from ..infra.config import Settings
from ..infra.logging import get_logger
from ..services.chunker import HierarchicalChunker
from ..services.corpus import count_content_documents, iter_documents
from ..services.html_parser import parse_html

log = get_logger(__name__)


class _SupportsExistingIds(Protocol):
    async def existing_document_ids(self) -> set[int]: ...


@dataclass(slots=True)
class IngestDeps:
    settings: Settings
    embedding: EmbeddingAdapter
    vector_store: VectorStoreAdapter
    lexical_store: LexicalStoreAdapter
    doc_store: DocStoreAdapter
    chunker: ChunkerAdapter | None = None  # default: HierarchicalChunker


@dataclass(slots=True)
class IngestStats:
    documents_seen: int = 0
    documents_processed: int = 0
    documents_skipped_existing: int = 0
    documents_skipped_empty: int = 0
    parents_indexed: int = 0
    children_indexed: int = 0
    failures: int = 0
    failed_documents: list[int] = field(default_factory=list)
    elapsed_seconds: float = 0.0


def _format_exc(exc: BaseException) -> str:
    """Render an exception so the log line is informative even when the
    exception's ``str()`` is empty (qdrant ``ResponseHandlingException``,
    some elasticsearch ``TransportError``s).

    Walks the ``__cause__`` / ``__context__`` chain and pulls a few
    well-known attributes from qdrant exceptions so root-cause network
    failures (timeout, 5xx, payload too large) make it into the log.
    """

    def describe(e: BaseException) -> str:
        parts: list[str] = [type(e).__name__]
        msg = str(e).strip()
        if msg:
            parts.append(msg)
        # qdrant_client.http.exceptions.ResponseHandlingException
        for attr in ("source", "status_code", "content"):
            v = getattr(e, attr, None)
            if v is not None and v != "":
                # Avoid recursing forever for `source` which is itself an exc
                if isinstance(v, BaseException):
                    parts.append(f"source={type(v).__name__}: {v!s}".strip().rstrip(":"))
                else:
                    parts.append(f"{attr}={v!s}"[:300])
        return ": ".join(parts).rstrip(":")

    out = [describe(exc)]
    cause = exc.__cause__ or exc.__context__
    seen: set[int] = {id(exc)}
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        out.append("→ " + describe(cause))
        cause = cause.__cause__ or cause.__context__
    return " ".join(out)


# Substrings that indicate the host disk is full at one of the stores. Once
# we see any of these in a flush we abort the ingest immediately — the
# situation cannot resolve itself and looping wastes embedding-API budget.
_DISK_FULL_HINTS: tuple[str, ...] = (
    "No space left on device",
    "WAL buffer size exceeds available disk space",
    "cluster_block_exception",
    "FORBIDDEN/12/index read-only",
    "TOO_MANY_REQUESTS/12/disk usage exceeded",
    "high disk watermark",
    "flood stage disk watermark",
)


def _is_disk_full(message: str) -> bool:
    return any(hint in message for hint in _DISK_FULL_HINTS)


class FatalDiskFullError(RuntimeError):
    """Raised to halt the ingest pipeline when a backing store reports
    that disk is full. Recovery is operator-side (free space, restart),
    so we surface the situation immediately."""


class IngestPipeline:
    def __init__(self, deps: IngestDeps) -> None:
        self._deps = deps
        self._settings = deps.settings
        self._chunker = deps.chunker or HierarchicalChunker(
            chunking_version=deps.settings.chunking_version,
            embedding_version=deps.settings.embedding_version,
            max_input_tokens=deps.settings.embedding_max_input_tokens,
        )

    async def setup(self, *, recreate: bool = False) -> None:
        if recreate:
            log.warning("ingest.recreate_requested", note="dropping existing index data")
            recreate_vec = getattr(self._deps.vector_store, "recreate_collection", None)
            if recreate_vec is not None:
                await recreate_vec(dim=self._deps.embedding.dim)
            else:
                await self._deps.vector_store.ensure_collection(dim=self._deps.embedding.dim)
            recreate_lex = getattr(self._deps.lexical_store, "recreate_index", None)
            if recreate_lex is not None:
                await recreate_lex()
            else:
                await self._deps.lexical_store.ensure_index()
            if isinstance(self._deps.doc_store, QdrantParentDocStore):
                await self._deps.doc_store.recreate_collection()
            return

        await self._deps.vector_store.ensure_collection(dim=self._deps.embedding.dim)
        await self._deps.lexical_store.ensure_index()
        if isinstance(self._deps.doc_store, QdrantParentDocStore):
            await self._deps.doc_store.ensure_collection()
        # Pause Elasticsearch periodic refresh for the duration of the
        # ingest. Search queries on the partial index would still work
        # against existing segments, but newly bulk-indexed documents
        # become searchable only after the run finishes (or after the
        # caller manually calls ``adapters.lexical_store.enable_refresh``).
        if self._settings.elasticsearch_disable_refresh_during_ingest:
            disable = getattr(self._deps.lexical_store, "disable_refresh", None)
            if callable(disable):
                try:
                    await disable()
                except Exception as exc:
                    log.warning(
                        "ingest.disable_refresh_failed", error=_format_exc(exc)
                    )

    async def teardown(self) -> None:
        # Re-enable refresh + force a refresh so search queries see the
        # newly-indexed documents.
        if self._settings.elasticsearch_disable_refresh_during_ingest:
            enable = getattr(self._deps.lexical_store, "enable_refresh", None)
            if callable(enable):
                try:
                    await enable()
                except Exception as exc:
                    log.warning(
                        "ingest.enable_refresh_failed", error=_format_exc(exc)
                    )

    async def _load_existing_ids(self) -> set[int]:
        store = self._deps.vector_store
        fn: Any = getattr(store, "existing_document_ids", None)
        if fn is None:
            return set()
        coro = fn()
        if not isinstance(coro, Awaitable):
            return set()
        try:
            ids = await coro
        except Exception as exc:
            log.warning("ingest.existing_ids_unavailable", error=_format_exc(exc))
            return set()
        if not isinstance(ids, set):
            return set(ids or [])
        return ids

    async def run(
        self,
        *,
        limit: int | None = None,
        flush_every: int = 16,
        skip_existing: bool = True,
        show_progress: bool = True,
        console: Console | None = None,
        log_progress_every_seconds: float = 5.0,
        recreate: bool = False,
    ) -> IngestStats:
        stats = IngestStats()
        start = time.perf_counter()

        await self.setup(recreate=recreate)

        # A fresh index has nothing to skip against; also avoids a wasted
        # scroll over an empty collection.
        existing_ids: set[int] = (
            await self._load_existing_ids()
            if (skip_existing and not recreate)
            else set()
        )
        if skip_existing and not recreate:
            log.info(
                "ingest.skip_existing_loaded",
                already_indexed_documents=len(existing_ids),
            )

        # Total to advance the progress bar.
        # - With ``--limit N`` the goal is N **fresh** documents (matching
        #   stats.documents_processed). Skipped/empty rows must NOT count
        #   against the limit, otherwise a corpus dominated by already-
        #   indexed docs would never reach 100%.
        # - Without a limit we use content.parquet row count: every row in
        #   the parquet *is* a candidate seen, so progress against
        #   stats.documents_seen is meaningful.
        try:
            total_corpus = count_content_documents(self._settings.data_dir)
        except Exception as exc:
            log.warning("ingest.count_unavailable", error=_format_exc(exc))
            total_corpus = 0
        if limit and limit > 0:
            progress_total = limit
            progress_metric = "processed"
        else:
            progress_total = total_corpus or None
            progress_metric = "seen"

        parent_buffer: list[ParentChunk] = []
        child_buffer: list[ChildChunk] = []
        pending_doc_ids: list[int] = []

        progress_console = console or Console(stderr=True)
        # Render the live bar only when the target stream is a real TTY.
        # When the user pipes through ``tee`` / ``> file`` the live updates
        # would all collapse into a single overwritten line; instead we
        # fall back to periodic structured ``ingest.progress`` log lines
        # which are greppable and survive ``tee``.
        live_bar_enabled = bool(
            show_progress and getattr(progress_console, "is_terminal", False)
        )

        progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold]ingest[/bold]"),
            BarColumn(bar_width=None),
            MofNCompleteColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            TextColumn("eta"),
            TimeRemainingColumn(),
            TextColumn(
                "skipped={task.fields[skipped]} new={task.fields[processed]} "
                "children={task.fields[children]} fails={task.fields[fails]} "
                "rate={task.fields[rate]:.1f}/s"
            ),
            console=progress_console,
            transient=False,
            disable=not live_bar_enabled,
            refresh_per_second=4,
        )
        task_id = progress.add_task(
            "ingest",
            total=progress_total,
            skipped=0,
            processed=0,
            children=0,
            fails=0,
            rate=0.0,
        )
        # Tracks the last time we emitted a structured progress event so
        # users tailing a non-TTY log get a heartbeat without flooding it.
        last_log_progress = start
        log_progress_every_seconds = max(0.5, float(log_progress_every_seconds))

        def emit_progress_log(force: bool = False) -> None:
            nonlocal last_log_progress
            now = time.perf_counter()
            if not force and (now - last_log_progress) < log_progress_every_seconds:
                return
            last_log_progress = now
            elapsed = max(now - start, 1e-3)
            current = (
                stats.documents_processed
                if progress_metric == "processed"
                else stats.documents_seen
            )
            rate = stats.documents_seen / elapsed
            remaining = (
                (progress_total - current)
                if (progress_total is not None and progress_total > current)
                else None
            )
            eta_seconds = (rate > 0 and remaining and remaining / rate) or None
            log.info(
                "ingest.progress",
                seen=stats.documents_seen,
                processed=stats.documents_processed,
                total=progress_total,
                percent=(
                    round(100.0 * current / progress_total, 2)
                    if progress_total
                    else None
                ),
                skipped_existing=stats.documents_skipped_existing,
                skipped_empty=stats.documents_skipped_empty,
                children=stats.children_indexed,
                failures=stats.failures,
                rate_docs_per_s=round(rate, 2),
                elapsed_seconds=round(elapsed, 1),
                eta_seconds=(round(eta_seconds, 1) if eta_seconds else None),
            )

        # Bounded set of in-flight flush tasks. We submit a new flush as
        # soon as the buffer hits ``flush_every`` documents, but block via
        # ``flush_semaphore`` if too many are already in flight, so the
        # parser doesn't run unbounded ahead of the network. ``flush_done``
        # collects task results so we can surface their stats from the
        # foreground loop without losing exceptions.
        flush_semaphore = asyncio.Semaphore(
            max(1, self._settings.ingest_flush_queue_size)
        )
        flush_concurrency = max(1, self._settings.ingest_flush_concurrency)
        active_flushes: set[asyncio.Task[None]] = set()

        def schedule_flush(
            parents: list[ParentChunk],
            children: list[ChildChunk],
            doc_ids: list[int],
        ) -> None:
            # Concurrency cap: when we already have N tasks in flight we
            # await one to free a slot before starting the next. This
            # keeps RAM bounded while still overlapping
            # embed → upsert → parse stages of consecutive batches.
            if not children:
                return

            async def runner() -> None:
                async with flush_semaphore:
                    await _do_flush(parents, children, doc_ids)

            task = asyncio.create_task(runner())
            active_flushes.add(task)
            task.add_done_callback(active_flushes.discard)

        async def drain_flushes(*, max_inflight: int = 0) -> None:
            """Wait until ``len(active_flushes) <= max_inflight``."""
            while len(active_flushes) > max_inflight:
                done, _ = await asyncio.wait(
                    active_flushes, return_when=asyncio.FIRST_COMPLETED
                )
                for t in done:
                    if exc := t.exception():
                        # The flush is already responsible for stat-keeping
                        # via _do_flush, but surface unexpected programmer
                        # errors so they aren't silently swallowed.
                        log.error(
                            "ingest.flush_task_unhandled",
                            error=_format_exc(exc),
                        )

        async def _do_flush(
            parents: list[ParentChunk],
            children: list[ChildChunk],
            doc_ids: list[int],
        ) -> None:
            if not children:
                return
            try:
                vectors = await self._deps.embedding.embed_documents(
                    [c.text_for_embedding for c in children]
                )
            except Exception as exc:
                stats.failures += len(doc_ids)
                stats.failed_documents.extend(doc_ids)
                log.error(
                    "ingest.embed_failed",
                    error=_format_exc(exc),
                    docs_in_batch=len(doc_ids),
                    children=len(children),
                )
                return

            try:
                results = await asyncio.gather(
                    self._deps.vector_store.upsert(
                        chunks=children, vectors=vectors
                    ),
                    self._deps.lexical_store.upsert(children),
                    self._deps.doc_store.upsert(parents),
                    return_exceptions=True,
                )
            except Exception as exc:
                stats.failures += len(doc_ids)
                stats.failed_documents.extend(doc_ids)
                log.error(
                    "ingest.flush_failed",
                    error=_format_exc(exc),
                    docs_in_batch=len(doc_ids),
                )
                return

            partial_failures = [
                (label, r)
                for label, r in zip(
                    ("vector_store", "lexical_store", "doc_store"),
                    results,
                    strict=True,
                )
                if isinstance(r, BaseException)
            ]
            if partial_failures:
                stats.failures += len(doc_ids)
                stats.failed_documents.extend(doc_ids)
                rendered = {lbl: _format_exc(exc) for lbl, exc in partial_failures}
                log.error(
                    "ingest.flush_partial_failure",
                    failures=rendered,
                    docs_in_batch=len(doc_ids),
                )
                # Fail-fast on disk-full at any backing store. Continuing
                # would just burn embedding-API budget on upserts that will
                # never land.
                disk_full_stores = [
                    lbl for lbl, msg in rendered.items() if _is_disk_full(msg)
                ]
                if disk_full_stores:
                    raise FatalDiskFullError(
                        "Disk full reported by: "
                        + ", ".join(disk_full_stores)
                        + ". Free space and re-run `clvn ingest` "
                        "(skip-existing will resume)."
                    )
            else:
                stats.parents_indexed += len(parents)
                stats.children_indexed += len(children)
                log.info(
                    "ingest.flush",
                    parents=len(parents),
                    children=len(children),
                    parents_total=stats.parents_indexed,
                    children_total=stats.children_indexed,
                )

        async def flush() -> None:
            nonlocal parent_buffer, child_buffer, pending_doc_ids
            if not child_buffer:
                return
            # Snapshot buffers and detach so the producer can keep filling
            # the next batch while this one is in flight.
            local_parents = parent_buffer
            local_children = child_buffer
            local_doc_ids = pending_doc_ids
            parent_buffer = []
            child_buffer = []
            pending_doc_ids = []
            schedule_flush(local_parents, local_children, local_doc_ids)
            # Bound the in-flight pool so memory stays predictable.
            await drain_flushes(max_inflight=flush_concurrency - 1)

        try:
            for document, html in iter_documents(
                data_dir=self._settings.data_dir,
                limit=None,  # we honor `limit` ourselves so skipped docs don't count against it
                require_html=True,
            ):
                stats.documents_seen += 1

                # Skip-by-document-id (idempotent resume).
                if existing_ids and document.document_id in existing_ids:
                    stats.documents_skipped_existing += 1
                    progress.update(
                        task_id,
                        skipped=stats.documents_skipped_existing,
                    )
                    if progress_metric == "seen":
                        progress.advance(task_id, 1)
                    continue

                if html is None:
                    stats.documents_skipped_empty += 1
                    if progress_metric == "seen":
                        progress.advance(task_id, 1)
                    continue

                try:
                    root = parse_html(
                        document_id=document.document_id,
                        document_title=document.title,
                        html=html,
                    )
                    parents, children = self._chunker.chunk(
                        document=document, root=root
                    )
                    if not children:
                        stats.documents_skipped_empty += 1
                        if progress_metric == "seen":
                            progress.advance(task_id, 1)
                        continue
                    parent_buffer.extend(parents)
                    child_buffer.extend(children)
                    pending_doc_ids.append(document.document_id)
                    stats.documents_processed += 1
                    progress.advance(task_id, 1)
                except Exception as exc:
                    stats.failures += 1
                    stats.failed_documents.append(document.document_id)
                    log.error(
                        "ingest.parse_failed",
                        document_id=document.document_id,
                        error=_format_exc(exc),
                    )
                    if progress_metric == "seen":
                        progress.advance(task_id, 1)

                # Update progress fields each time we touch a doc.
                elapsed = max(time.perf_counter() - start, 1e-3)
                progress.update(
                    task_id,
                    processed=stats.documents_processed,
                    children=stats.children_indexed,
                    fails=stats.failures,
                    rate=stats.documents_seen / elapsed,
                )

                # Heartbeat for non-TTY consumers (tee / file / journald).
                if not live_bar_enabled and show_progress:
                    emit_progress_log()

                if (
                    stats.documents_processed
                    and stats.documents_processed % flush_every == 0
                ):
                    await flush()
                    # After a flush there is fresh state worth surfacing
                    # immediately even on TTY users will appreciate the
                    # lower-cadence baseline check.
                    emit_progress_log(force=False)

                if limit is not None and stats.documents_processed >= limit:
                    break

            await flush()
            # Wait for any remaining flush tasks to complete before
            # claiming the run is done.
            await drain_flushes(max_inflight=0)
            emit_progress_log(force=True)
        finally:
            progress.update(
                task_id,
                processed=stats.documents_processed,
                children=stats.children_indexed,
                fails=stats.failures,
                skipped=stats.documents_skipped_existing,
            )
            progress.stop()
            await self.teardown()

        stats.elapsed_seconds = round(time.perf_counter() - start, 2)
        log.info(
            "ingest.done",
            documents_seen=stats.documents_seen,
            documents_processed=stats.documents_processed,
            documents_skipped_existing=stats.documents_skipped_existing,
            documents_skipped_empty=stats.documents_skipped_empty,
            parents_indexed=stats.parents_indexed,
            children_indexed=stats.children_indexed,
            failures=stats.failures,
            elapsed_seconds=stats.elapsed_seconds,
        )
        return stats
