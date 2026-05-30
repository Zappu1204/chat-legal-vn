"""Tests for citation parsing & canonical formatting."""

from __future__ import annotations

from chat_legal_vn.domain.entities import Citation
from chat_legal_vn.services.citation import parse_citations


def test_parse_full_citation() -> None:
    text = "Theo [Nghị định 100/2019/NĐ-CP - Điều 6 - Khoản 4 - Điểm e]."
    cits = parse_citations(text)
    assert len(cits) == 1
    c = cits[0]
    assert c.loai_van_ban and c.loai_van_ban.lower().startswith("nghị")
    assert c.so_ky_hieu and "100/2019" in c.so_ky_hieu
    assert c.dieu == "6"
    assert c.khoan == "4"
    assert c.diem == "e"


def test_canonical_round_trip() -> None:
    c = Citation(
        loai_van_ban="Nghị định",
        so_ky_hieu="100/2019/NĐ-CP",
        dieu="6",
        khoan="4",
        diem="e",
    )
    s = c.canonical()
    cits = parse_citations(s)
    assert cits, f"expected citation parse from {s!r}"
    again = cits[0].canonical()
    assert again == s
