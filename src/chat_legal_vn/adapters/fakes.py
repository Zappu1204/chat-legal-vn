"""In-memory fake adapters for unit and property-based testing.

These avoid the need for live infrastructure during tests. They are
intentionally simple but type-conformant with the ports.
"""

from __future__ import annotations

import math
import re
from collections.abc import AsyncIterator, Iterable, Sequence
from typing import Any

from ..domain.entities import (
    ChildChunk,
    LegalDocument,
    ParentChunk,
    QueryFilter,
    RetrievalCandidate,
)
from ..domain.ports import (
    DocStoreAdapter,
    EmbeddingAdapter,
    GraphStoreAdapter,
    LexicalStoreAdapter,
    LLMAdapter,
    RerankerAdapter,
    VectorStoreAdapter,
)

# ---------------------------------------------------------------------------
# Embeddings — hashing-based, deterministic
# ---------------------------------------------------------------------------


class HashingEmbedding(EmbeddingAdapter):
    def __init__(self, dim: int = 256) -> None:
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def model_name(self) -> str:
        return f"fake-hashing-{self._dim}"

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self._dim
        for tok in re.findall(r"\w+", text.lower()):
            h = hash(tok)
            v[h % self._dim] += 1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._vec(text)

    async def embed_queries(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]


# ---------------------------------------------------------------------------
# Reranker — token-overlap based
# ---------------------------------------------------------------------------


class TokenOverlapReranker(RerankerAdapter):
    @property
    def model_name(self) -> str:
        return "fake-token-overlap"

    async def rerank(
        self,
        *,
        query: str,
        documents: Sequence[str],
        top_n: int | None = None,
    ) -> list[tuple[int, float]]:
        q = set(re.findall(r"\w+", query.lower()))
        scored: list[tuple[int, float]] = []
        for i, d in enumerate(documents):
            ds = set(re.findall(r"\w+", d.lower()))
            inter = len(q & ds)
            union = len(q | ds) or 1
            scored.append((i, inter / union))
        scored.sort(key=lambda x: x[1], reverse=True)
        if top_n is not None:
            scored = scored[:top_n]
        return scored


# ---------------------------------------------------------------------------
# LLM — echoes / templated responses
# ---------------------------------------------------------------------------


class EchoLLM(LLMAdapter):
    def __init__(
        self,
        *,
        json_response: dict[str, Any] | None = None,
        stream_text: str | None = None,
    ) -> None:
        self._json_response = json_response or {}
        self._stream_text = stream_text

    async def complete(
        self,
        *,
        system: str,
        user: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
        stop: Sequence[str] | None = None,
    ) -> str:
        return f"[echo] {user[:200]}"

    async def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        return dict(self._json_response)

    async def stream(
        self,
        *,
        system: str,
        user: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
        stop: Sequence[str] | None = None,
    ) -> AsyncIterator[str]:
        text = self._stream_text if self._stream_text is not None else f"[echo] {user[:200]}"
        for token in re.findall(r"\S+\s*", text):
            yield token


# ---------------------------------------------------------------------------
# Vector store — cosine similarity in memory
# ---------------------------------------------------------------------------


class InMemoryVectorStore(VectorStoreAdapter):
    def __init__(self) -> None:
        self._chunks: dict[str, ChildChunk] = {}
        self._vectors: dict[str, list[float]] = {}

    async def ensure_collection(self, *, dim: int) -> None:
        return

    async def upsert(
        self,
        *,
        chunks: Sequence[ChildChunk],
        vectors: Sequence[list[float]],
    ) -> None:
        for c, v in zip(chunks, vectors, strict=True):
            self._chunks[c.chunk_id] = c
            self._vectors[c.chunk_id] = v

    @staticmethod
    def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
        if not a or not b:
            return 0.0
        dot = sum(x * y for x, y in zip(a, b, strict=True))
        na = math.sqrt(sum(x * x for x in a)) or 1.0
        nb = math.sqrt(sum(y * y for y in b)) or 1.0
        return dot / (na * nb)

    async def search(
        self,
        *,
        vector: Sequence[float],
        top_k: int,
        filter: QueryFilter | None = None,
    ) -> list[RetrievalCandidate]:
        scored: list[tuple[ChildChunk, float]] = []
        for cid, vec in self._vectors.items():
            chunk = self._chunks[cid]
            if filter is not None:
                m = chunk.metadata
                if filter.so_ky_hieu and m.so_ky_hieu != filter.so_ky_hieu:
                    continue
                if filter.loai_van_ban and m.loai_van_ban not in filter.loai_van_ban:
                    continue
                if filter.effective_only and m.effective_status.value != "effective":
                    continue
            scored.append((chunk, self._cosine(vector, vec)))
        scored.sort(key=lambda x: x[1], reverse=True)
        out: list[RetrievalCandidate] = []
        for c, s in scored[:top_k]:
            out.append(
                RetrievalCandidate(
                    chunk_id=c.chunk_id,
                    parent_chunk_id=c.parent_chunk_id,
                    document_id=c.document_id,
                    hierarchical_path=c.hierarchical_path,
                    body=c.body,
                    score=s,
                    source="dense",
                    metadata=c.metadata.model_dump(mode="json"),
                )
            )
        return out

    async def get_by_ids(self, ids: Sequence[str]) -> list[RetrievalCandidate]:
        out: list[RetrievalCandidate] = []
        for cid in ids:
            c = self._chunks.get(cid)
            if c is None:
                continue
            out.append(
                RetrievalCandidate(
                    chunk_id=c.chunk_id,
                    parent_chunk_id=c.parent_chunk_id,
                    document_id=c.document_id,
                    hierarchical_path=c.hierarchical_path,
                    body=c.body,
                    score=0.0,
                    source="lookup",
                    metadata=c.metadata.model_dump(mode="json"),
                )
            )
        return out

    async def count(self) -> int:
        return len(self._chunks)


