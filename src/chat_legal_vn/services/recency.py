"""Effective-status aware re-scoring.

Vietnamese legal corpora contain many *superseded* versions of the same
subject matter. A query like "mức phạt vượt đèn đỏ" is semantically
equally close to the 2003, 2010, 2013, 2016, 2019 and 2024 traffic
decrees, so a pure dense/cross-encoder ranking routinely surfaces an
**expired** decree above the one currently in force. The downstream
answer engine, told to answer only from the evidence, then either cites
a repealed rule or declares "không tìm thấy".

This module applies a multiplicative *status weight* to each candidate's
relevance score so in-force law is preferred, while keeping expired text
in the pool (with a lower weight) so genuinely historical questions still
retrieve their answer.

The transform is monotonic within a status class — it never reorders two
candidates that share the same ``effective_status`` — so it composes
cleanly after the cross-encoder.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..domain.entities import EffectiveStatus, RetrievalCandidate
from ..infra.config import Settings


def status_weight(settings: Settings, status: str | None) -> float:
    """Return the multiplicative weight for an ``effective_status`` value."""
    mapping = {
        EffectiveStatus.EFFECTIVE.value: settings.effective_weight_effective,
        EffectiveStatus.PARTIALLY_EXPIRED.value: settings.effective_weight_partially_expired,
        EffectiveStatus.NOT_YET_EFFECTIVE.value: settings.effective_weight_not_yet_effective,
        EffectiveStatus.EXPIRED.value: settings.effective_weight_expired,
        EffectiveStatus.UNKNOWN.value: settings.effective_weight_unknown,
    }
    return mapping.get(status or EffectiveStatus.UNKNOWN.value, settings.effective_weight_unknown)


def apply_effective_bias(
    settings: Settings,
    candidates: Sequence[RetrievalCandidate],
) -> list[RetrievalCandidate]:
    """Re-score and re-sort ``candidates`` by status-weighted relevance.

    Each candidate's score is multiplied by its status weight. The list is
    then re-sorted descending with a deterministic ``chunk_id`` tie-break.
    Candidates keep their (now weighted) score so later stages and logs
    reflect the adjustment.
    """
    if not settings.effective_bias_enabled or not candidates:
        return list(candidates)

    rescored: list[RetrievalCandidate] = []
    for c in candidates:
        status = (c.metadata or {}).get("effective_status")
        weight = status_weight(settings, status)
        rescored.append(c.model_copy(update={"score": c.score * weight}))

    rescored.sort(key=lambda x: (-x.score, x.chunk_id))
    return rescored


__all__ = ["apply_effective_bias", "status_weight"]
