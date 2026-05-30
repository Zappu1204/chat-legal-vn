"""Query Planner.

Performs:
- Unicode NFC normalization
- Intent classification + decomposition (LLM-driven, with safe fallback)
- Metadata-filter extraction (regex)
- HyDE expansion (optional, via LLM)
"""

from __future__ import annotations

import re
import unicodedata

from ..domain.entities import QueryFilter, QueryIntent, QueryPlan, SubQuery
from ..domain.ports import LLMAdapter
from ..infra.logging import get_logger
from ..prompts.templates import (
    HYDE_SYSTEM,
    HYDE_USER,
    QUERY_PLANNER_SYSTEM,
    QUERY_PLANNER_USER,
)

log = get_logger(__name__)


_VALID_INTENTS: set[QueryIntent] = {
    "lookup_article",
    "penalty",
    "procedure",
    "definition",
    "comparison",
    "effective_status",
    "other",
}

_SO_KY_HIEU_RE = re.compile(r"\b(\d+\s*[/\-][^\s\-,;.]+)\b")
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
_LOAI_RE = re.compile(
    r"\b(Luật|Bộ luật|Nghị định|Nghị quyết|Thông tư|Quyết định|Pháp lệnh|Hiến pháp|Lệnh|Chỉ thị|Công văn)\b",
    re.IGNORECASE,
)
_EFFECTIVE_HINTS = ("còn hiệu lực", "hiện hành", "đang áp dụng")


def normalize_query(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    return re.sub(r"\s+", " ", text).strip()


def extract_filter_heuristic(text: str) -> QueryFilter:
    f = QueryFilter()
    if m := _SO_KY_HIEU_RE.search(text):
        f.so_ky_hieu = m.group(1).replace(" ", "")
    f.loai_van_ban = sorted({m.group(1).capitalize() for m in _LOAI_RE.finditer(text)})
    years = [int(m.group(0)) for m in _YEAR_RE.finditer(text)]
    if years:
        f.year_min = min(years)
        f.year_max = max(years)
    lower = text.lower()
    if any(h in lower for h in _EFFECTIVE_HINTS):
        f.effective_only = True
    return f


class QueryPlanner:
    """Plans an incoming query into sub-queries plus optional HyDE expansions."""

    def __init__(
        self,
        *,
        llm: LLMAdapter,
        decompose_enabled: bool = True,
        hyde_enabled: bool = True,
        hyde_num: int = 1,
    ) -> None:
        self._llm = llm
        self._decompose_enabled = decompose_enabled
        self._hyde_enabled = hyde_enabled
        self._hyde_num = max(0, hyde_num)

    async def plan(self, query: str) -> QueryPlan:
        normalized = normalize_query(query)
        regex_filter = extract_filter_heuristic(normalized)

        intent: QueryIntent = "other"
        sub_queries: list[SubQuery] = [
            SubQuery(text=normalized, intent="other", filter=regex_filter)
        ]

        if self._decompose_enabled:
            try:
                payload = await self._llm.complete_json(
                    system=QUERY_PLANNER_SYSTEM,
                    user=QUERY_PLANNER_USER.format(query=normalized),
                    temperature=0.0,
                    max_tokens=512,
                )
                intent_raw = payload.get("intent", "other")
                if intent_raw in _VALID_INTENTS:
                    intent = intent_raw  # type: ignore[assignment]
                planner_filter = self._merge_filter(
                    regex_filter, payload.get("filter") or {}
                )
                raw_subs = payload.get("sub_queries") or []
                planned: list[SubQuery] = []
                for s in raw_subs:
                    text = (s.get("text") or "").strip()
                    if not text:
                        continue
                    s_intent = s.get("intent", intent)
                    if s_intent not in _VALID_INTENTS:
                        s_intent = intent
                    planned.append(
                        SubQuery(text=text, intent=s_intent, filter=planner_filter)
                    )
                if planned:
                    sub_queries = planned
                else:
                    sub_queries = [
                        SubQuery(text=normalized, intent=intent, filter=planner_filter)
                    ]
            except Exception as exc:
                log.warning("query_planner.fallback", error=str(exc))

        hyde_passages: list[str] = []
        if self._hyde_enabled and self._hyde_num > 0:
            for _ in range(self._hyde_num):
                try:
                    passage = await self._llm.complete(
                        system=HYDE_SYSTEM,
                        user=HYDE_USER.format(query=normalized),
                        temperature=0.2,
                        max_tokens=256,
                    )
                    if passage.strip():
                        hyde_passages.append(passage.strip())
                except Exception as exc:
                    log.warning("hyde.failed", error=str(exc))
                    break

        return QueryPlan(
            original=query,
            normalized=normalized,
            intent=intent,
            sub_queries=sub_queries,
            hyde_passages=hyde_passages,
            filter=sub_queries[0].filter if sub_queries else regex_filter,
        )

    @staticmethod
    def _merge_filter(base: QueryFilter, planner: dict[str, object]) -> QueryFilter:
        out = base.model_copy()
        if isinstance(planner.get("so_ky_hieu"), str):
            out.so_ky_hieu = planner["so_ky_hieu"]  # type: ignore[assignment]
        loai = planner.get("loai_van_ban")
        if isinstance(loai, list):
            out.loai_van_ban = [str(x) for x in loai if x]
        co_quan = planner.get("co_quan_ban_hanh")
        if isinstance(co_quan, list):
            out.co_quan_ban_hanh = [str(x) for x in co_quan if x]
        if isinstance(planner.get("year_min"), int):
            out.year_min = planner["year_min"]  # type: ignore[assignment]
        if isinstance(planner.get("year_max"), int):
            out.year_max = planner["year_max"]  # type: ignore[assignment]
        if isinstance(planner.get("effective_only"), bool):
            out.effective_only = planner["effective_only"]  # type: ignore[assignment]
        return out
