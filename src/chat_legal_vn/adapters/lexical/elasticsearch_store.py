"""Elasticsearch BM25 adapter with a Vietnamese-aware analyzer."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from elasticsearch import AsyncElasticsearch
from elasticsearch.helpers import (
    BulkIndexError,
    async_streaming_bulk,
)

from ...domain.entities import ChildChunk, EffectiveStatus, QueryFilter, RetrievalCandidate
from ...domain.ports import LexicalStoreAdapter
from ...infra.config import Settings
from ...infra.logging import get_logger

log = get_logger(__name__)


_VN_ANALYZER = {
    "settings": {
        "index": {"number_of_shards": 1, "number_of_replicas": 0},
        "analysis": {
            "analyzer": {
                "vi_legal_analyzer": {
                    "type": "custom",
                    "tokenizer": "icu_tokenizer",
                    "filter": ["icu_folding", "lowercase"],
                }
            }
        },
    },
    "mappings": {
        "properties": {
            "chunk_id": {"type": "keyword"},
            "parent_chunk_id": {"type": "keyword"},
            "document_id": {"type": "long"},
            "text": {"type": "text", "analyzer": "vi_legal_analyzer"},
            "body": {"type": "text", "analyzer": "vi_legal_analyzer"},
            "hierarchical_path": {"type": "text", "analyzer": "vi_legal_analyzer"},
            "title": {"type": "text", "analyzer": "vi_legal_analyzer"},
            "so_ky_hieu": {"type": "keyword"},
            "loai_van_ban": {"type": "keyword"},
            "co_quan_ban_hanh": {"type": "keyword"},
            "nganh": {"type": "keyword"},
            "linh_vuc": {"type": "keyword"},
            "effective_status": {"type": "keyword"},
            "ngay_ban_hanh": {"type": "date", "format": "yyyy-MM-dd||strict_date_optional_time"},
            "ngay_co_hieu_luc": {"type": "date", "format": "yyyy-MM-dd||strict_date_optional_time"},
            "ngay_het_hieu_luc": {"type": "date", "format": "yyyy-MM-dd||strict_date_optional_time"},
            "chunking_version": {"type": "keyword"},
            "embedding_version": {"type": "keyword"},
        }
    },
}


def _doc(chunk: ChildChunk) -> dict[str, Any]:
    m = chunk.metadata
    return {
        "_op_type": "index",
        "_index": None,  # filled by helper
        "_id": chunk.chunk_id,
        "_source": {
            "chunk_id": chunk.chunk_id,
            "parent_chunk_id": chunk.parent_chunk_id,
            "document_id": m.document_id,
            "text": chunk.text_for_embedding,
            "body": chunk.body,
            "hierarchical_path": m.hierarchical_path,
            "so_ky_hieu": m.so_ky_hieu,
            "loai_van_ban": m.loai_van_ban,
            "co_quan_ban_hanh": m.co_quan_ban_hanh,
            "nganh": m.nganh,
            "linh_vuc": m.linh_vuc,
            "effective_status": m.effective_status.value,
            "ngay_ban_hanh": m.ngay_ban_hanh,
            "ngay_co_hieu_luc": m.ngay_co_hieu_luc,
            "ngay_het_hieu_luc": m.ngay_het_hieu_luc,
            "chunking_version": m.chunking_version,
            "embedding_version": m.embedding_version,
        },
    }


def _build_filters(qf: QueryFilter | None) -> list[dict[str, Any]]:
    if qf is None:
        return []
    filters: list[dict[str, Any]] = []
    if qf.so_ky_hieu:
        filters.append({"term": {"so_ky_hieu": qf.so_ky_hieu}})
    if qf.loai_van_ban:
        filters.append({"terms": {"loai_van_ban": qf.loai_van_ban}})
    if qf.co_quan_ban_hanh:
        filters.append({"terms": {"co_quan_ban_hanh": qf.co_quan_ban_hanh}})
    range_clause: dict[str, str] = {}
    if qf.year_min is not None:
        range_clause["gte"] = f"{qf.year_min}-01-01"
    if qf.year_max is not None:
        range_clause["lte"] = f"{qf.year_max}-12-31"
    if range_clause:
        filters.append({"range": {"ngay_ban_hanh": range_clause}})
    if qf.effective_only:
        filters.append({"term": {"effective_status": EffectiveStatus.EFFECTIVE.value}})
    return filters


class ElasticsearchLexicalStore(LexicalStoreAdapter):
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._index = settings.index_name
        self._client = AsyncElasticsearch(
            hosts=[settings.elasticsearch_host],
            basic_auth=(
                settings.elasticsearch_user,
                settings.elasticsearch_password.get_secret_value(),
            ),
            verify_certs=settings.elasticsearch_verify_certs,
        )

    async def ensure_index(self) -> None:
        if await self._client.indices.exists(index=self._index):
            return
        await self._client.indices.create(index=self._index, body=_VN_ANALYZER)

    async def disable_refresh(self) -> None:
        """Pause periodic refresh during a long bulk-ingest run.

        Elasticsearch defaults to refreshing every second. During a heavy
        bulk-ingest run this triggers many small segment flushes that
        compete with the indexing thread. Setting ``refresh_interval=-1``
        until the run finishes routinely doubles bulk throughput; we
        re-enable refresh with ``enable_refresh()`` afterwards.
        """
        await self._client.indices.put_settings(
            index=self._index, body={"index": {"refresh_interval": "-1"}}
        )

    async def enable_refresh(self) -> None:
        await self._client.indices.put_settings(
            index=self._index, body={"index": {"refresh_interval": "1s"}}
        )
        # Force a refresh once so newly-indexed docs are searchable.
        try:
            await self._client.indices.refresh(index=self._index)
        except Exception:
            pass

    async def upsert(self, chunks: Sequence[ChildChunk]) -> None:
        if not chunks:
            return
        actions = [{**_doc(c), "_index": self._index} for c in chunks]
        ok_count = 0
        per_doc_errors: list[dict[str, Any]] = []
        # ``raise_on_error=False`` + ``raise_on_exception=False`` makes
        # ``async_bulk`` yield ``(ok, info)`` tuples for every action so we
        # can capture per-document failures (mapper_parsing_exception,
        # cluster_block_exception when disk is full, ...) instead of getting
        # an opaque ``BulkIndexError: 256 document(s) failed to index``.
        async for ok, info in async_streaming_bulk(  # type: ignore[no-untyped-call]
            self._client,
            actions,
            chunk_size=self._settings.elasticsearch_bulk_chunk_size,
            request_timeout=120,
            raise_on_error=False,
            raise_on_exception=False,
            max_retries=2,
            initial_backoff=0.5,
            max_backoff=8.0,
        ):
            if ok:
                ok_count += 1
            else:
                per_doc_errors.append(info)
                if len(per_doc_errors) <= 3:
                    log.warning("es.bulk.item_failed", info=info)

        failed = len(per_doc_errors)
        if failed:
            sample = per_doc_errors[:3]
            cluster_block = any(
                "cluster_block_exception" in str(e) or "disk usage exceeded" in str(e)
                or "TOO_MANY_REQUESTS/12/disk usage" in str(e)
                for e in sample
            )
            log.error(
                "es.bulk.failures",
                ok=ok_count,
                failed=failed,
                total=len(actions),
                cluster_block=cluster_block,
                sample=sample,
            )
            # Re-raise so the ingest pipeline marks this flush as failed and
            # — in the disk-full case — can halt fast instead of looping.
            # We embed the root-cause string from the first failed item into
            # the exception message so the upstream circuit breaker (which
            # only sees the exception's str()) can detect ``cluster_block_
            # exception`` / ``disk usage exceeded`` and trip immediately.
            cause_str = ""
            for item in per_doc_errors[:5]:
                # ``async_streaming_bulk`` returns dicts shaped like
                # ``{"index": {"_id": ..., "error": {...}}}``. Walk it
                # defensively.
                if isinstance(item, dict):
                    for op_info in item.values():
                        if isinstance(op_info, dict):
                            err = op_info.get("error") or {}
                            if isinstance(err, dict):
                                cause_str = (
                                    f"{err.get('type', '')}: {err.get('reason', '')}"
                                ).strip(": ")
                                if cause_str:
                                    break
                if cause_str:
                    break
            raise BulkIndexError(
                f"{failed} document(s) failed to index"
                + (
                    f" — first cause: {cause_str}"
                    if cause_str
                    else ""
                )
                + (" (cluster_block_exception)" if cluster_block else ""),
                per_doc_errors,
            )

    async def search(
        self,
        *,
        query: str,
        top_k: int,
        filter: QueryFilter | None = None,
    ) -> list[RetrievalCandidate]:
        # Boost ``hierarchical_path`` aggressively so a citation-style
        # query ("Điều 7 Khoản 2 Điểm h Nghị định 168/2024/NĐ-CP") lifts
        # chunks whose path matches the cited clause to the top.
        should_clauses: list[dict[str, Any]] = []
        if filter is not None and filter.path_substrings:
            for substring in filter.path_substrings:
                # match_phrase ensures the tokens stay adjacent in the
                # path. Each Điểm gets its own clause so multi-clause
                # citations ("Điểm h, i") match either chunk.
                should_clauses.append(
                    {
                        "match_phrase": {
                            "hierarchical_path": {"query": substring, "boost": 5.0}
                        }
                    }
                )
        if filter is not None and filter.so_ky_hieu:
            # Belt-and-braces: even if the strict ``so_ky_hieu`` term
            # filter below already pre-narrows the candidate set, scoring
            # this in `should` keeps relevance high when the query is
            # citation-only.
            should_clauses.append(
                {"term": {"so_ky_hieu": {"value": filter.so_ky_hieu, "boost": 3.0}}}
            )
        body: dict[str, Any] = {
            "size": top_k,
            "query": {
                "bool": {
                    "must": [
                        {
                            "multi_match": {
                                "query": query,
                                "type": "best_fields",
                                "fields": [
                                    "text^1.0",
                                    "body^1.0",
                                    "hierarchical_path^2.5",
                                ],
                            }
                        }
                    ],
                    "should": should_clauses,
                    "filter": _build_filters(filter),
                }
            },
        }
        resp = await self._client.search(index=self._index, body=body)
        out: list[RetrievalCandidate] = []
        for hit in resp["hits"]["hits"]:
            src = hit["_source"]
            out.append(
                RetrievalCandidate(
                    chunk_id=str(src["chunk_id"]),
                    parent_chunk_id=str(src.get("parent_chunk_id", "")),
                    document_id=int(src.get("document_id", 0)),
                    hierarchical_path=str(src.get("hierarchical_path", "")),
                    body=str(src.get("body", "")),
                    score=float(hit.get("_score", 0.0)),
                    source="sparse",
                    metadata=src,
                )
            )
        return out

    async def count(self) -> int:
        try:
            # Refresh first so newly bulk-indexed docs are searchable / countable.
            await self._client.indices.refresh(index=self._index)
            resp = await self._client.count(index=self._index)
            return int(resp.get("count", 0))
        except Exception:
            return 0

    async def aclose(self) -> None:
        await self._client.close()
