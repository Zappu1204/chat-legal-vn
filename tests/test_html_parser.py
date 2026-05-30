"""Tests for the Vietnamese legal HTML parser."""

from __future__ import annotations

from chat_legal_vn.services.html_parser import parse_html, pretty_print

SAMPLE_HTML = """
<div>
  <h2>Chương I. NHỮNG QUY ĐỊNH CHUNG</h2>
  <p>Điều 1. Phạm vi điều chỉnh</p>
  <p>Luật này quy định về trật tự, an toàn giao thông đường bộ.</p>
  <p>Điều 2. Đối tượng áp dụng</p>
  <p>1. Cơ quan, tổ chức, cá nhân Việt Nam.</p>
  <p>2. Cơ quan, tổ chức, cá nhân nước ngoài có liên quan.</p>
  <p>a) Trường hợp 1.</p>
  <p>b) Trường hợp 2.</p>
</div>
"""


def test_parse_extracts_articles_and_clauses() -> None:
    root = parse_html(document_id=1, document_title="Luật Mẫu", html=SAMPLE_HTML)
    levels = []

    def walk(node):
        levels.append((node.level, node.number))
        for c in node.children:
            walk(c)

    walk(root)
    types = {lvl for lvl, _ in levels}
    assert "chuong" in types
    assert "dieu" in types
    assert "khoan" in types
    assert "diem" in types


def test_pretty_print_round_trip_preserves_structure() -> None:
    root1 = parse_html(document_id=1, document_title="Luật Mẫu", html=SAMPLE_HTML)
    text = pretty_print(root1)
    assert "Điều 1" in text
    assert "Điều 2" in text
    # Re-parsing the printed text yields the same set of structural paths.
    paragraphs = "".join(f"<p>{line}</p>" for line in text.splitlines() if line.strip())
    root2 = parse_html(
        document_id=1,
        document_title="Luật Mẫu",
        html=f"<div>{paragraphs}</div>",
    )

    def collect(node, out):
        for c in node.children:
            if c.level in {"dieu", "khoan", "diem"}:
                out.append((c.level, c.number))
            collect(c, out)

    a, b = [], []
    collect(root1, a)
    collect(root2, b)
    assert set(a) == set(b)
