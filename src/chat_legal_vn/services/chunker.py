"""Hierarchical Parent-Child chunker.

- `Parent_Chunk` is produced once per Article (`dieu`).
- `Child_Chunk` is produced once per Clause (`khoan`) or Point (`diem`),
  and once per Article that has no Clause children.
- Every Child carries `parent_chunk_id` pointing at the enclosing Article.
- Every Child's `text_for_embedding` is prefixed with the Hierarchical Path
  and a short Article summary so semantic search has structural context.
- IDs are deterministic given the same input + `chunking_version`.
"""

from __future__ import annotations

import hashlib
import re
import uuid

from ..domain.entities import (
    ChildChunk,
    ChunkMetadata,
    EffectiveStatus,
    LegalDocument,
    LegalNode,
    ParentChunk,
)
from ..infra.tokens import estimate_tokens, max_chars_for_tokens

# ---------------------------------------------------------------------------
# IDs
# ---------------------------------------------------------------------------

# Stable namespace for chat-legal-vn chunk IDs. Generated once via
# `uuid.uuid5(uuid.NAMESPACE_URL, "chat-legal-vn/chunks")` and inlined here so
# the value is reproducible across processes.
_CHUNK_NAMESPACE = uuid.UUID("d2dc3f58-95f7-5b41-9e44-30b7d3e1bd71")


def _stable_id(*parts: str | int) -> str:
    """Return a deterministic UUID string for the given key parts.

    Qdrant point IDs must be either unsigned integers or RFC-4122 UUIDs;
    arbitrary hashes are rejected. Encoding SHA-1 of the joined parts as a
    UUID5 keeps the IDs deterministic across processes while satisfying
    Qdrant's format requirement.
    """
    raw = "|".join(str(p) for p in parts)
    return str(uuid.uuid5(_CHUNK_NAMESPACE, raw))


def _legacy_short_id(*parts: str | int) -> str:
    """The legacy non-UUID 24-hex form. Kept for migration / debugging."""
    raw = "|".join(str(p) for p in parts)
    return hashlib.sha1(raw.encode("utf-8"), usedforsecurity=False).hexdigest()[:24]


def parent_chunk_id(*, document_id: int, dieu_number: str | None, version: str) -> str:
    return _stable_id("parent", version, document_id, dieu_number or "0")


def child_chunk_id(
    *,
    document_id: int,
    dieu_number: str | None,
    khoan_number: str | None,
    diem_number: str | None,
    split_index: int,
    version: str,
) -> str:
    return _stable_id(
        "child",
        version,
        document_id,
        dieu_number or "0",
        khoan_number or "0",
        diem_number or "0",
        split_index,
    )


# ---------------------------------------------------------------------------
# Splitting
# ---------------------------------------------------------------------------


_SENTENCE_BOUNDARY = re.compile(r"(?<=[\.!?…])\s+")


def _approx_token_count(text: str) -> int:
    """Estimate token count using the corpus-calibrated chars/token ratio.

    The previous implementation assumed 4 chars/token, but Vietnamese
    legal text measures ~2.85 chars/token against the Qwen3 tokenizer, so
    the old estimate undercounted tokens by ~40% and let chunks grow well
    past the intended ``max_input_tokens`` budget. ``estimate_tokens``
    uses a conservative 2.6 ratio so we err on the side of smaller chunks.
    """
    return estimate_tokens(text)


def _split_by_sentence(text: str, max_tokens: int) -> list[str]:
    if _approx_token_count(text) <= max_tokens:
        return [text]
    sentences = _SENTENCE_BOUNDARY.split(text)
    if len(sentences) == 1:
        # No sentence boundaries detected: hard-split on a char budget
        # derived from the calibrated chars/token ratio.
        max_chars = max_chars_for_tokens(max_tokens)
        return [text[i : i + max_chars] for i in range(0, len(text), max_chars)]
    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0
    for s in sentences:
        st = _approx_token_count(s)
        if current and current_tokens + st > max_tokens:
            chunks.append(" ".join(current).strip())
            current = [s]
            current_tokens = st
        else:
            current.append(s)
            current_tokens += st
    if current:
        chunks.append(" ".join(current).strip())
    # If a single sentence still exceeds the budget, hard-split it on a
    # char budget. The previous implementation recursed into
    # ``_split_by_sentence`` on the over-sized piece, which would in turn
    # see "no sentence boundary" and recurse on the same input — a stack
    # overflow on long single-sentence runs (some vbpl.vn docs ship a
    # 100k-character "sentence" containing no period). The iterative
    # char-budget split is bounded and gives an identical result.
    max_chars = max_chars_for_tokens(max_tokens)
    out: list[str] = []
    for c in chunks:
        if _approx_token_count(c) > max_tokens:
            out.extend(c[i : i + max_chars] for i in range(0, len(c), max_chars))
        else:
            out.append(c)
    return out


