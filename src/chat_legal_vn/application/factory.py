"""Composition root: build live adapters from `Settings`.

Tests construct `QueryDeps` / `IngestDeps` with fakes directly; production
code uses these factory helpers.
"""

from __future__ import annotations

import contextlib
from contextlib import AsyncExitStack
from dataclasses import dataclass

from ..adapters.docstore.qdrant_doc_store import QdrantParentDocStore
from ..adapters.embeddings.vllm_qwen3 import VLLMQwen3EmbeddingAdapter
from ..adapters.graph.neo4j_store import Neo4jGraphStore
from ..adapters.lexical.elasticsearch_store import ElasticsearchLexicalStore
from ..adapters.llm.openai_llm import OpenAICompatLLM
from ..adapters.rerankers.vllm_qwen3 import VLLMQwen3RerankerAdapter
from ..adapters.vectorstores.qdrant_store import QdrantVectorStore
from ..infra.config import Settings, get_settings
from .ingest_pipeline import IngestDeps, IngestPipeline
from .query_pipeline import QueryDeps, QueryPipeline


@dataclass
class LiveAdapters:
    settings: Settings
    llm: OpenAICompatLLM
    embedding: VLLMQwen3EmbeddingAdapter
    reranker: VLLMQwen3RerankerAdapter
    vector_store: QdrantVectorStore
    lexical_store: ElasticsearchLexicalStore
    doc_store: QdrantParentDocStore
    graph_store: Neo4jGraphStore

    async def aclose(self) -> None:
        for closer in (
            getattr(self.reranker, "aclose", None),
            getattr(self.vector_store, "aclose", None),
            getattr(self.lexical_store, "aclose", None),
            getattr(self.doc_store, "aclose", None),
            getattr(self.graph_store, "aclose", None),
        ):
            if closer is None:
                continue
            with contextlib.suppress(Exception):
                await closer()


def build_adapters(settings: Settings | None = None) -> LiveAdapters:
    settings = settings or get_settings()
    return LiveAdapters(
        settings=settings,
        llm=OpenAICompatLLM(settings),
        embedding=VLLMQwen3EmbeddingAdapter(settings),
        reranker=VLLMQwen3RerankerAdapter(settings),
        vector_store=QdrantVectorStore(settings),
        lexical_store=ElasticsearchLexicalStore(settings),
        doc_store=QdrantParentDocStore(settings),
        graph_store=Neo4jGraphStore(settings),
    )


def build_query_pipeline(adapters: LiveAdapters) -> QueryPipeline:
    return QueryPipeline(
        QueryDeps(
            settings=adapters.settings,
            llm=adapters.llm,
            embedding=adapters.embedding,
            reranker=adapters.reranker,
            vector_store=adapters.vector_store,
            lexical_store=adapters.lexical_store,
            doc_store=adapters.doc_store,
            graph_store=adapters.graph_store,
        )
    )


def build_ingest_pipeline(adapters: LiveAdapters) -> IngestPipeline:
    return IngestPipeline(
        IngestDeps(
            settings=adapters.settings,
            embedding=adapters.embedding,
            vector_store=adapters.vector_store,
            lexical_store=adapters.lexical_store,
            doc_store=adapters.doc_store,
        )
    )


__all__ = [
    "AsyncExitStack",
    "LiveAdapters",
    "build_adapters",
    "build_ingest_pipeline",
    "build_query_pipeline",
]
