"""Dynamic system configuration.

A single-row (singleton) table holding the tunable RAG parameters that an
admin can change at runtime. Secret-bearing fields (API keys) are stored in
dedicated columns and never serialized back to non-admin clients. When this
row changes, the ``ConfigService`` rebuilds the adapter registry so new
requests pick up the values without a server restart.

Only fields that are *safe and useful* to tune at runtime live here. Host /
port / credentials for the backing stores (Qdrant, ES, Neo4j) stay in the
process environment because changing them implies re-indexing and is not a
hot-reloadable operation.
"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, SQLModel

from ._types import tz_column, utcnow


# A fixed primary key so there is always exactly one config row.
SINGLETON_ID = 1


class SystemConfig(SQLModel, table=True):
    __tablename__ = "system_config"

    id: int = Field(default=SINGLETON_ID, primary_key=True)

    # ----- LLM (generation) --------------------------------------------------
    llm_base_url: str | None = None
    llm_api_key: str | None = None  # secret; write-only to clients
    llm_model_name: str | None = None
    llm_temperature: float | None = None
    llm_max_tokens: int | None = None
    llm_max_concurrency: int | None = None

    # ----- Embedding / Reranker endpoints -----------------------------------
    embedding_base_url: str | None = None
    embedding_api_key: str | None = None  # secret
    reranker_base_url: str | None = None
    reranker_api_key: str | None = None  # secret

    # ----- Retrieval tuning --------------------------------------------------
    hybrid_fusion: str | None = None  # "rrf" | "alpha"
    hybrid_rrf_k: int | None = None
    hybrid_alpha_dense: float | None = None
    hybrid_alpha_sparse: float | None = None
    retriever_top_k: int | None = None
    reranker_top_k: int | None = None
    reranker_threshold: float | None = None
    diversity_max_per_parent: int | None = None

    # ----- Query planner / GraphRAG -----------------------------------------
    query_decompose_enabled: bool | None = None
    query_hyde_enabled: bool | None = None
    query_hyde_num: int | None = None
    graph_expand_enabled: bool | None = None
    graph_expand_hops: int | None = None
    graph_expand_limit: int | None = None

    # ----- Bookkeeping -------------------------------------------------------
    updated_at: datetime = Field(default_factory=utcnow, sa_column=tz_column())
    updated_by: str | None = None
    # Bumped on every successful hot-reload so clients can detect changes.
    revision: int = Field(default=0)


# Field groups used by the ConfigService to map config → core Settings.
SECRET_FIELDS: frozenset[str] = frozenset(
    {"llm_api_key", "embedding_api_key", "reranker_api_key"}
)

TUNABLE_FIELDS: tuple[str, ...] = (
    "llm_base_url",
    "llm_api_key",
    "llm_model_name",
    "llm_temperature",
    "llm_max_tokens",
    "llm_max_concurrency",
    "embedding_base_url",
    "embedding_api_key",
    "reranker_base_url",
    "reranker_api_key",
    "hybrid_fusion",
    "hybrid_rrf_k",
    "hybrid_alpha_dense",
    "hybrid_alpha_sparse",
    "retriever_top_k",
    "reranker_top_k",
    "reranker_threshold",
    "diversity_max_per_parent",
    "query_decompose_enabled",
    "query_hyde_enabled",
    "query_hyde_num",
    "graph_expand_enabled",
    "graph_expand_hops",
    "graph_expand_limit",
)
