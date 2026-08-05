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
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Any

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
from ..services.metadata_lookup import filter_in_path, lookup_by_citation
from ..services.query_planner import QueryPlanner
from ..services.recency import apply_effective_bias

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


@dataclass(slots=True)
class _Prepared:
    """Internal result of the shared retrieval stages."""

    plan: QueryPlan
    retrieved: list[RetrievalCandidate]
    reranked: list[RetrievalCandidate]
    expanded: list[RetrievalCandidate]
    retained: list[RetrievalCandidate]
    parents: dict[str, ParentChunk]


@dataclass(slots=True)
class StreamHandle:
    """Handle returned by :meth:`QueryPipeline.run_stream`.

    Exposes the retrieved evidence up front (for an immediate ``sources``
    event) and a coroutine factory ``answer_deltas`` that yields Markdown
    text deltas. ``citations`` and ``warnings`` are populated only after
    the stream is fully consumed.
    """

    engine: Any  # AnswerEngine — typed as Any to avoid a domain import cycle
    plan: QueryPlan
    retained: list[RetrievalCandidate]
    parents: dict[str, ParentChunk]
    timings: dict[str, float]
    started_at: float

    async def answer_deltas(self) -> AsyncIterator[str]:
        async for delta in self.engine.stream_answer(
            query=self.plan.normalized,
            candidates=self.retained,
            parents=self.parents,
        ):
            yield delta
        self.timings["total"] = time.perf_counter() - self.started_at


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
            hyde_temperature=deps.settings.query_hyde_temperature,
            cache_size=deps.settings.query_plan_cache_size,
        )
        self._answer_engine = AnswerEngine(llm=deps.llm)

    # ------------------------------------------------------------------
    # Entry
    # ------------------------------------------------------------------

    async def run(self, query: str) -> QueryResult:
        timings: dict[str, float] = {}
        t0 = time.perf_counter()

        prep = await self._prepare(query, timings, t0)
        plan = prep.plan
        retained = prep.retained
        parents = prep.parents

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
            retrieved=len(prep.retrieved),
            reranked=len(prep.reranked),
            expanded=len(prep.expanded),
            retained=len(retained),
            confidence=answer.confidence,
            needs_human_review=answer.needs_human_review,
            timings=timings,
        )

        return QueryResult(
            answer=answer,
            plan=plan,
            retrieved=prep.retrieved,
            reranked=prep.reranked,
            expanded=prep.expanded,
            timings=timings,
        )

    async def run_stream(self, query: str) -> StreamHandle:
        """Run retrieval, then expose sources + a token stream for the answer.

        The returned :class:`StreamHandle` carries the retrieved/retained
        candidates and hydrated parents (so the API can emit a ``sources``
        event immediately) plus an async generator ``answer_deltas`` that
        yields Markdown text as the LLM produces it. Citations are parsed
        from the accumulated text after the stream completes.
        """
        timings: dict[str, float] = {}
        t0 = time.perf_counter()
        prep = await self._prepare(query, timings, t0)
        return StreamHandle(
            engine=self._answer_engine,
            plan=prep.plan,
            retained=prep.retained,
            parents=prep.parents,
            timings=timings,
            started_at=t0,
        )

    async def _prepare(
        self,
        query: str,
        timings: dict[str, float],
        t0: float,
    ) -> _Prepared:
        """Shared retrieval → rerank → graph → hydrate stages.

        Used by both :meth:`run` (blocking JSON answer) and
        :meth:`run_stream` (token streaming). Returns everything needed to
        produce an answer without re-running retrieval.
        """
        plan = await self._planner.plan(query)
        timings["plan"] = time.perf_counter() - t0

        # 1. Hybrid retrieval over each sub-query (and HyDE passages).
        t1 = time.perf_counter()
        retrieved = await self._retrieve(plan)
        timings["retrieve"] = time.perf_counter() - t1

        # 1b. Citation-anchored hard filter. When the planner detected a
        # specific clause we drop every retrieved chunk whose
        # ``hierarchical_path`` does not match — the cross-encoder is
        # otherwise free to promote a *neighbouring* clause whose body
        # text scores higher semantically and the answer engine then
        # confidently hallucinates the wrong rule. Falling back to the
        # unfiltered list when no chunk matches keeps recall non-zero on
        # questions whose citation is malformed or pre-dates the corpus.
        if plan.filter.so_ky_hieu and plan.filter.path_substrings and retrieved:
            anchored_pre = filter_in_path(retrieved, plan.filter.path_substrings)
            if anchored_pre:
                log.info(
                    "retrieve.path_filter_applied",
                    before=len(retrieved),
                    after=len(anchored_pre),
                    path_substrings=plan.filter.path_substrings,
                )
                retrieved = anchored_pre
            else:
                log.warning(
                    "retrieve.path_filter_empty",
                    path_substrings=plan.filter.path_substrings,
                    note="falling back to unfiltered list",
                )

        # 2. Cross-encoder rerank against the original query.
        t2 = time.perf_counter()
        reranked = await self._rerank(plan.normalized, retrieved)
        timings["rerank"] = time.perf_counter() - t2

        # 2b. When the question carries a strong citation anchor, restrict
        # retained candidates to those that actually match the cited
        # clause. Without this guard the cross-encoder routinely promotes
        # a semantically similar — but legally wrong — Điểm/Khoản from a
        # neighbouring article and the answer engine then hallucinates.
        if (
            plan.filter.so_ky_hieu
            and plan.filter.path_substrings
            and reranked
        ):
            anchored = filter_in_path(reranked, plan.filter.path_substrings)
            if anchored:
                log.info(
                    "rerank.path_anchored",
                    before=len(reranked),
                    after=len(anchored),
                    path_substrings=plan.filter.path_substrings,
                )
                reranked = anchored

        # 2c. Effective-status bias. Re-weight the reranked list so
        # currently-in-force law outranks superseded versions of the same
        # subject. Skip when the user explicitly anchored a citation
        # (so_ky_hieu) — they asked for that specific document regardless
        # of status.
        if not plan.filter.so_ky_hieu and reranked:
            biased = apply_effective_bias(self._settings, reranked)
            if biased and biased[0].chunk_id != reranked[0].chunk_id:
                log.info(
                    "rerank.effective_bias_applied",
                    new_top_status=(biased[0].metadata or {}).get("effective_status"),
                    new_top_skh=(biased[0].metadata or {}).get("so_ky_hieu"),
                )
            reranked = biased

        # 3. Optional graph expansion + re-rank.
        t3 = time.perf_counter()
        expanded = await self._graph_expand(plan, reranked)
        timings["graph"] = time.perf_counter() - t3

        # 4. Hydrate parent chunks for LLM context.
        t4 = time.perf_counter()
        retained = expanded[: self._settings.reranker_top_k]
        parents = await self._hydrate_parents(retained)
        timings["hydrate"] = time.perf_counter() - t4

        return _Prepared(
            plan=plan,
            retrieved=retrieved,
            reranked=reranked,
            expanded=expanded,
            retained=retained,
            parents=parents,
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

        tasks: list[Any] = []
        for sq, vec in zip(sub_queries, emb_vectors, strict=True):
            tasks.append(_dense(sq, vec))
            tasks.append(_sparse(sq))

        # Metadata-aware lookup: if the planner detected a precise
        # citation (so_ky_hieu + at least one of Điều/Khoản/Điểm), pull
        # the exact chunks straight from the lexical store. This adds one
        # extra task to the parallel batch but lifts citation-style
        # questions to top-1 recall when the cited text exists in the
        # corpus, regardless of how the dense retriever scores them.
        metadata_task_index: int | None = None
        if plan.filter.so_ky_hieu and plan.filter.path_substrings:
            metadata_task_index = len(tasks)
            tasks.append(
                lookup_by_citation(
                    lexical_store=self._deps.lexical_store,
                    query_filter=plan.filter,
                    top_k_per_path=5,
                    overall_cap=30,
                )
            )

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

        # Pick up the metadata-anchor list and put it at the head of the
        # input lists so RRF gives its top-1 the highest score
        # (1/(k+1) plus contributions from semantic agreement).
        metadata_hits: list[RetrievalCandidate] = []
        if metadata_task_index is not None:
            metadata_hits = _coerce(results[metadata_task_index])
            log.info(
                "retrieve.metadata_lookup",
                so_ky_hieu=plan.filter.so_ky_hieu,
                path_substrings=plan.filter.path_substrings,
                hits=len(metadata_hits),
            )

        all_lists: list[list[RetrievalCandidate]] = []
        if metadata_hits:
            all_lists.append(metadata_hits)
        all_lists.extend(per_subquery_lists)

        # Union across all input lists via RRF.
        union = rrf(lists=all_lists, k=self._settings.hybrid_rrf_k)
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
        # Cross-encode against the FULL article (parent) text, not the bare
        # leaf clause. A Điểm body is often a fragment like "Không chấp
        # hành hiệu lệnh của đèn tín hiệu giao thông;" — it carries neither
        # the penalty amount nor the "xe mô tô" subject (both live in the
        # parent Điều / sibling Khoản), so the reranker scores it below an
        # unrelated document that shares more surface tokens. Reranking on
        # the parent body gives the model the penalty + subject context and
        # matches the evidence the answer engine ultimately reads.
        #
        # We fetch each parent once and cap its length to the reranker's
        # budget. Fallbacks: text_for_embedding (payload ``text``) → body.
        parent_ids = sorted(
            {c.parent_chunk_id for c in candidates if c.parent_chunk_id}
        )
        parents: dict[str, Any] = {}
        if parent_ids:
            try:
                fetched = await self._deps.doc_store.get_many(parent_ids)
                parents = {p.parent_chunk_id: p for p in fetched}
            except Exception as exc:
                log.warning("rerank.parent_hydrate_failed", error=str(exc))

        def _doc_text(c: RetrievalCandidate) -> str:
            parent = parents.get(c.parent_chunk_id)
            if parent is not None and parent.body:
                # Cap the parent body so a 13k-char article doesn't blow
                # the reranker latency budget (cross-encoders are O(len)).
                # The first ~2k chars of a Vietnamese article reliably
                # contain the subject ("người điều khiển xe mô tô...") and
                # the penalty band, which is what the reranker needs.
                body = parent.body[:2000]
                return f"{c.hierarchical_path}\n{body}"
            return (c.metadata or {}).get("text") or c.body or ""

        documents = [_doc_text(c) for c in candidates]
        try:
            scores = await self._deps.reranker.rerank(
                query=query,
                documents=documents,
                top_n=None,
            )
        except Exception as exc:
            log.warning("rerank.failed_fallback", error=str(exc))
            return list(candidates)[: self._settings.reranker_top_k]

        # Blend the cross-encoder score with the candidate's *retrieval*
        # rank. The Qwen3 reranker under vLLM is poorly calibrated for a
        # handful of documents — some unrelated texts score 0.7-0.8 against
        # ANY query. Those docs almost always sit deep in the hybrid
        # retrieval ranking, so adding a small reciprocal-rank prior from
        # retrieval ("this doc was the Nth best hybrid hit") demotes the
        # mis-calibrated outliers without hurting genuine top hits. The
        # candidates arrive pre-sorted by fusion, so their list index *is*
        # the retrieval rank.
        blend = max(0.0, min(1.0, self._settings.rerank_retrieval_blend))
        rrf_k = self._settings.hybrid_rrf_k
        threshold = self._settings.reranker_threshold
        scored: list[tuple[int, float, float]] = []  # (idx, rerank, blended)
        top_rerank_idx = scores[0][0] if scores else -1
        for idx, score in scores:
            retrieval_prior = 1.0 / (rrf_k + idx + 1)  # idx is 0-based rank
            blended = (1.0 - blend) * score + blend * retrieval_prior * rrf_k
            scored.append((idx, score, blended))

        # Keep candidates above the cross-encoder threshold (top-1 always
        # preserved), then order by the blended score.
        kept = [
            (idx, rer, bl)
            for (idx, rer, bl) in scored
            if rer >= threshold or idx == top_rerank_idx
        ]
        kept.sort(key=lambda t: (-t[2], candidates[t[0]].chunk_id))

        out: list[RetrievalCandidate] = []
        for idx, rer, _bl in kept:
            out.append(
                candidates[idx].model_copy(update={"score": rer, "source": "rerank"})
            )

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
