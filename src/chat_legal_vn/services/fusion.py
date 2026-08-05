"""Result fusion: Reciprocal Rank Fusion (RRF) and alpha-fusion."""

from __future__ import annotations

from collections.abc import Sequence

from ..domain.entities import RetrievalCandidate


def rrf(
    *,
    lists: Sequence[Sequence[RetrievalCandidate]],
    k: int = 60,
) -> list[RetrievalCandidate]:
    """Reciprocal Rank Fusion.

    For a candidate appearing at rank `r` (1-indexed) in list `i`, its
    contribution is `1 / (k + r)`. Final score is the sum across all lists.
    """
    if not lists:
        return []

    scores: dict[str, float] = {}
    by_id: dict[str, RetrievalCandidate] = {}
    for cand_list in lists:
        for rank, cand in enumerate(cand_list, start=1):
            scores[cand.chunk_id] = scores.get(cand.chunk_id, 0.0) + 1.0 / (k + rank)
            # Prefer the highest-original-score copy when duplicates appear.
            existing = by_id.get(cand.chunk_id)
            if existing is None or cand.score > existing.score:
                by_id[cand.chunk_id] = cand

    fused: list[RetrievalCandidate] = []
    for chunk_id, fused_score in scores.items():
        c = by_id[chunk_id].model_copy(update={"score": fused_score, "source": "rrf"})
        fused.append(c)
    # Sort by score desc, then by chunk_id asc as a deterministic
    # tie-breaker. RRF produces many exact-tie scores (1/(k+r) collides
    # across lists); without a stable secondary key the final order would
    # depend on dict insertion order — which itself depends on the
    # (non-deterministic) retrieval order — making the whole pipeline
    # unstable run-to-run.
    fused.sort(key=lambda x: (-x.score, x.chunk_id))
    return fused


def alpha_fusion(
    *,
    dense: Sequence[RetrievalCandidate],
    sparse: Sequence[RetrievalCandidate],
    alpha_dense: float,
    alpha_sparse: float,
) -> list[RetrievalCandidate]:
    """Linear combination after min-max normalization of each list."""

    def _normalize(items: Sequence[RetrievalCandidate]) -> dict[str, float]:
        if not items:
            return {}
        scores = [c.score for c in items]
        s_min, s_max = min(scores), max(scores)
        rng = s_max - s_min or 1.0
        return {c.chunk_id: (c.score - s_min) / rng for c in items}

    nd = _normalize(dense)
    ns = _normalize(sparse)

    by_id: dict[str, RetrievalCandidate] = {}
    for c in list(dense) + list(sparse):
        if c.chunk_id not in by_id:
            by_id[c.chunk_id] = c

    fused: list[RetrievalCandidate] = []
    for chunk_id, c in by_id.items():
        score = alpha_dense * nd.get(chunk_id, 0.0) + alpha_sparse * ns.get(chunk_id, 0.0)
        fused.append(c.model_copy(update={"score": score, "source": "rrf"}))
    fused.sort(key=lambda x: (-x.score, x.chunk_id))
    return fused
