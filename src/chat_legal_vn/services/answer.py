"""Answer Engine: builds the IRAC-structured answer with strict citations.

Heavy lifting is performed by the LLM; this module is responsible for
- packaging Evidence
- prompting in JSON mode
- repair-once on schema failure
- mapping the structured response into the domain `Answer`
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..domain.entities import (
    Answer,
    Citation,
    EffectiveStatus,
    IRACIssue,
    ParentChunk,
    RetrievalCandidate,
)
from ..domain.ports import LLMAdapter
from ..infra.logging import get_logger
from ..prompts.templates import ANSWER_SYSTEM, ANSWER_USER
from .citation import parse_citations

log = get_logger(__name__)


def _format_evidence(
    candidates: Sequence[RetrievalCandidate],
    parents: dict[str, ParentChunk],
) -> str:
    lines: list[str] = []
    for i, cand in enumerate(candidates, start=1):
        parent = parents.get(cand.parent_chunk_id)
        meta = cand.metadata or {}
        loai = meta.get("loai_van_ban") or "—"
        skh = meta.get("so_ky_hieu") or "—"
        status = meta.get("effective_status") or EffectiveStatus.UNKNOWN.value
        path = cand.hierarchical_path
        summary = parent.summary if parent else ""
        body = cand.body or (parent.body if parent else "")
        head = (
            f"[{i}] {loai} {skh} | {path} | effective_status={status}"
        )
        block = head + (
            f"\nTóm tắt Điều: {summary}" if summary else ""
        ) + f"\nNội dung: {body}"
        lines.append(block)
    return "\n\n".join(lines)


def _map_response(payload: dict[str, Any]) -> Answer:
    issues_raw = payload.get("issues") or []
    issues: list[IRACIssue] = []
    for it in issues_raw:
        if not isinstance(it, dict):
            continue
        issues.append(
            IRACIssue(
                issue=str(it.get("issue", "")),
                rule=str(it.get("rule", "")),
                application=str(it.get("application", "")),
                conclusion=str(it.get("conclusion", "")),
                citations=[str(x) for x in (it.get("citations") or []) if x],
            )
        )

    text_blob = " ".join(
        [
            payload.get("short_answer", "") or "",
            *[i.rule for i in issues],
            *[i.application for i in issues],
            *[i.conclusion for i in issues],
            payload.get("analysis", "") or "",
            payload.get("final_conclusion", "") or "",
            *[c for c in (payload.get("legal_basis") or []) if isinstance(c, str)],
            *[c for issue in issues_raw for c in (issue.get("citations") or [])
              if isinstance(c, str)],
        ]
    )
    citations = parse_citations(text_blob)

    return Answer(
        short_answer=str(payload.get("short_answer", "")),
        issues=issues,
        legal_basis=[str(x) for x in (payload.get("legal_basis") or []) if x],
        analysis=str(payload.get("analysis", "")),
        exceptions_or_conditions=str(payload.get("exceptions_or_conditions", "")),
        final_conclusion=str(payload.get("final_conclusion", "")),
        citations=citations,
        confidence=float(payload.get("confidence", 0.0) or 0.0),
        needs_human_review=bool(payload.get("needs_human_review", False)),
        warnings=[str(x) for x in (payload.get("warnings") or []) if x],
    )


class AnswerEngine:
    def __init__(self, *, llm: LLMAdapter) -> None:
        self._llm = llm

    async def answer(
        self,
        *,
        query: str,
        candidates: Sequence[RetrievalCandidate],
        parents: dict[str, ParentChunk],
    ) -> Answer:
        evidence = _format_evidence(candidates, parents)
        if not evidence:
            return Answer(
                short_answer="Không tìm thấy quy định đủ căn cứ trong dữ liệu.",
                final_conclusion=(
                    "Hệ thống chưa truy hồi được tài liệu pháp luật phù hợp."
                ),
                needs_human_review=True,
                confidence=0.0,
                warnings=["no_evidence"],
            )

        try:
            payload = await self._llm.complete_json(
                system=ANSWER_SYSTEM,
                user=ANSWER_USER.format(evidence=evidence, query=query),
                temperature=0.0,
                max_tokens=2048,
            )
        except Exception as exc:
            log.error("answer.llm_failed", error=str(exc))
            return Answer(
                short_answer="Hệ thống tạm thời không thể tạo câu trả lời.",
                final_conclusion="",
                needs_human_review=True,
                confidence=0.0,
                warnings=[f"llm_error: {exc!s}"],
            )

        answer = _map_response(payload)

        # Effective-status warning: if any retained candidate is non-effective.
        statuses = {
            (c.metadata or {}).get("effective_status", EffectiveStatus.UNKNOWN.value)
            for c in candidates
        }
        if statuses and EffectiveStatus.EFFECTIVE.value not in statuses:
            answer.warnings.append("all_retained_chunks_non_effective")
            answer.needs_human_review = True
        elif any(s != EffectiveStatus.EFFECTIVE.value for s in statuses):
            answer.warnings.append("some_retained_chunks_non_effective")

        return answer

    @staticmethod
    def attach_resolved_citations(
        answer: Answer,
        resolved: dict[str, Citation],
    ) -> Answer:
        """Replace string citations with `Citation` objects when resolved."""
        out: list[Citation] = []
        for parsed in answer.citations:
            key = parsed.canonical()
            full = resolved.get(key, parsed)
            out.append(full)
        answer.citations = out
        return answer
