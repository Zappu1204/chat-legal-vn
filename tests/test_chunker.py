"""Tests for the Hierarchical Parent-Child chunker."""

from __future__ import annotations

from datetime import date

from chat_legal_vn.domain.entities import EffectiveStatus, LegalDocument
from chat_legal_vn.services.chunker import HierarchicalChunker
from chat_legal_vn.services.html_parser import parse_html

HTML = """
<div>
  <p>Điều 5. Trách nhiệm</p>
  <p>1. Khoản 1 nội dung.</p>
  <p>2. Khoản 2 nội dung.</p>
  <p>a) Điểm a chi tiết.</p>
  <p>b) Điểm b chi tiết.</p>
  <p>Điều 6. Quy định khác</p>
  <p>Toàn bộ nội dung của Điều 6.</p>
</div>
"""


def _doc() -> LegalDocument:
    return LegalDocument(
        document_id=42,
        title="Luật Mẫu",
        so_ky_hieu="01/2024/QH15",
        loai_van_ban="Luật",
        co_quan_ban_hanh="Quốc hội",
        ngay_ban_hanh=date(2024, 1, 1),
        effective_status=EffectiveStatus.EFFECTIVE,
        has_full_text=True,
    )


def test_chunker_emits_parent_per_article_and_child_per_unit() -> None:
    doc = _doc()
    root = parse_html(document_id=doc.document_id, document_title=doc.title, html=HTML)
    chunker = HierarchicalChunker(
        chunking_version="v1",
        embedding_version="emb-v1",
        max_input_tokens=8192,
    )
    parents, children = chunker.chunk(document=doc, root=root)
    assert len(parents) == 2
    # Khoản 2 has 2 Điểm and Khoản 1 stands alone, plus Điều 6 is itself one child.
    assert len(children) >= 3
    # Every child carries `parent_chunk_id`
    parent_ids = {p.parent_chunk_id for p in parents}
    for c in children:
        assert c.parent_chunk_id in parent_ids
        assert c.metadata.chunking_version == "v1"


def test_chunker_is_deterministic() -> None:
    doc = _doc()
    root = parse_html(document_id=doc.document_id, document_title=doc.title, html=HTML)
    chunker = HierarchicalChunker(
        chunking_version="v1",
        embedding_version="emb-v1",
        max_input_tokens=8192,
    )
    p1, c1 = chunker.chunk(document=doc, root=root)
    p2, c2 = chunker.chunk(document=doc, root=root)
    assert [p.parent_chunk_id for p in p1] == [p.parent_chunk_id for p in p2]
    assert [c.chunk_id for c in c1] == [c.chunk_id for c in c2]