# ---------------------------------------------------------------------------
# Article walk
# ---------------------------------------------------------------------------


def _iter_articles(root: LegalNode) -> list[LegalNode]:
    """DFS for all `dieu` nodes. Order preserved.

    Iterative implementation so deep parse trees do not blow Python's
    recursion limit when invoked from inside an async event loop.
    """
    out: list[LegalNode] = []
    stack: list[LegalNode] = [root]
    while stack:
        n = stack.pop()
        if n.level == "dieu":
            out.append(n)
            continue
        # Push children in reverse so document order is preserved on pop.
        for c in reversed(n.children):
            stack.append(c)
    return out


def _flatten_descendants(n: LegalNode) -> list[LegalNode]:
    """All descendants of ``n`` in document order, excluding ``n`` itself.

    Iterative DFS to keep stack usage independent of tree depth.
    """
    out: list[LegalNode] = []
    stack: list[LegalNode] = list(reversed(n.children))
    while stack:
        node = stack.pop()
        out.append(node)
        for child in reversed(node.children):
            stack.append(child)
    return out


def _summary(text: str, *, max_chars: int = 280) -> str:
    text = text.strip().replace("\n", " ")
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    last_period = cut.rfind(".")
    if last_period >= max_chars // 2:
        return cut[: last_period + 1]
    return cut + "…"


def _diem_label(node: LegalNode) -> str:
    """Reconstruct a point marker like ``a)`` from a parsed Điểm node.

    The HTML parser strips the leading marker into ``node.number`` (e.g.
    ``"a"``), so we re-attach it when assembling a clause so the embedded
    text reads like the source ("a) ...; b) ...") and citations stay
    legible.
    """
    if node.level == "diem" and node.number:
        return f"{node.number})"
    return ""


def _clause_text(khoan: LegalNode) -> str:
    """Assemble the full text of a Khoản: its own body plus every nested
    Điểm (and any deeper descendant) in document order.

    This is the key change behind Khoản-level chunking: a clause like
    "Phạt tiền từ 4-6 triệu đối với người điều khiển xe ... thực hiện một
    trong các hành vi sau: a) ...; c) Không chấp hành hiệu lệnh đèn tín
    hiệu; ..." stays in ONE chunk, so the penalty amount, the subject and
    the prohibited behaviour are never separated.
    """
    parts: list[str] = []
    if khoan.body:
        parts.append(khoan.body)
    for desc in _flatten_descendants(khoan):
        if not desc.body:
            continue
        label = _diem_label(desc)
        parts.append(f"{label} {desc.body}" if label else desc.body)
    return "\n".join(parts).strip()


# ---------------------------------------------------------------------------
# Chunker
# ---------------------------------------------------------------------------