# ---------------------------------------------------------------------------
# Lexical store — naive token overlap
# ---------------------------------------------------------------------------


class InMemoryLexicalStore(LexicalStoreAdapter):
    def __init__(self) -> None:
        self._chunks: dict[str, ChildChunk] = {}

    async def ensure_index(self) -> None:
        return

    async def upsert(self, chunks: Sequence[ChildChunk]) -> None:
        for c in chunks:
            self._chunks[c.chunk_id] = c

    async def search(
        self,
        *,
        query: str,
        top_k: int,
        filter: QueryFilter | None = None,
    ) -> list[RetrievalCandidate]:
        q = set(re.findall(r"\w+", query.lower()))
        scored: list[tuple[ChildChunk, float]] = []
        for c in self._chunks.values():
            d = set(re.findall(r"\w+", c.text_for_embedding.lower()))
            inter = len(q & d)
            if inter == 0:
                continue
            scored.append((c, inter))
        scored.sort(key=lambda x: x[1], reverse=True)
        out: list[RetrievalCandidate] = []
        for c, s in scored[:top_k]:
            out.append(
                RetrievalCandidate(
                    chunk_id=c.chunk_id,
                    parent_chunk_id=c.parent_chunk_id,
                    document_id=c.document_id,
                    hierarchical_path=c.hierarchical_path,
                    body=c.body,
                    score=float(s),
                    source="sparse",
                    metadata=c.metadata.model_dump(mode="json"),
                )
            )
        return out

    async def count(self) -> int:
        return len(self._chunks)


# ---------------------------------------------------------------------------
# Graph store — adjacency list
# ---------------------------------------------------------------------------


class InMemoryGraphStore(GraphStoreAdapter):
    def __init__(self) -> None:
        self._documents: dict[int, LegalDocument] = {}
        self._adj: dict[int, list[tuple[int, str]]] = {}

    async def ensure_schema(self) -> None:
        return

    async def upsert_documents(self, documents: Iterable[LegalDocument]) -> None:
        for d in documents:
            self._documents[d.document_id] = d
            self._adj.setdefault(d.document_id, [])

    async def upsert_relationships(
        self, edges: Iterable[tuple[int, int, str]]
    ) -> None:
        for src, dst, label in edges:
            self._adj.setdefault(src, []).append((dst, label))

    async def neighbors(
        self,
        *,
        document_ids: Sequence[int],
        edge_labels: Sequence[str] | None = None,
        hops: int = 1,
        limit: int = 10,
    ) -> list[int]:
        labels = set(edge_labels or [])
        seen: set[int] = set(document_ids)
        frontier: list[int] = list(document_ids)
        result: list[int] = []
        for _ in range(hops):
            next_frontier: list[int] = []
            for node in frontier:
                for nb, label in self._adj.get(node, []):
                    if labels and label not in labels:
                        continue
                    if nb in seen:
                        continue
                    seen.add(nb)
                    next_frontier.append(nb)
                    result.append(nb)
                    if len(result) >= limit:
                        return result
            frontier = next_frontier
        return result


# ---------------------------------------------------------------------------
# Doc store — dict
# ---------------------------------------------------------------------------


class InMemoryDocStore(DocStoreAdapter):
    def __init__(self) -> None:
        self._parents: dict[str, ParentChunk] = {}

    async def upsert(self, parents: Sequence[ParentChunk]) -> None:
        for p in parents:
            self._parents[p.parent_chunk_id] = p

    async def get(self, parent_chunk_id: str) -> ParentChunk | None:
        return self._parents.get(parent_chunk_id)

    async def get_many(self, parent_chunk_ids: Sequence[str]) -> list[ParentChunk]:
        return [self._parents[i] for i in parent_chunk_ids if i in self._parents]
