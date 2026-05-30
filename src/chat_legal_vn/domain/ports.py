"""Ports (Protocols) for every external dependency.

Domain and application layers depend only on these protocols. Vendor SDKs
are confined to `chat_legal_vn.adapters.*`.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any, Protocol, runtime_checkable

from .entities import (
    ChildChunk,
    LegalDocument,
    LegalNode,
    ParentChunk,
    QueryFilter,
    RetrievalCandidate,
)

# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------


@runtime_checkable
class LLMAdapter(Protocol):
    """A minimal LLM port covering chat completion and structured output."""

    async def complete(
        self,
        *,
        system: str,
        user: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
        stop: Sequence[str] | None = None,
    ) -> str:
        """Plain-text chat completion."""
        ...

    async def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """JSON-mode chat completion. Returns a parsed dict."""
        ...


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------


@runtime_checkable
class EmbeddingAdapter(Protocol):
    """An embedding port. Queries and documents have distinct entry points
    so that task-aware instructions can be applied to queries only.
    """

    @property
    def dim(self) -> int: ...

    @property
    def model_name(self) -> str: ...

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed plain document texts (no instruction prefix)."""
        ...

    async def embed_query(self, text: str) -> list[float]:
        """Embed a single query (with task-aware instruction prefix)."""
        ...

    async def embed_queries(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed multiple queries (with task-aware instruction prefix)."""
        ...


# ---------------------------------------------------------------------------
# Reranker
# ---------------------------------------------------------------------------


@runtime_checkable
class RerankerAdapter(Protocol):
    """A cross-encoder reranking port."""

    @property
    def model_name(self) -> str: ...

    async def rerank(
        self,
        *,
        query: str,
        documents: Sequence[str],
        top_n: int | None = None,
    ) -> list[tuple[int, float]]:
        """Score `documents` against `query`. Returns list of `(index, score)` sorted desc."""
        ...


# ---------------------------------------------------------------------------
# Stores
# ---------------------------------------------------------------------------


@runtime_checkable
class VectorStoreAdapter(Protocol):
    """A dense vector store port."""

    async def ensure_collection(self, *, dim: int) -> None: ...

    async def upsert(
        self,
        *,
        chunks: Sequence[ChildChunk],
        vectors: Sequence[list[float]],
    ) -> None: ...

    async def search(
        self,
        *,
        vector: Sequence[float],
        top_k: int,
        filter: QueryFilter | None = None,
    ) -> list[RetrievalCandidate]: ...

    async def get_by_ids(self, ids: Sequence[str]) -> list[RetrievalCandidate]: ...

    async def count(self) -> int: ...

    async def existing_document_ids(self) -> set[int]:
        """Return the set of distinct ``document_id`` values currently
        indexed. Used by the ingest pipeline for resume/skip semantics.
        Adapters that don't support this can return an empty set; callers
        will fall back to "process everything".
        """
        ...


@runtime_checkable
class LexicalStoreAdapter(Protocol):
    """A lexical (BM25) store port."""

    async def ensure_index(self) -> None: ...

    async def upsert(self, chunks: Sequence[ChildChunk]) -> None: ...

    async def search(
        self,
        *,
        query: str,
        top_k: int,
        filter: QueryFilter | None = None,
    ) -> list[RetrievalCandidate]: ...

    async def count(self) -> int: ...


@runtime_checkable
class GraphStoreAdapter(Protocol):
    """A graph-store port for cross-document references."""

    async def ensure_schema(self) -> None: ...

    async def upsert_documents(self, documents: Iterable[LegalDocument]) -> None: ...

    async def upsert_relationships(
        self,
        edges: Iterable[tuple[int, int, str]],  # (doc_id, other_doc_id, relationship)
    ) -> None: ...

    async def neighbors(
        self,
        *,
        document_ids: Sequence[int],
        edge_labels: Sequence[str] | None = None,
        hops: int = 1,
        limit: int = 10,
    ) -> list[int]: ...


# ---------------------------------------------------------------------------
# Document store (parent chunks)
# ---------------------------------------------------------------------------


@runtime_checkable
class DocStoreAdapter(Protocol):
    """A simple parent-chunk lookup store."""

    async def upsert(self, parents: Sequence[ParentChunk]) -> None: ...

    async def get(self, parent_chunk_id: str) -> ParentChunk | None: ...

    async def get_many(self, parent_chunk_ids: Sequence[str]) -> list[ParentChunk]: ...


# ---------------------------------------------------------------------------
# Chunker (domain service exposed as a port for swappability)
# ---------------------------------------------------------------------------


@runtime_checkable
class ChunkerAdapter(Protocol):
    """A legal-structure-aware chunker."""

    @property
    def chunking_version(self) -> str: ...

    def chunk(
        self,
        *,
        document: LegalDocument,
        root: LegalNode,
    ) -> tuple[list[ParentChunk], list[ChildChunk]]: ...