class HierarchicalChunker:
    """A legal-structure-aware Parent-Child chunker."""

    def __init__(
        self,
        *,
        chunking_version: str,
        embedding_version: str,
        max_input_tokens: int = 8192,
        safety_margin_tokens: int = 256,
    ) -> None:
        self._chunking_version = chunking_version
        self._embedding_version = embedding_version
        self._max_input_tokens = max_input_tokens
        self._safety = safety_margin_tokens

    @property
    def chunking_version(self) -> str:
        return self._chunking_version

    def _metadata(self, doc: LegalDocument, hierarchical_path: str) -> ChunkMetadata:
        return ChunkMetadata(
            document_id=doc.document_id,
            so_ky_hieu=doc.so_ky_hieu,
            loai_van_ban=doc.loai_van_ban,
            co_quan_ban_hanh=doc.co_quan_ban_hanh,
            nganh=doc.nganh,
            linh_vuc=doc.linh_vuc,
            ngay_ban_hanh=doc.ngay_ban_hanh.isoformat() if doc.ngay_ban_hanh else None,
            ngay_co_hieu_luc=(
                doc.ngay_co_hieu_luc.isoformat() if doc.ngay_co_hieu_luc else None
            ),
            ngay_het_hieu_luc=(
                doc.ngay_het_hieu_luc.isoformat() if doc.ngay_het_hieu_luc else None
            ),
            effective_status=doc.effective_status or EffectiveStatus.UNKNOWN,
            hierarchical_path=hierarchical_path,
            chunking_version=self._chunking_version,
            embedding_version=self._embedding_version,
        )

    def chunk(
        self,
        *,
        document: LegalDocument,
        root: LegalNode,
    ) -> tuple[list[ParentChunk], list[ChildChunk]]:
        articles = _iter_articles(root)
        parents: list[ParentChunk] = []
        children: list[ChildChunk] = []

        max_tokens = self._max_input_tokens - self._safety

        for article in articles:
            # ---- Parent chunk -------------------------------------------------
            article_body_parts: list[str] = []
            if article.body:
                article_body_parts.append(article.body)
            for desc in _flatten_descendants(article):
                if desc.body:
                    article_body_parts.append(desc.body)
            article_body = "\n".join(article_body_parts).strip()
            article_summary = _summary(article_body)

            pid = parent_chunk_id(
                document_id=document.document_id,
                dieu_number=article.number,
                version=self._chunking_version,
            )
            parent_meta = self._metadata(document, article.hierarchical_path)
            parents.append(
                ParentChunk(
                    parent_chunk_id=pid,
                    document_id=document.document_id,
                    hierarchical_path=article.hierarchical_path,
                    heading=article.heading,
                    body=article_body,
                    summary=article_summary,
                    metadata=parent_meta,
                )
            )

            # ---- Child chunks (Khoản-level) ----------------------------------
            # Retrieval unit = one Clause (Khoản). All nested Điểm are
            # folded into the clause body so the chunk is self-contained:
            # it carries the penalty band, the subject ("người điều khiển
            # xe mô tô...") and every prohibited behaviour together. This
            # is what lets the embedder and the cross-encoder recognise a
            # clause in isolation. Articles with no Khoản emit a single
            # article-level child.
            khoans = [c for c in article.children if c.level == "khoan"]
            child_units: list[LegalNode] = list(khoans) if khoans else [article]

            for unit_index, kh in enumerate(child_units):
                is_article_unit = kh is article
                hier = kh.hierarchical_path
                if is_article_unit:
                    body = article_body
                else:
                    body = _clause_text(kh) or kh.body or ""

                # Header injected for embedding/BM25 — gives lexical and
                # semantic signal about location and the owning document.
                header = f"[{hier}]"
                if article.heading:
                    header += f" | {article.heading}"
                if parent_meta.so_ky_hieu:
                    header += f" | {parent_meta.so_ky_hieu}"

                # Prepend the article summary so a terse clause still
                # inherits the article's framing ("Xử phạt người điều
                # khiển xe mô tô... Phạt tiền từ...").
                full_text = f"{header}\n{article_summary}\n{body}".strip()
                splits = _split_by_sentence(full_text, max_tokens)

                for split_idx, piece in enumerate(splits):
                    cid = child_chunk_id(
                        document_id=document.document_id,
                        dieu_number=article.number,
                        khoan_number=None if is_article_unit else kh.number,
                        diem_number=None,
                        split_index=split_idx,
                        version=self._chunking_version,
                    )
                    children.append(
                        ChildChunk(
                            chunk_id=cid,
                            parent_chunk_id=pid,
                            document_id=document.document_id,
                            hierarchical_path=hier,
                            body=body,
                            text_for_embedding=piece,
                            sequence_index=unit_index,
                            split_index=split_idx,
                            metadata=self._metadata(document, hier),
                        )
                    )

        return parents, children
