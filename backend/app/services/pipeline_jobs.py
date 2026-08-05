"""In-process job tracker for long-running pipeline runs (ingest, graph-build).

Indexing is a long, single-instance operation, so a lightweight in-memory
tracker is the right scope here — there is exactly one backend process that
owns the pipeline. A job is started as an asyncio task; its progress is polled
via ``GET /admin/pipeline/jobs/{id}``. Only one job of a given kind may run at
a time. For a multi-replica deployment this would move to a real task queue
(Celery / Arq / RQ), but that is out of scope for the current single-node
setup and is flagged in the admin UI.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from ..core.logging import get_logger

log = get_logger(__name__)

JobStatus = Literal["running", "succeeded", "failed"]


@dataclass
class Job:
    id: str
    kind: str
    status: JobStatus = "running"
    started_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    finished_at: datetime | None = None
    result: dict[str, Any] | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "result": self.result,
            "error": self.error,
        }


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._running_kinds: set[str] = set()
        self._lock = asyncio.Lock()

    async def start(
        self, kind: str, coro_factory: Callable[[], Awaitable[dict[str, Any]]]
    ) -> Job:
        async with self._lock:
            if kind in self._running_kinds:
                msg = f"A '{kind}' job is already running."
                raise RuntimeError(msg)
            job = Job(id=uuid.uuid4().hex, kind=kind)
            self._jobs[job.id] = job
            self._running_kinds.add(kind)

        async def _run() -> None:
            try:
                result = await coro_factory()
                job.status = "succeeded"
                job.result = result
            except Exception as exc:
                job.status = "failed"
                job.error = str(exc)
                log.error("pipeline_job.failed", kind=kind, error=str(exc))
            finally:
                job.finished_at = datetime.now(tz=UTC)
                async with self._lock:
                    self._running_kinds.discard(kind)

        asyncio.create_task(_run())  # noqa: RUF006
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list(self) -> list[Job]:
        return sorted(self._jobs.values(), key=lambda j: j.started_at, reverse=True)
