"""Domain entities and value objects for the Vietnamese Legal RAG system.

These classes are pure data; they hold no behaviour beyond validation and
trivial helpers, and they import nothing from vendor SDKs. The application
and adapter layers convert these to/from external representations.
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Effective status (normalized from `tinh_trang_hieu_luc`)
# ---------------------------------------------------------------------------


class EffectiveStatus(str, Enum):  # noqa: UP042 — pydantic + JSON serialization friendly
    EFFECTIVE = "effective"
    PARTIALLY_EXPIRED = "partially_expired"
    EXPIRED = "expired"
    NOT_YET_EFFECTIVE = "not_yet_effective"
    UNKNOWN = "unknown"


# Lookup table mandated by Requirement 1 (Corpus normalization).
TINH_TRANG_LOOKUP: dict[str, EffectiveStatus] = {
    "Còn hiệu lực": EffectiveStatus.EFFECTIVE,
    "Hết hiệu lực toàn bộ": EffectiveStatus.EXPIRED,
    "Hết hiệu lực một phần": EffectiveStatus.PARTIALLY_EXPIRED,
    "Chưa có hiệu lực": EffectiveStatus.NOT_YET_EFFECTIVE,
}


# ---------------------------------------------------------------------------
# Documents and parsed nodes
# ---------------------------------------------------------------------------

NodeLevel = Literal["phan", "chuong", "muc", "dieu", "khoan", "diem"]


class LegalDocument(BaseModel):
    """A normalized row from the `metadata` config."""

    model_config = ConfigDict(frozen=True)

    document_id: int
    title: str
    so_ky_hieu: str | None = None
    loai_van_ban: str | None = None
    co_quan_ban_hanh: str | None = None
    nganh: str | None = None
    linh_vuc: str | None = None
    nguoi_ky: str | None = None
    chuc_danh: str | None = None
    pham_vi: str | None = None
    ngay_ban_hanh: date | None = None
    ngay_co_hieu_luc: date | None = None
    ngay_het_hieu_luc: date | None = None
    tinh_trang_hieu_luc: str | None = None
    effective_status: EffectiveStatus = EffectiveStatus.UNKNOWN
    has_full_text: bool = False
    raw_dates: dict[str, str] = Field(default_factory=dict)


class LegalNode(BaseModel):
    """A structural node produced by parsing `content_html`."""

    model_config = ConfigDict(frozen=False)

    node_id: str
    document_id: int
    level: NodeLevel
    number: str | None = None  # e.g. "5", "1", "a"
    heading: str | None = None  # e.g. "Điều 5. Quyền của công dân..."
    body: str = ""
    hierarchical_path: str = ""
    children: list[LegalNode] = Field(default_factory=list)
    sequence_index: int = 0
    parse_warnings: list[str] = Field(default_factory=list)


LegalNode.model_rebuild()


# ---------------------------------------------------------------------------
# Chunks
# ---------------------------------------------------------------------------


class ChunkMetadata(BaseModel):
    model_config = ConfigDict(frozen=True)

    document_id: int
    so_ky_hieu: str | None
    loai_van_ban: str | None
    co_quan_ban_hanh: str | None
    nganh: str | None
    linh_vuc: str | None
    ngay_ban_hanh: str | None  # ISO 8601
    ngay_co_hieu_luc: str | None
    ngay_het_hieu_luc: str | None
    effective_status: EffectiveStatus
    hierarchical_path: str
    chunking_version: str
    embedding_version: str


class ParentChunk(BaseModel):
    """An Article-level (`dieu`) chunk used as LLM context."""

    model_config = ConfigDict(frozen=True)

    parent_chunk_id: str
    document_id: int
    hierarchical_path: str
    heading: str | None
    body: str
    summary: str = ""
    metadata: ChunkMetadata


class ChildChunk(BaseModel):
    """A Clause/Point-level (`khoan`/`diem`) chunk used as the retrieval unit."""

    model_config = ConfigDict(frozen=True)

    chunk_id: str
    parent_chunk_id: str
    document_id: int
    hierarchical_path: str
    body: str
    text_for_embedding: str  # body prefixed with hierarchical header
    sequence_index: int = 0
    split_index: int = 0  # >0 if a long chunk was split along sentence boundaries
    metadata: ChunkMetadata


# ---------------------------------------------------------------------------
# Retrieval and answers
# ---------------------------------------------------------------------------


class RetrievalCandidate(BaseModel):
    """A single retrieval hit, vendor-neutral."""

    model_config = ConfigDict(frozen=False)

    chunk_id: str
    parent_chunk_id: str
    document_id: int
    hierarchical_path: str
    body: str
    score: float
    source: str  # "dense", "sparse", "rrf", "rerank", "graph"
    metadata: dict[str, Any] = Field(default_factory=dict)


class QueryFilter(BaseModel):
    """Structured metadata filter extracted by the query planner."""

    model_config = ConfigDict(frozen=False)

    so_ky_hieu: str | None = None
    loai_van_ban: list[str] = Field(default_factory=list)
    co_quan_ban_hanh: list[str] = Field(default_factory=list)
    year_min: int | None = None
    year_max: int | None = None
    effective_only: bool = False


QueryIntent = Literal[
    "lookup_article",
    "penalty",
    "procedure",
    "definition",
    "comparison",
    "effective_status",
    "other",
]


class SubQuery(BaseModel):
    text: str
    intent: QueryIntent = "other"
    filter: QueryFilter = Field(default_factory=QueryFilter)
    is_hyde: bool = False


class QueryPlan(BaseModel):
    """The output of the Query Planner."""

    model_config = ConfigDict(frozen=False)

    original: str
    normalized: str
    intent: QueryIntent
    sub_queries: list[SubQuery] = Field(default_factory=list)
    hyde_passages: list[str] = Field(default_factory=list)
    filter: QueryFilter = Field(default_factory=QueryFilter)


class Citation(BaseModel):
    """Canonical legal citation."""

    model_config = ConfigDict(frozen=True)

    loai_van_ban: str | None
    so_ky_hieu: str | None
    dieu: str | None
    khoan: str | None = None
    diem: str | None = None
    document_id: int | None = None  # filled by Citation_Resolver

    def canonical(self) -> str:
        parts: list[str] = []
        if self.loai_van_ban:
            parts.append(self.loai_van_ban)
        if self.so_ky_hieu:
            parts.append(self.so_ky_hieu)
        head = " ".join(parts)
        tail: list[str] = []
        if self.dieu:
            tail.append(f"Điều {self.dieu}")
        if self.khoan:
            tail.append(f"Khoản {self.khoan}")
        if self.diem:
            tail.append(f"Điểm {self.diem}")
        body = " - ".join(tail)
        if head and body:
            return f"[{head} - {body}]"
        return f"[{head or body}]"


class IRACIssue(BaseModel):
    issue: str
    rule: str
    application: str
    conclusion: str
    citations: list[str] = Field(default_factory=list)


class Answer(BaseModel):
    """Final user-facing answer."""

    model_config = ConfigDict(frozen=False)

    short_answer: str
    issues: list[IRACIssue] = Field(default_factory=list)
    legal_basis: list[str] = Field(default_factory=list)
    analysis: str = ""
    exceptions_or_conditions: str = ""
    final_conclusion: str = ""
    citations: list[Citation] = Field(default_factory=list)
    confidence: float = 0.0
    needs_human_review: bool = False
    disclaimer: str = (
        "Thông tin này không thay thế tư vấn pháp lý chuyên nghiệp. "
        "Vui lòng tham khảo luật sư đối với các tình huống cụ thể."
    )
    warnings: list[str] = Field(default_factory=list)
