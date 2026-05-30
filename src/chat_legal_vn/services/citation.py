"""Citation parsing and resolution.

Canonical form: `[{loai_van_ban} {so_ky_hieu} - Điều X - Khoản Y - Điểm Z]`
"""

from __future__ import annotations

import re
import unicodedata

from ..domain.entities import Citation

_BRACKETED = re.compile(r"\[([^\[\]]+)\]")
_DIEU = re.compile(r"Điều\s+(\d+[a-zA-Z]?)", re.IGNORECASE)
_KHOAN = re.compile(r"Khoản\s+(\d+[a-zA-Z]?)", re.IGNORECASE)
_DIEM = re.compile(r"Điểm\s+([a-zđ])", re.IGNORECASE)
# Match Vietnamese document numbers like `100/2019/NĐ-CP`, `64/2025/QH15`,
# `115/NQ-HĐBCQG`. The pattern allows hyphens **inside** the token but not
# the surrounding `" - "` separator used in the canonical citation form.
_SO_KY_HIEU = re.compile(r"\b(\d+(?:[/\-][\w\u00C0-\u1EF9]+)+)")
_LOAI = re.compile(
    r"\b(Luật|Bộ luật|Nghị định|Nghị quyết|Thông tư|Quyết định|Pháp lệnh|Hiến pháp|Lệnh|Chỉ thị|Công văn)\b",
    re.IGNORECASE,
)


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def parse_citations(text: str) -> list[Citation]:
    """Extract citations from a free-form Vietnamese answer text."""
    out: list[Citation] = []
    text = _nfc(text)
    for match in _BRACKETED.finditer(text):
        body = match.group(1)
        loai = (m := _LOAI.search(body)) and m.group(1)
        so_ky_hieu = (m := _SO_KY_HIEU.search(body)) and m.group(1).replace(" ", "")
        dieu = (m := _DIEU.search(body)) and m.group(1)
        khoan = (m := _KHOAN.search(body)) and m.group(1)
        diem = (m := _DIEM.search(body)) and m.group(1)
        if not (dieu or so_ky_hieu):
            continue
        out.append(
            Citation(
                loai_van_ban=loai,
                so_ky_hieu=so_ky_hieu,
                dieu=dieu,
                khoan=khoan,
                diem=diem.lower() if diem else None,
            )
        )
    return out
