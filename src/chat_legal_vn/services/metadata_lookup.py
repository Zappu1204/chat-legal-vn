"""Metadata-aware retrieval path.

When a user question carries a precise legal citation (a document number
together with one or more of ``Điều / Khoản / Điểm``) we bypass semantic
retrieval and pull the exact chunks straight from the lexical store.
The resulting candidate list is fused with the regular dense + BM25
results via RRF so it always lands at the top of the ranking even when
the cross-encoder rerankers can't tell the difference between two
neighbouring clauses.

References
----------
* HARP / RegLab "metadata-aware legal retrieval" (NeurIPS 2024) — pre-
  filters candidate chunks by document id and hierarchical path before
  running the dense retriever.
* IBM RAG-Legal pipeline — phrase boost on hierarchical headings is
  reported to recover ~25 pp recall@5 on citation-style questions.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..domain.entities import QueryFilter, RetrievalCandidate
from ..domain.ports import LexicalStoreAdapter
from ..infra.logging import get_logger

log = get_logger(__name__)


def _filter_with_path(qf: QueryFilter, substring: str) -> QueryFilter:
    out = qf.model_copy()
    out.path_substrings = [substring]
    return out


async def lookup_by_citation(
    *,
    lexical_store: LexicalStoreAdapter,
    query_filter: QueryFilter,
    top_k_per_path: int = 5,
    overall_cap: int = 30,
) -> list[RetrievalCandidate]:
    """Pull chunks that exactly match the citation in ``query_filter``.

    Each ``path_substring`` in the filter is searched independently so a
    multi-target citation (e.g. ``["Điều 7 > Khoản 2 > Điểm h", "… Điểm i"]``)
    returns chunks for *all* of the cited points, not just the first one
    that the lexical scorer prefers.

    Returns at most ``overall_cap`` candidates, deduplicated on
    ``chunk_id`` and tagged with ``source="metadata"``. The empty list is
    returned when the filter has no path anchor at all — callers should
    treat that as "no metadata-aware path applicable".
    """
    if not query_filter.path_substrings or not query_filter.so_ky_hieu:
        return []

    seen: set[str] = set()
    out: list[RetrievalCandidate] = []

    for substring in query_filter.path_substrings:
        scoped = _filter_with_path(query_filter, substring)
        try:
            hits = await lexical_store.search(
                query=substring,
                top_k=top_k_per_path,
                filter=scoped,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "metadata_lookup.search_failed",
                substring=substring,
                error=str(exc),
            )
            continue

        for hit in hits:
            if hit.chunk_id in seen:
                continue
            seen.add(hit.chunk_id)
            # Re-tag so downstream fusion / logs can recognise the source.
            out.append(hit.model_copy(update={"source": "metadata"}))
            if len(out) >= overall_cap:
                return out
    return out


def filter_in_path(
    candidates: Sequence[RetrievalCandidate],
    substrings: Sequence[str],
) -> list[RetrievalCandidate]:
    """Filter ``candidates`` to those whose ``hierarchical_path`` contains
    any of ``substrings``. Used as a post-filter when we want to keep
    only retrieval results that exactly match the cited clause.
    """
    if not substrings:
        return list(candidates)
    norm_subs = [s.lower() for s in substrings]
    out: list[RetrievalCandidate] = []
    for c in candidates:
        path = (c.hierarchical_path or "").lower()
        if any(s in path for s in norm_subs):
            out.append(c)
    return out


__all__ = ["filter_in_path", "lookup_by_citation"]


# ----- compatibility re-exports for static analysers -----------------------
_ = Any  # silence unused-import lints when this file is imported elsewhere
