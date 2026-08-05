"""Query Planner.

Performs:
- Unicode NFC normalization
- Intent classification + decomposition (LLM-driven, with safe fallback)
- Metadata-filter extraction (regex)
- HyDE expansion (optional, via LLM)
- Citation parsing (so_ky_hieu / Điều / Khoản / Điểm) for metadata-aware
  retrieval. When the user's question references a specific clause
  (``"Điểm h, i Khoản 2 Điều 7 Nghị định 168/2024/NĐ-CP"``) we lift those
  anchors into the :class:`QueryFilter` so the retrieval pipeline can
  hard-filter on ``so_ky_hieu`` and boost by ``hierarchical_path``.
"""

from __future__ import annotations

import re
import unicodedata
from collections import OrderedDict

from ..domain.entities import QueryFilter, QueryIntent, QueryPlan, SubQuery
from ..domain.ports import LLMAdapter
from ..infra.logging import get_logger
from ..prompts.templates import (
    HYDE_SYSTEM,
    HYDE_USER,
    QUERY_PLANNER_SYSTEM,
    QUERY_PLANNER_USER,
)
from .citation_query import parse_query_citation

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
    """Build a :class:`QueryFilter` from a Vietnamese question via regex.

    Combines:

    * Loose lexical extraction (so_ky_hieu, year, document type,
      effective-status hints).
    * Structural citation parsing — ``Điều / Khoản / Điểm`` together with
      the matching ``hierarchical_path`` substrings — via
      :func:`citation_query.parse_query_citation`.

    These structural anchors enable the retrieval pipeline to *hard
    filter* by document number and to *boost* candidates whose
    ``hierarchical_path`` matches the cited clause.
    """
    f = QueryFilter()

    # ── coarse extraction ────────────────────────────────────────────────
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

    # ── citation parsing — overrides looser regex when present ───────────
    cit = parse_query_citation(text)
    if cit.so_ky_hieu:
        f.so_ky_hieu = cit.so_ky_hieu
    if cit.loai_van_ban and cit.loai_van_ban not in f.loai_van_ban:
        f.loai_van_ban = sorted({*f.loai_van_ban, cit.loai_van_ban})
    if cit.dieu:
        f.dieu = cit.dieu
    if cit.khoan:
        f.khoan = cit.khoan
    if cit.diem:
        f.diem = cit.diem
    f.path_substrings = cit.expand_paths()
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
        hyde_temperature: float = 0.0,
        cache_size: int = 256,
    ) -> None:
        self._llm = llm
        self._decompose_enabled = decompose_enabled
        self._hyde_enabled = hyde_enabled
        self._hyde_num = max(0, hyde_num)
        self._hyde_temperature = hyde_temperature
        self._cache_size = max(0, cache_size)
        # Insertion-ordered cache → cheap FIFO eviction. Keyed by the
        # normalised query so identical questions reuse the same plan and
        # become fully deterministic despite upstream LLM drift.
        self._cache: OrderedDict[str, QueryPlan] = OrderedDict()

    def _cache_get(self, key: str) -> QueryPlan | None:
        if self._cache_size == 0:
            return None
        plan = self._cache.get(key)
        if plan is not None:
            self._cache.move_to_end(key)
        return plan

    def _cache_put(self, key: str, plan: QueryPlan) -> None:
        if self._cache_size == 0:
            return
        self._cache[key] = plan
        self._cache.move_to_end(key)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)

    async def plan(self, query: str) -> QueryPlan:
        normalized = normalize_query(query)

        cached = self._cache_get(normalized)
        if cached is not None:
            log.info("query_planner.cache_hit", normalized=normalized)
            # Return a copy so callers can't mutate the cached instance.
            return cached.model_copy(deep=True)

        plan = await self._plan_uncached(normalized, original=query)
        self._cache_put(normalized, plan)
        return plan

    async def _plan_uncached(self, normalized: str, *, original: str) -> QueryPlan:
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
                        temperature=self._hyde_temperature,
                        max_tokens=256,
                    )
                    if passage.strip():
                        hyde_passages.append(passage.strip())
                except Exception as exc:
                    log.warning("hyde.failed", error=str(exc))
                    break

        return QueryPlan(
            original=original,
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
        # Structural anchors are always taken from the regex/citation
        # parser (``base``); the LLM is not asked for them and any
        # ``planner`` payload is ignored to avoid hallucinated overrides
        # that would silently dilute hard filters.
        out.dieu = base.dieu
        out.khoan = list(base.khoan)
        out.diem = list(base.diem)
        out.path_substrings = list(base.path_substrings)
        return out
