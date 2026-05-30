"""Neo4j graph-store adapter for cross-document references.

The graph schema used by this adapter:

    (:LegalDocument {id, title, so_ky_hieu, loai_van_ban,
                     co_quan_ban_hanh, ngay_ban_hanh, effective_status})

    (:LegalDocument)-[:RELATED {label}]->(:LegalDocument)

We keep the original Vietnamese `relationship` label as a string property so
no information is lost; canonical edge types are derived in queries via
`COALESCE(label_canonical, label)` in the future.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from neo4j import AsyncGraphDatabase

from ...domain.entities import LegalDocument
from ...domain.ports import GraphStoreAdapter
from ...infra.config import Settings
from ...infra.logging import get_logger

log = get_logger(__name__)


class Neo4jGraphStore(GraphStoreAdapter):
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._driver = AsyncGraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password.get_secret_value()),
        )
        self._db = settings.neo4j_database

    async def ensure_schema(self) -> None:
        statements = [
            "CREATE CONSTRAINT legal_document_id IF NOT EXISTS FOR (d:LegalDocument) REQUIRE d.id IS UNIQUE",
            "CREATE INDEX legal_document_so_ky_hieu IF NOT EXISTS FOR (d:LegalDocument) ON (d.so_ky_hieu)",
        ]
        async with self._driver.session(database=self._db) as session:
            for stmt in statements:
                try:
                    await session.run(stmt)
                except Exception as exc:
                    log.warning("neo4j.schema.skip", stmt=stmt, error=str(exc))

    async def upsert_documents(self, documents: Iterable[LegalDocument]) -> None:
        rows: list[dict[str, Any]] = []
        for d in documents:
            rows.append(
                {
                    "id": d.document_id,
                    "title": d.title,
                    "so_ky_hieu": d.so_ky_hieu,
                    "loai_van_ban": d.loai_van_ban,
                    "co_quan_ban_hanh": d.co_quan_ban_hanh,
                    "ngay_ban_hanh": d.ngay_ban_hanh.isoformat()
                    if d.ngay_ban_hanh
                    else None,
                    "effective_status": d.effective_status.value,
                }
            )
            if len(rows) >= 1000:
                await self._flush_documents(rows)
                rows = []
        if rows:
            await self._flush_documents(rows)

    async def _flush_documents(self, rows: list[dict[str, Any]]) -> None:
        cypher = (
            "UNWIND $rows AS r "
            "MERGE (d:LegalDocument {id: r.id}) "
            "SET d.title = r.title, "
            "    d.so_ky_hieu = r.so_ky_hieu, "
            "    d.loai_van_ban = r.loai_van_ban, "
            "    d.co_quan_ban_hanh = r.co_quan_ban_hanh, "
            "    d.ngay_ban_hanh = r.ngay_ban_hanh, "
            "    d.effective_status = r.effective_status"
        )
        async with self._driver.session(database=self._db) as session:
            await session.run(cypher, rows=rows)

    async def upsert_relationships(
        self,
        edges: Iterable[tuple[int, int, str]],
    ) -> None:
        batch: list[dict[str, Any]] = []
        for src, dst, label in edges:
            batch.append({"src": src, "dst": dst, "label": label or "RELATED"})
            if len(batch) >= 5000:
                await self._flush_edges(batch)
                batch = []
        if batch:
            await self._flush_edges(batch)

    async def _flush_edges(self, rows: list[dict[str, Any]]) -> None:
        cypher = (
            "UNWIND $rows AS r "
            "MERGE (a:LegalDocument {id: r.src}) "
            "MERGE (b:LegalDocument {id: r.dst}) "
            "MERGE (a)-[e:RELATED {label: r.label}]->(b)"
        )
        async with self._driver.session(database=self._db) as session:
            await session.run(cypher, rows=rows)

    async def neighbors(
        self,
        *,
        document_ids: Sequence[int],
        edge_labels: Sequence[str] | None = None,
        hops: int = 1,
        limit: int = 10,
    ) -> list[int]:
        if not document_ids:
            return []
        hops = max(1, min(hops, 3))
        label_filter = ""
        params: dict[str, Any] = {"ids": list(document_ids), "limit": limit}
        if edge_labels:
            label_filter = " WHERE e.label IN $labels"
            params["labels"] = list(edge_labels)
        cypher = (
            "MATCH (a:LegalDocument) WHERE a.id IN $ids "
            f"MATCH p = (a)-[e:RELATED*1..{hops}]-(b:LegalDocument){label_filter} "
            "WHERE b.id <> a.id "
            "RETURN DISTINCT b.id AS id LIMIT $limit"
        )
        async with self._driver.session(database=self._db) as session:
            result = await session.run(cypher, **params)
            ids = [int(record["id"]) async for record in result]
        return ids

    async def aclose(self) -> None:
        await self._driver.close()
