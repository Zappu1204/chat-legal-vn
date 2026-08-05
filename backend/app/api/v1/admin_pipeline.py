"""Admin: trigger and monitor offline pipeline runs (ingest / graph-build).

These wrap the existing core pipelines. Each run builds a *fresh* adapter set
from the current effective settings (independent of the query registry) so a
long ingest does not share connection pools with live query traffic, and
closes them when done.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from chat_legal_vn.application.factory import build_adapters, build_ingest_pipeline
from chat_legal_vn.application.graph_pipeline import run_graph_ingest
from fastapi import APIRouter, HTTPException, Request, status

from ...services.config_service import build_effective_settings, get_or_create_config
from ..deps import AdminUser, DbSession

router = APIRouter(prefix="/admin/pipeline", tags=["admin"])


def _job_manager(request: Request):
    return request.app.state.jobs


@router.post("/ingest", status_code=status.HTTP_202_ACCEPTED)
async def trigger_ingest(
    request: Request,
    admin: AdminUser,
    session: DbSession,
    limit: int = 0,
    flush_every: int = 16,
    skip_existing: bool = True,
) -> dict[str, Any]:
    config = await get_or_create_config(session)
    settings = build_effective_settings(config)

    async def _run() -> dict[str, Any]:
        adapters = build_adapters(settings)
        try:
            pipeline = build_ingest_pipeline(adapters)
            stats = await pipeline.run(
                limit=limit if limit > 0 else None,
                flush_every=flush_every,
                skip_existing=skip_existing,
                show_progress=False,
            )
            return asdict(stats)
        finally:
            await adapters.aclose()

    try:
        job = await _job_manager(request).start("ingest", _run)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return job.to_dict()


@router.post("/graph-build", status_code=status.HTTP_202_ACCEPTED)
async def trigger_graph_build(
    request: Request, admin: AdminUser, session: DbSession
) -> dict[str, Any]:
    config = await get_or_create_config(session)
    settings = build_effective_settings(config)

    async def _run() -> dict[str, Any]:
        adapters = build_adapters(settings)
        try:
            stats = await run_graph_ingest(
                settings=adapters.settings, graph=adapters.graph_store
            )
            return asdict(stats)
        finally:
            await adapters.aclose()

    try:
        job = await _job_manager(request).start("graph-build", _run)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return job.to_dict()


@router.get("/jobs")
async def list_jobs(request: Request, admin: AdminUser) -> list[dict[str, Any]]:
    return [j.to_dict() for j in _job_manager(request).list()]


@router.get("/jobs/{job_id}")
async def get_job(request: Request, job_id: str, admin: AdminUser) -> dict[str, Any]:
    job = _job_manager(request).get(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy job.")
    return job.to_dict()
