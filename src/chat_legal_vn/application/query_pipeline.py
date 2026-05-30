"""Online QA Pipeline (Agentic-style).

Sequence:

    User query
      → QueryPlanner (normalize, intent, decomposition, HyDE, filter extraction)
      → For each sub-query:
          ├─ Dense search (Qdrant)
          └─ Sparse search (Elasticsearch BM25)
      → RRF fusion (or alpha)
      → Cross-encoder reranking (Qwen3-Reranker-8B via vLLM /v1/rerank)
      → Diversity-aware top-K selection (max children per parent article)
      → Optional GraphRAG expansion (Neo4j neighbors of retained doc_ids)
      → Re-rank expanded set
      → Parent-chunk hydration (DocStore)
      → AnswerEngine (IRAC + strict citations, repair-once)
      → CitationResolver (every citation resolves to a real Legal_Node)
      → Graceful degradation when evidence is insufficient
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from dataclasses import dataclass

from ..domain.entities import (
    Answer,
    Citation,
    ParentChunk,
    QueryFilter,
    QueryPlan,
    RetrievalCandidate,
    SubQuery,
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
from ..infra.config import Settings
from ..infra.logging import get_logger
from ..services.answer import AnswerEngine
from ..services.fusion import alpha_fusion, rrf
from ..services.query_planner import QueryPlanner

log = get_logger(__name__)


@dataclass(slots=True)
class QueryDeps:
    settings: Settings
    llm: LLMAdapter
    embedding: EmbeddingAdapter
    reranker: RerankerAdapter
    vector_store: VectorStoreAdapter
    lexical_store: LexicalStoreAdapter
    doc_store: DocStoreAdapter
    graph_store: GraphStoreAdapter | None = None


@dataclass(slots=True)
class QueryResult:
    answer: Answer
    plan: QueryPlan
    retrieved: list[RetrievalCandidate]
    reranked: list[RetrievalCandidate]
    expanded: list[RetrievalCandidate]
    timings: dict[str, float]


class QueryPipeline:
    """Top-level orchestrator for the online QA flow."""

    def __init__(self, deps: QueryDeps) -> None:
        self._deps = deps
        self._settings = deps.settings
        self._planner = QueryPlanner(
            llm=deps.llm,
            decompose_enabled=deps.settings.query_decompose_enabled,
            hyde_enabled=deps.settings.query_hyde_enabled,
            hyde_num=deps.settings.query_hyde_num,
        )
        self._answer_engine = AnswerEngine(llm=deps.llm)

    # ------------------------------------------------------------------
    # Entry
    # ------------------------------------------------------------------

    async def run(self, query: str) -> QueryResult:
        timings: dict[str, float] = {}
        t0 = time.perf_counter()

        plan = await self._planner.plan(query)
        timings["plan"] = time.perf_counter() - t0

        # 1. Hybrid retrieval over each sub-query (and HyDE passages).
        t1 = time.perf_counter()
        retrieved = await self._retrieve(plan)
        timings["retrieve"] = time.perf_counter() - t1

        # 2. Cross-encoder rerank against the original query.
        t2 = time.perf_counter()
        reranked = await self._rerank(plan.normalized, retrieved)
        timings["rerank"] = time.perf_counter() - t2

        # 3. Optional graph expansion + re-rank.
        t3 = time.perf_counter()
        expanded = await self._graph_expand(plan, reranked)
        timings["graph"] = time.perf_counter() - t3

        # 4. Hydrate parent chunks for LLM context.
        t4 = time.perf_counter()
        retained = expanded[: self._settings.reranker_top_k]
        parents = await self._hydrate_parents(retained)
        timings["hydrate"] = time.perf_counter() - t4

        # 5. Answer engine.
        t5 = time.perf_counter()
        answer = await self._answer_engine.answer(
            query=plan.normalized,
            candidates=retained,
            parents=parents,
        )
        timings["answer"] = time.perf_counter() - t5

        # 6. Citation resolution.
        t6 = time.perf_counter()
        answer = self._resolve_citations(answer, retained, parents)
        timings["resolve"] = time.perf_counter() - t6

        timings["total"] = time.perf_counter() - t0

        log.info(
            "query.completed",
            query=query,
            sub_queries=[s.text for s in plan.sub_queries],
            retrieved=len(retrieved),
            reranked=len(reranked),
            expanded=len(expanded),
            retained=len(retained),
            confidence=answer.confidence,
            needs_human_review=answer.needs_human_review,
            timings=timings,
        )

        return QueryResult(
            answer=answer,
            plan=plan,
            retrieved=retrieved,
            reranked=reranked,
            expanded=expanded,
            timings=timings,
        )

    # ------------------------------------------------------------------
    # Step 1: Hybrid retrieval
    # ------------------------------------------------------------------

    async def _retrieve(self, plan: QueryPlan) -> list[RetrievalCandidate]:
        # Build the list of retrieval queries: every sub-query + every HyDE.
        sub_queries: list[SubQuery] = list(plan.sub_queries)
        for hyde_text in plan.hyde_passages:
            sub_queries.append(
                SubQuery(
                    text=hyde_text,
                    intent=plan.intent,
                    filter=plan.filter,
                    is_hyde=True,
                )
            )

        if not sub_queries:
            sub_queries = [SubQuery(text=plan.normalized, intent=plan.intent)]

        # Pre-compute query embeddings in one batch.
        emb_texts = [s.text for s in sub_queries]
        emb_vectors = await self._deps.embedding.embed_queries(emb_texts)

        # Run dense and sparse in parallel for each sub-query.
        async def _dense(sq: SubQuery, vec: list[float]) -> list[RetrievalCandidate]:
            return await self._deps.vector_store.search(
                vector=vec,
                top_k=self._settings.retriever_top_k,
                filter=sq.filter,
            )

        async def _sparse(sq: SubQuery) -> list[RetrievalCandidate]:
            return await self._deps.lexical_store.search(
                query=sq.text,
                top_k=self._settings.retriever_top_k,
                filter=sq.filter,
            )

        tasks = []
        for sq, vec in zip(sub_queries, emb_vectors, strict=True):
            tasks.append(_dense(sq, vec))
            tasks.append(_sparse(sq))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        per_subquery_lists: list[list[RetrievalCandidate]] = []
        for i, _sq in enumerate(sub_queries):
            dense = results[2 * i]
            sparse = results[2 * i + 1]
            dense_list = _coerce(dense)
            sparse_list = _coerce(sparse)

            for c in dense_list:
                c.source = "dense"
            for c in sparse_list:
                c.source = "sparse"

            if self._settings.hybrid_fusion == "rrf":
                fused = rrf(
                    lists=[dense_list, sparse_list],
                    k=self._settings.hybrid_rrf_k,
                )
            else:
                fused = alpha_fusion(
                    dense=dense_list,
                    sparse=sparse_list,
                    alpha_dense=self._settings.hybrid_alpha_dense,
                    alpha_sparse=self._settings.hybrid_alpha_sparse,
                )
            per_subquery_lists.append(fused)

        # Union across sub-queries via RRF over the fused lists.
        union = rrf(lists=per_subquery_lists, k=self._settings.hybrid_rrf_k)
        return union[: self._settings.retriever_top_k * 2]

    # ------------------------------------------------------------------
    # Step 2: Cross-encoder rerank
    # ------------------------------------------------------------------

    async def _rerank(
        self,
        query: str,
        candidates: Sequence[RetrievalCandidate],
    ) -> list[RetrievalCandidate]:
        if not candidates:
            return []
        documents = [c.body or c.metadata.get("text", "") for c in candidates]
        try:
            scores = await self._deps.reranker.rerank(
                query=query,
                documents=documents,
                top_n=None,
            )
        except Exception as exc:
            log.warning("rerank.failed_fallback", error=str(exc))
            return list(candidates)[: self._settings.reranker_top_k]

        threshold = self._settings.reranker_threshold
        out: list[RetrievalCandidate] = []
        for idx, score in scores:
            if score < threshold and idx != scores[0][0]:
                # Top-1 is always preserved (Requirement 8.5)
                continue
            cand = candidates[idx].model_copy(update={"score": score, "source": "rerank"})
            out.append(cand)

        # Diversity-aware selection
        out = self._diversify(out, max_per_parent=self._settings.diversity_max_per_parent)
        return out[: self._settings.reranker_top_k]

    @staticmethod
    def _diversify(
        items: Sequence[RetrievalCandidate],
        *,
        max_per_parent: int,
    ) -> list[RetrievalCandidate]:
        if max_per_parent <= 0:
            return list(items)
        counts: dict[str, int] = {}
        kept: list[RetrievalCandidate] = []
        # Items are assumed sorted desc by score.
        for c in items:
            n = counts.get(c.parent_chunk_id, 0)
            if n >= max_per_parent and len(kept) > 0:
                continue
            counts[c.parent_chunk_id] = n + 1
            kept.append(c)
        return kept

    # ------------------------------------------------------------------
    # Step 3: Graph expansion
    # ------------------------------------------------------------------

    async def _graph_expand(
        self,
        plan: QueryPlan,
        reranked: Sequence[RetrievalCandidate],
    ) -> list[RetrievalCandidate]:
        if (
            not self._settings.graph_expand_enabled
            or self._deps.graph_store is None
            or not reranked
        ):
            return list(reranked)

        seed_doc_ids = sorted({c.document_id for c in reranked if c.document_id})
        try:
            neighbor_ids = await self._deps.graph_store.neighbors(
                document_ids=seed_doc_ids,
                hops=self._settings.graph_expand_hops,
                limit=self._settings.graph_expand_limit,
            )
        except Exception as exc:
            log.warning("graph.unavailable", error=str(exc))
            return list(reranked)

        if not neighbor_ids:
            return list(reranked)

        # Pull representative chunks for each neighbor doc via a metadata
        # filter on the vector store. We reuse the dense path with the
        # original query for cheap relevance.
        try:
            qvec = await self._deps.embedding.embed_query(plan.normalized)
            extra: list[RetrievalCandidate] = []
            for doc_id in neighbor_ids:
                qf = QueryFilter()
                hits = await self._deps.vector_store.search(
                    vector=qvec, top_k=2, filter=qf
                )
                # Filter post-hoc to only the matching doc_id (since QueryFilter
                # has no doc-id field by design).
                for h in hits:
                    if h.document_id == doc_id:
                        extra.append(h)
        except Exception as exc:
            log.warning("graph.expand_fetch_failed", error=str(exc))
            return list(reranked)

        if not extra:
            return list(reranked)

        # Merge & re-rank
        merged_ids: set[str] = {c.chunk_id for c in reranked}
        merged: list[RetrievalCandidate] = list(reranked)
        for c in extra:
            if c.chunk_id not in merged_ids:
                merged_ids.add(c.chunk_id)
                merged.append(c.model_copy(update={"source": "graph"}))

        return await self._rerank(plan.normalized, merged)

    # ------------------------------------------------------------------
    # Step 4: Parent hydration
    # ------------------------------------------------------------------

    async def _hydrate_parents(
        self, candidates: Sequence[RetrievalCandidate]
    ) -> dict[str, ParentChunk]:
        ids = sorted({c.parent_chunk_id for c in candidates if c.parent_chunk_id})
        if not ids:
            return {}
        parents = await self._deps.doc_store.get_many(ids)
        return {p.parent_chunk_id: p for p in parents}

    # ------------------------------------------------------------------
    # Step 5: Citation resolution
    # ------------------------------------------------------------------

    def _resolve_citations(
        self,
        answer: Answer,
        candidates: Sequence[RetrievalCandidate],
        parents: dict[str, ParentChunk],
    ) -> Answer:
        # Index candidates by (loai, so_ky_hieu, dieu) so we can resolve
        # citations against the actual evidence.
        index: dict[tuple[str, str, str], int] = {}
        for c in candidates:
            meta = c.metadata or {}
            loai = (meta.get("loai_van_ban") or "").lower()
            skh = (meta.get("so_ky_hieu") or "").lower().replace(" ", "")
            # Try to read article number from hierarchical_path
            dieu = ""
            if "Điều " in c.hierarchical_path:
                tail = c.hierarchical_path.split("Điều ", 1)[1]
                dieu = tail.split(">", 1)[0].split(" ", 1)[0].strip()
            index[(loai, skh, dieu)] = c.document_id

        resolved: list[Citation] = []
        unresolved: list[Citation] = []
        for cit in answer.citations:
            loai = (cit.loai_van_ban or "").lower()
            skh = (cit.so_ky_hieu or "").lower().replace(" ", "")
            dieu = (cit.dieu or "").strip()
            key = (loai, skh, dieu)
            if key in index:
                resolved.append(cit.model_copy(update={"document_id": index[key]}))
            elif any(k[1] == skh and skh for k in index):
                # Match by so_ky_hieu only — partial resolution.
                document_id = next(v for k, v in index.items() if k[1] == skh)
                resolved.append(cit.model_copy(update={"document_id": document_id}))
            else:
                unresolved.append(cit)

        if unresolved and not resolved:
            answer.warnings.append("all_citations_unresolved")
            answer.needs_human_review = True
        elif unresolved:
            answer.warnings.append(f"unresolved_citations:{len(unresolved)}")

        # Keep only resolved citations in the public answer; unresolved are dropped.
        answer.citations = resolved
        return answer


def _coerce(x: object) -> list[RetrievalCandidate]:
    if isinstance(x, list):
        return [c for c in x if isinstance(c, RetrievalCandidate)]
    if isinstance(x, BaseException):
        log.warning("retrieval.subtask_failed", error=str(x))
    return []
