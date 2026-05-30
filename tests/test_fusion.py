"""Property-based tests for RRF and alpha fusion."""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from chat_legal_vn.domain.entities import RetrievalCandidate
from chat_legal_vn.services.fusion import alpha_fusion, rrf


def _cands(seed: int, n: int) -> list[RetrievalCandidate]:
    return [
        RetrievalCandidate(
            chunk_id=f"c-{seed}-{i}",
            parent_chunk_id=f"p-{seed}-{i}",
            document_id=seed * 100 + i,
            hierarchical_path="x",
            body="b",
            score=float(n - i),
            source="dense",
        )
        for i in range(n)
    ]


def test_rrf_identity_with_empty_list() -> None:
    a = _cands(1, 5)
    fused = rrf(lists=[a, []], k=60)
    assert {c.chunk_id for c in fused} == {c.chunk_id for c in a}


@given(
    st.integers(min_value=1, max_value=10),
    st.integers(min_value=1, max_value=10),
)
@settings(max_examples=50)
def test_rrf_commutativity(n_a: int, n_b: int) -> None:
    a = _cands(1, n_a)
    b = _cands(2, n_b)
    fused_ab = rrf(lists=[a, b], k=60)
    fused_ba = rrf(lists=[b, a], k=60)
    # Same set and same scores
    assert {c.chunk_id for c in fused_ab} == {c.chunk_id for c in fused_ba}
    score_ab = {c.chunk_id: c.score for c in fused_ab}
    score_ba = {c.chunk_id: c.score for c in fused_ba}
    for k in score_ab:
        assert abs(score_ab[k] - score_ba[k]) < 1e-9


def test_alpha_fusion_handles_disjoint_lists() -> None:
    a = _cands(1, 3)
    b = _cands(2, 3)
    fused = alpha_fusion(dense=a, sparse=b, alpha_dense=0.6, alpha_sparse=0.4)
    ids = {c.chunk_id for c in fused}
    assert ids == {c.chunk_id for c in a + b}
