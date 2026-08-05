"""Parent-chunk document store backed by Qdrant payload-only collection.

We use a separate Qdrant collection that holds 1-dim placeholder vectors so
we can leverage Qdrant's payload retrieval API. This avoids introducing yet
another database for the parent lookup path.
"""

from __future__ import annotations

import contextlib
from collections.abc import Sequence

from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qm
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ...domain.entities import ChunkMetadata, EffectiveStatus, ParentChunk
from ...domain.ports import DocStoreAdapter
from ...infra.config import Settings
from ..vectorstores.qdrant_store import (
    _RETRYABLE_QDRANT_EXCEPTIONS,
    _qdrant_client_kwargs,
)


class QdrantParentDocStore(DocStoreAdapter):
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._collection = f"{settings.collection_name}__parents"
        api_key = settings.qdrant_api_key.get_secret_value() or None
        self._client = AsyncQdrantClient(
            **_qdrant_client_kwargs(settings, api_key=api_key),
        )

    async def ensure_collection(self) -> None:
        if await self._client.collection_exists(self._collection):
            return
        await self._client.create_collection(
            collection_name=self._collection,
            vectors_config=qm.VectorParams(size=1, distance=qm.Distance.DOT),
        )

    async def recreate_collection(self) -> None:
        """Drop and recreate the parent-doc collection for a clean re-ingest."""
        with contextlib.suppress(Exception):
            await self._client.delete_collection(self._collection)
        await self._client.create_collection(
            collection_name=self._collection,
            vectors_config=qm.VectorParams(size=1, distance=qm.Distance.DOT),
        )

    async def upsert(self, parents: Sequence[ParentChunk]) -> None:
        if not parents:
            return
        bs = max(1, self._settings.qdrant_upsert_batch_size)
        max_retries = max(1, self._settings.qdrant_upsert_max_retries)
        for offset in range(0, len(parents), bs):
            sub = parents[offset : offset + bs]
            points = [
                qm.PointStruct(
                    id=p.parent_chunk_id,
                    vector=[0.0],
                    payload={
                        "parent_chunk_id": p.parent_chunk_id,
                        "document_id": p.document_id,
                        "hierarchical_path": p.hierarchical_path,
                        "heading": p.heading,
                        "body": p.body,
                        "summary": p.summary,
                        "metadata": p.metadata.model_dump(mode="json"),
                    },
                )
                for p in sub
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

    async def get(self, parent_chunk_id: str) -> ParentChunk | None:
        ps = await self.get_many([parent_chunk_id])
        return ps[0] if ps else None

    async def get_many(self, parent_chunk_ids: Sequence[str]) -> list[ParentChunk]:
        if not parent_chunk_ids:
            return []
        points = await self._client.retrieve(
            collection_name=self._collection,
            ids=list(parent_chunk_ids),
            with_payload=True,
            with_vectors=False,
        )
        out: list[ParentChunk] = []
        for p in points:
            payload = p.payload or {}
            md = payload.get("metadata") or {}
            md.setdefault("effective_status", EffectiveStatus.UNKNOWN.value)
            out.append(
                ParentChunk(
                    parent_chunk_id=str(payload.get("parent_chunk_id", p.id)),
                    document_id=int(payload.get("document_id", 0)),
                    hierarchical_path=str(payload.get("hierarchical_path", "")),
                    heading=payload.get("heading"),
                    body=str(payload.get("body", "")),
                    summary=str(payload.get("summary", "")),
                    metadata=ChunkMetadata(**md),
                )
            )
        return out

    async def aclose(self) -> None:
        with contextlib.suppress(Exception):
            await self._client.close()
