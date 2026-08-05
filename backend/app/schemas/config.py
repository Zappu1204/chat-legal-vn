"""System config schemas.

``SystemConfigRead`` masks secret fields (API keys) — it reports only whether
a key is set, never the value. ``SystemConfigUpdate`` accepts all tunable
fields as optional; secrets are write-only.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from ..models.system_config import SECRET_FIELDS, SystemConfig


class SystemConfigUpdate(BaseModel):
    llm_base_url: str | None = None
    llm_api_key: str | None = None  # write-only
    llm_model_name: str | None = None
    llm_temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    llm_max_tokens: int | None = Field(default=None, gt=0)
    llm_max_concurrency: int | None = Field(default=None, gt=0)

    embedding_base_url: str | None = None
    embedding_api_key: str | None = None  # write-only
    reranker_base_url: str | None = None
    reranker_api_key: str | None = None  # write-only

    hybrid_fusion: str | None = None
    hybrid_rrf_k: int | None = Field(default=None, gt=0)
    hybrid_alpha_dense: float | None = Field(default=None, ge=0.0, le=1.0)
    hybrid_alpha_sparse: float | None = Field(default=None, ge=0.0, le=1.0)
    retriever_top_k: int | None = Field(default=None, gt=0)
    reranker_top_k: int | None = Field(default=None, gt=0)
    reranker_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    diversity_max_per_parent: int | None = Field(default=None, ge=0)

    query_decompose_enabled: bool | None = None
    query_hyde_enabled: bool | None = None
    query_hyde_num: int | None = Field(default=None, ge=0)
    graph_expand_enabled: bool | None = None
    graph_expand_hops: int | None = Field(default=None, ge=0)
    graph_expand_limit: int | None = Field(default=None, ge=0)


class SystemConfigRead(BaseModel):
    """Admin-facing read view. Secrets reported as booleans only."""

    revision: int
    updated_at: datetime
    updated_by: str | None
    values: dict
    secrets_set: dict[str, bool]

    @classmethod
    def from_model(cls, m: SystemConfig) -> SystemConfigRead:
        values: dict = {}
        secrets_set: dict[str, bool] = {}
        for name in type(m).model_fields:
            if name in {"id", "revision", "updated_at", "updated_by"}:
                continue
            value = getattr(m, name)
            if name in SECRET_FIELDS:
                secrets_set[name] = bool(value)
            else:
                values[name] = value
        return cls(
            revision=m.revision,
            updated_at=m.updated_at,
            updated_by=m.updated_by,
            values=values,
            secrets_set=secrets_set,
        )
