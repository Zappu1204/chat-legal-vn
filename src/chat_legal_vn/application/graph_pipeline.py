"""Cross-reference graph ingestion.

Streams `metadata.parquet` and `relationships.parquet` row-group by
row-group so the 897k-edge corpus never lands as a materialised pandas
DataFrame in RAM.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.ports import GraphStoreAdapter
from ..infra.config import Settings
from ..infra.logging import get_logger
from ..services.corpus import iter_documents, iter_relationships

log = get_logger(__name__)


@dataclass(slots=True)
class GraphIngestStats:
    documents: int = 0
    edges: int = 0
    skipped_edges: int = 0


async def run_graph_ingest(
    *,
    settings: Settings,
    graph: GraphStoreAdapter,
    document_batch: int = 5000,
    edge_batch: int = 5000,
) -> GraphIngestStats:
    stats = GraphIngestStats()
    await graph.ensure_schema()

    documents = []
    for doc, _ in iter_documents(data_dir=settings.data_dir, require_html=False):
        documents.append(doc)
        if len(documents) >= document_batch:
            await graph.upsert_documents(documents)
            stats.documents += len(documents)
            documents = []
    if documents:
        await graph.upsert_documents(documents)
        stats.documents += len(documents)
        documents = []

    edges_buf: list[tuple[int, int, str]] = []
    for edge in iter_relationships(data_dir=settings.data_dir):
        edges_buf.append(edge)
        if len(edges_buf) >= edge_batch:
            await graph.upsert_relationships(edges_buf)
            stats.edges += len(edges_buf)
            edges_buf = []
    if edges_buf:
        await graph.upsert_relationships(edges_buf)
        stats.edges += len(edges_buf)
        edges_buf = []

    log.info(
        "graph.ingest_done",
        documents=stats.documents,
        edges=stats.edges,
        skipped_edges=stats.skipped_edges,
    )
    return stats
