"""Qdrant vector-store adapter."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Sequence
from datetime import date
from typing import Any

import httpx
from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qm
from qdrant_client.http.exceptions import (
    ResponseHandlingException,
    UnexpectedResponse,
)
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ...domain.entities import ChildChunk, EffectiveStatus, QueryFilter, RetrievalCandidate
from ...domain.ports import VectorStoreAdapter
from ...infra.config import Settings
from ...infra.logging import get_logger

log = get_logger(__name__)


_RETRYABLE_QDRANT_EXCEPTIONS: tuple[type[Exception], ...] = (
    ResponseHandlingException,
    UnexpectedResponse,
    httpx.HTTPError,
    ConnectionError,
    TimeoutError,
)


def _qdrant_client_kwargs(settings: Settings, *, api_key: str | None) -> dict[str, Any]:
    """Build kwargs for `AsyncQdrantClient` from settings.

    Accepts both `QDRANT_HOST=hostname` and `QDRANT_HOST=http(s)://hostname[:port]`.
    The qdrant-client SDK refuses a `host` value that contains a protocol, so
    when one is detected we route through the `url` parameter instead.
    """
    raw_host = (settings.qdrant_host or "").strip()
    kwargs: dict[str, Any] = {
        "api_key": api_key,
        "prefer_grpc": settings.qdrant_prefer_grpc,
        "timeout": 120,
    }
    if raw_host.startswith(("http://", "https://")):
        # Use `url` form. Append the port if the URL doesn't already have one.
        url = raw_host.rstrip("/")
        # naive port-presence check on the netloc
        netloc = url.split("://", 1)[1].split("/", 1)[0]
        if ":" not in netloc and settings.qdrant_port:
            url = f"{url}:{settings.qdrant_port}"
        kwargs["url"] = url
    else:
        kwargs["host"] = raw_host or "localhost"
        kwargs["port"] = settings.qdrant_port
        kwargs["grpc_port"] = settings.qdrant_grpc_port
        kwargs["https"] = settings.qdrant_use_https
    return kwargs


def _payload_filter(qf: QueryFilter | None) -> qm.Filter | None:
    if qf is None:
        return None
    must: list[qm.FieldCondition] = []
    if qf.so_ky_hieu:
        must.append(
            qm.FieldCondition(key="so_ky_hieu", match=qm.MatchValue(value=qf.so_ky_hieu))
        )
    if qf.loai_van_ban:
        must.append(
            qm.FieldCondition(key="loai_van_ban", match=qm.MatchAny(any=qf.loai_van_ban))
        )
    if qf.co_quan_ban_hanh:
        must.append(
            qm.FieldCondition(
                key="co_quan_ban_hanh", match=qm.MatchAny(any=qf.co_quan_ban_hanh)
            )
        )
    # Date range: payload `ngay_ban_hanh` is stored as ISO-8601 strings.
    # qdrant-client's `Range` only accepts numeric bounds — date filtering
    # requires `DatetimeRange` together with a payload index of type
    # ``DATETIME``. Anything else silently degrades to a noop or, with
    # pydantic v2, raises a ``ValidationError`` at request build time.
    if qf.year_min is not None or qf.year_max is not None:
        gte = date(qf.year_min, 1, 1) if qf.year_min is not None else None
        lte = date(qf.year_max, 12, 31) if qf.year_max is not None else None
        must.append(
            qm.FieldCondition(
                key="ngay_ban_hanh",
                range=qm.DatetimeRange(gte=gte, lte=lte),
            )
        )
    if qf.effective_only:
        must.append(
            qm.FieldCondition(
                key="effective_status",
                match=qm.MatchValue(value=EffectiveStatus.EFFECTIVE.value),
            )
        )
    # Structural anchors. We split the citation expansions into ``should``
    # (any of the substrings counts) so multi-clause queries like "Điểm h,
    # i" still match when each Điểm sits in its own chunk.
    should: list[qm.FieldCondition] = []
    if qf.path_substrings:
        for substring in qf.path_substrings:
            should.append(
                qm.FieldCondition(
                    key="hierarchical_path",
                    match=qm.MatchText(text=substring),
                )
            )

    if not must and not should:
        return None
    return qm.Filter(must=must or None, should=should or None)


def _chunk_payload(chunk: ChildChunk) -> dict[str, Any]:
    m = chunk.metadata
    return {
        "chunk_id": chunk.chunk_id,
        "parent_chunk_id": chunk.parent_chunk_id,
        "document_id": m.document_id,
        "so_ky_hieu": m.so_ky_hieu,
        "loai_van_ban": m.loai_van_ban,
        "co_quan_ban_hanh": m.co_quan_ban_hanh,
        "nganh": m.nganh,
        "linh_vuc": m.linh_vuc,
        "ngay_ban_hanh": m.ngay_ban_hanh,
        "ngay_co_hieu_luc": m.ngay_co_hieu_luc,
        "ngay_het_hieu_luc": m.ngay_het_hieu_luc,
        "effective_status": m.effective_status.value,
        "hierarchical_path": m.hierarchical_path,
        "chunking_version": m.chunking_version,
        "embedding_version": m.embedding_version,
        "body": chunk.body,
        "text": chunk.text_for_embedding,
    }


def _candidate_from_point(point: Any) -> RetrievalCandidate:
    payload = point.payload or {}
    return RetrievalCandidate(
        chunk_id=str(payload.get("chunk_id", point.id)),
        parent_chunk_id=str(payload.get("parent_chunk_id", "")),
        document_id=int(payload.get("document_id", 0)),
        hierarchical_path=str(payload.get("hierarchical_path", "")),
        body=str(payload.get("body", "")),
        score=float(getattr(point, "score", 0.0) or 0.0),
        source="dense",
        metadata=payload,
    )


class QdrantVectorStore(VectorStoreAdapter):
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        api_key = settings.qdrant_api_key.get_secret_value() or None
        self._client = AsyncQdrantClient(
            **_qdrant_client_kwargs(settings, api_key=api_key),
        )
        self._collection = settings.collection_name

    # Field → schema mapping for payload indexes. Keep in sync with the
    # filter logic in ``_payload_filter`` — adding a new filter dimension
    # without an index makes Qdrant fall back to a full scan.
    _PAYLOAD_INDEX_SCHEMA: dict[str, Any] = {
        "so_ky_hieu": qm.PayloadSchemaType.KEYWORD,
        "loai_van_ban": qm.PayloadSchemaType.KEYWORD,
        "co_quan_ban_hanh": qm.PayloadSchemaType.KEYWORD,
        "effective_status": qm.PayloadSchemaType.KEYWORD,
        "parent_chunk_id": qm.PayloadSchemaType.KEYWORD,
        "document_id": qm.PayloadSchemaType.INTEGER,
        # ISO-8601 string payload — must be DATETIME to support
        # `qm.DatetimeRange` filters.
        "ngay_ban_hanh": qm.PayloadSchemaType.DATETIME,
        "ngay_co_hieu_luc": qm.PayloadSchemaType.DATETIME,
        "ngay_het_hieu_luc": qm.PayloadSchemaType.DATETIME,
        # Full-text index over the hierarchical path so a user citation
        # such as "Điều 7 > Khoản 2 > Điểm h" can be matched directly via
        # ``MatchText`` (with phrase matching) without falling back to a
        # full scan.
        "hierarchical_path": qm.TextIndexParams(
            type=qm.TextIndexType.TEXT,
            tokenizer=qm.TokenizerType.WHITESPACE,
            lowercase=True,
            phrase_matching=True,
        ),
    }

    @staticmethod
    def _wanted_data_type(schema: Any) -> str:
        """Render a schema definition to the lowercase data_type string
        Qdrant exposes via ``payload_schema``."""
        if isinstance(schema, qm.PayloadSchemaType):
            return str(schema.value).lower()
        # qm.TextIndexParams / KeywordIndexParams / IntegerIndexParams …
        type_attr = getattr(schema, "type", None)
        if type_attr is not None:
            return str(getattr(type_attr, "value", type_attr)).lower()
        return str(schema).lower()

    async def _ensure_payload_indexes(self) -> None:
        """Idempotent — creates any missing payload indexes.

        Qdrant rejects duplicate index creations with a recognisable
        error; we swallow those and only escalate genuine failures so
        a redeploy that adds new index fields can self-heal.

        Also reconciles schema drift: if an existing index uses a
        different ``PayloadSchemaType`` than the one we want (e.g. an
        older deployment indexed ``ngay_ban_hanh`` as KEYWORD before we
        switched to DATETIME) we drop and recreate it.
        """
        # Inspect current state once.
        try:
            info = await self._client.get_collection(self._collection)
            current_schema: dict[str, Any] = {}
            payload_schema = getattr(info, "payload_schema", None) or {}
            for fname, finfo in payload_schema.items():
                # ``finfo`` is a ``PayloadIndexInfo`` whose ``data_type``
                # exposes the active schema as an enum/string.
                data_type = getattr(finfo, "data_type", None)
                if data_type is not None:
                    current_schema[fname] = str(data_type).lower()
        except Exception as exc:  # noqa: BLE001
            log.debug("qdrant.payload_schema_unavailable", error=str(exc))
            current_schema = {}

        for field, schema in self._PAYLOAD_INDEX_SCHEMA.items():
            wanted = self._wanted_data_type(schema)
            existing_type = current_schema.get(field)
            if existing_type and existing_type != wanted:
                log.warning(
                    "qdrant.payload_index_recreating",
                    field=field,
                    from_=existing_type,
                    to=wanted,
                )
                with contextlib.suppress(Exception):
                    await self._client.delete_payload_index(
                        collection_name=self._collection,
                        field_name=field,
                    )
                existing_type = None

            if existing_type == wanted:
                continue
            try:
                await self._client.create_payload_index(
                    collection_name=self._collection,
                    field_name=field,
                    field_schema=schema,
                )
            except Exception as exc:  # noqa: BLE001
                msg = str(exc).lower()
                if "already" in msg or "exists" in msg:
                    continue
                log.debug(
                    "qdrant.index_skip",
                    field=field,
                    schema=str(schema),
                    error=str(exc),
                )

    async def ensure_collection(self, *, dim: int) -> None:
        existing = await self._client.collection_exists(self._collection)
        if not existing:
            await self._client.create_collection(
                collection_name=self._collection,
                vectors_config=qm.VectorParams(size=dim, distance=qm.Distance.COSINE),
            )
        # Always run — handles both new collections AND old collections
        # that pre-date a payload-schema change (e.g. swapping
        # ``ngay_ban_hanh`` from KEYWORD to DATETIME).
        await self._ensure_payload_indexes()

    async def upsert(
        self,
        *,
        chunks: Sequence[ChildChunk],
        vectors: Sequence[list[float]],
    ) -> None:
        """Upsert points in sub-batches with retry on transient errors.

        A single ``upsert`` call to Qdrant must fit in the server-side
        request budget (default ~32 MiB). With 4096-dim float32 vectors
        every point is roughly ``4096 × 4 + payload ≈ 18 KiB``, so a batch
        of 2000 already pushes 36 MiB and routinely triggers
        ``BrokenPipeError`` / ``ResponseHandlingException`` on busy hosts.
        We split into small fixed-size sub-batches and retry each
        sub-batch independently with exponential back-off so a single
        flake doesn't lose hundreds of documents' worth of compute.
        """
        if not chunks:
            return
        if len(chunks) != len(vectors):
            msg = "chunks and vectors length mismatch"
            raise ValueError(msg)

        bs = max(1, self._settings.qdrant_upsert_batch_size)
        max_retries = max(1, self._settings.qdrant_upsert_max_retries)

        for offset in range(0, len(chunks), bs):
            sub_chunks = chunks[offset : offset + bs]
            sub_vectors = vectors[offset : offset + bs]
            points = [
                qm.PointStruct(
                    id=chunk.chunk_id,
                    vector=vec,
                    payload=_chunk_payload(chunk),
                )
                for chunk, vec in zip(sub_chunks, sub_vectors, strict=True)
            ]
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(max_retries),
                wait=wait_exponential(multiplier=1, min=1, max=15),
                retry=retry_if_exception_type(_RETRYABLE_QDRANT_EXCEPTIONS),
                reraise=True,
            ):
                with attempt:
                    await self._client.upsert(
                        collection_name=self._collection,
                        points=points,
                        wait=False,
                    )

    async def search(
        self,
        *,
        vector: Sequence[float],
        top_k: int,
        filter: QueryFilter | None = None,
    ) -> list[RetrievalCandidate]:
        # qdrant-client >=1.14 removed `search`; use `query_points` (the
        # canonical Query API). The response wraps the points list in `points`.
        response = await self._client.query_points(
            collection_name=self._collection,
            query=list(vector),
            limit=top_k,
            query_filter=_payload_filter(filter),
            with_payload=True,
        )
        points = getattr(response, "points", response) or []
        return [_candidate_from_point(p) for p in points]

    async def get_by_ids(self, ids: Sequence[str]) -> list[RetrievalCandidate]:
        if not ids:
            return []
        points = await self._client.retrieve(
            collection_name=self._collection,
            ids=list(ids),
            with_payload=True,
            with_vectors=False,
        )
        out: list[RetrievalCandidate] = []
        for p in points:
            payload = p.payload or {}
            out.append(
                RetrievalCandidate(
                    chunk_id=str(payload.get("chunk_id", p.id)),
                    parent_chunk_id=str(payload.get("parent_chunk_id", "")),
                    document_id=int(payload.get("document_id", 0)),
                    hierarchical_path=str(payload.get("hierarchical_path", "")),
                    body=str(payload.get("body", "")),
                    score=0.0,
                    source="lookup",
                    metadata=payload,
                )
            )
        return out

    async def count(self) -> int:
        info = await self._client.count(collection_name=self._collection, exact=False)
        return int(info.count)

    async def existing_document_ids(self) -> set[int]:
        """Return the set of `document_id` payload values currently indexed.

        Implemented as a payload-only scroll (no vectors fetched) so the
        round-trip cost is bounded by the number of points, not their
        dimensionality. For a 150k-doc corpus producing ~1M chunks this
        completes in a few seconds and pulls ~5 MB over the wire.
        """
        ids: set[int] = []  # using list-then-set to avoid hash churn
        ids = []
        offset: Any = None
        try:
            while True:
                points, offset = await self._client.scroll(
                    collection_name=self._collection,
                    limit=4096,
                    with_payload=["document_id"],
                    with_vectors=False,
                    offset=offset,
                )
                for p in points:
                    payload = p.payload or {}
                    raw = payload.get("document_id")
                    if raw is None:
                        continue
                    try:
                        ids.append(int(raw))
                    except (TypeError, ValueError):
                        continue
                if offset is None:
                    break
        except Exception as exc:
            log.warning("qdrant.scroll_failed", error=str(exc))
        return set(ids)

    async def aclose(self) -> None:
        with contextlib.suppress(Exception):
            await self._client.close()
        # Ensure no lingering tasks
        await asyncio.sleep(0)
