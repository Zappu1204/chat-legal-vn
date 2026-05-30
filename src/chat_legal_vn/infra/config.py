"""Configuration loaded from environment variables.

The `.env` file is loaded by `pydantic-settings` at process startup. The
file itself is **never read or echoed** by application code. Values
are referenced through the `Settings` instance returned by `get_settings()`.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Strongly-typed view over the `.env` configuration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ----- LLM ---------------------------------------------------------------
    llm_base_url: str = Field(...)
    llm_api_key: SecretStr = Field(...)
    llm_model_name: str = Field(...)
    llm_temperature: float = 0.1
    llm_max_tokens: int = 2048
    llm_timeout_seconds: float = 60.0
    llm_max_retries: int = 6
    llm_retry_min_seconds: float = 1.0
    llm_retry_max_seconds: float = 30.0
    # Bounded concurrency on the upstream LLM. MIMO returns 400 with body
    # `{"error": {"code": "429", "type": "router_queue_limitation"}}` when
    # its router queue is full; capping in-flight requests keeps us under
    # the queue admission limit and avoids thundering-herd retries.
    llm_max_concurrency: int = 4

    # ----- Embedding ---------------------------------------------------------
    embedding_base_url: str = Field(...)
    embedding_api_key: SecretStr = Field(...)
    embedding_model_name: str = Field(...)
    embedding_dim: int = 4096
    embedding_batch_size: int = 64
    embedding_concurrency: int = 4
    embedding_max_input_tokens: int = 8192
    embedding_timeout_seconds: float = 60.0
    embedding_max_retries: int = 4
    embedding_task_instruction: str = (
        "Given a Vietnamese legal question, retrieve relevant Vietnamese legal "
        "documents (laws, decrees, circulars, decisions) that answer the question."
    )

    # ----- Reranker ----------------------------------------------------------
    reranker_base_url: str = Field(...)
    reranker_api_key: SecretStr = Field(...)
    reranker_model_name: str = Field(...)
    reranker_max_input_tokens: int = 32768
    reranker_timeout_seconds: float = 60.0
    reranker_batch_size: int = 32
    reranker_task_instruction: str = (
        "Given a Vietnamese legal question, judge whether the candidate "
        "Vietnamese legal passage contains the rule that answers the question."
    )

    # ----- Vector store ------------------------------------------------------
    vector_store_type: Literal["qdrant"] = "qdrant"
    qdrant_host: str = "localhost"
    qdrant_port: int = 6333
    qdrant_grpc_port: int = 6334
    qdrant_api_key: SecretStr = SecretStr("")
    qdrant_use_https: bool = False
    qdrant_prefer_grpc: bool = True
    qdrant_upsert_batch_size: int = 128
    qdrant_upsert_max_retries: int = 4
    collection_name: str = "vn_legal_docs"

    # ----- Lexical store -----------------------------------------------------
    elasticsearch_host: str = "http://localhost:9200"
    elasticsearch_user: str = "elastic"
    elasticsearch_password: SecretStr = SecretStr("")
    elasticsearch_verify_certs: bool = False
    elasticsearch_bulk_chunk_size: int = 256
    elasticsearch_disable_refresh_during_ingest: bool = True
    index_name: str = "vn_legal_lexical"

    # ----- Graph store -------------------------------------------------------
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: SecretStr = SecretStr("")
    neo4j_database: str = "neo4j"

    # ----- Retrieval pipeline ------------------------------------------------
    hybrid_fusion: Literal["rrf", "alpha"] = "rrf"
    hybrid_rrf_k: int = 60
    hybrid_alpha_dense: float = 0.7
    hybrid_alpha_sparse: float = 0.3
    retriever_top_k: int = 40
    reranker_top_k: int = 5
    reranker_threshold: float = 0.7
    diversity_max_per_parent: int = 2

    # ----- Ingest pipeline ---------------------------------------------------
    # Number of flush jobs (embed → upsert across stores) that may run
    # concurrently. Each flush already parallelises store writes; this
    # additional concurrency overlaps the next flush's embedding call with
    # the previous flush's upserts. Set to 1 to disable.
    ingest_flush_concurrency: int = 2
    # Maximum number of in-flight flush tasks the producer may hold. When
    # this many tasks are queued the parser blocks until one completes,
    # bounding peak RAM.
    ingest_flush_queue_size: int = 4

    # ----- Query planner -----------------------------------------------------
    query_decompose_enabled: bool = True
    query_hyde_enabled: bool = True
    query_hyde_num: int = 1

    # ----- GraphRAG ----------------------------------------------------------
    graph_expand_enabled: bool = True
    graph_expand_hops: int = 1
    graph_expand_limit: int = 10

    # ----- Versions ----------------------------------------------------------
    chunking_version: str = "v1"
    embedding_version: str = "qwen3-emb-8b-4096-v1"
    prompt_version: str = "irac-v1"
    policy_version: str = "hybrid-rrf-rerank-v1"

    # ----- App ---------------------------------------------------------------
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    data_dir: Path = Path("data/vietnamese-legal-documents/data")
    http_host: str = "0.0.0.0"
    http_port: int = 8080

    # ----- Validators --------------------------------------------------------
    @field_validator("hybrid_alpha_dense", "hybrid_alpha_sparse", "reranker_threshold")
    @classmethod
    def _unit_interval(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            msg = f"value must be in [0, 1]; got {v!r}"
            raise ValueError(msg)
        return v

    @field_validator("retriever_top_k", "reranker_top_k", "embedding_batch_size")
    @classmethod
    def _positive_int(cls, v: int) -> int:
        if v <= 0:
            msg = f"value must be a positive integer; got {v!r}"
            raise ValueError(msg)
        return v

    # Convenience -------------------------------------------------------------
    @property
    def qdrant_url(self) -> str:
        scheme = "https" if self.qdrant_use_https else "http"
        return f"{scheme}://{self.qdrant_host}:{self.qdrant_port}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached Settings instance.

    Pydantic raises a `ValidationError` if a required field is missing.
    The error message identifies the offending field name without echoing
    the value of any other secret-bearing variable.
    """
    return Settings()  # type: ignore[call-arg]
