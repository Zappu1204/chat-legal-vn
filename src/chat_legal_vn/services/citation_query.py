"""Parse legal citations directly from a Vietnamese user query.

The :mod:`citation` module already extracts ``[bracketed]`` citations from
LLM-generated answers. This module solves the inverse problem: detect
citation-style references inside a free-form *question* so the retrieval
pipeline can route them through a metadata-aware path.

Examples it handles
-------------------

* ``"Điểm h, i khoản 2 Điều 7 Nghị định 168/2024/NĐ-CP nói về những gì?"``
* ``"Điều 38 Luật 60/2014/QH13 quy định gì?"``
* ``"Khoản 1, 3, 5 Điều 100/2019/NĐ-CP"``
* ``"Nghị định 100/2019/NĐ-CP, Điều 5"``

Output
------

A :class:`CitationQuery` exposing:

* ``so_ky_hieu``  — normalised, e.g. ``"168/2024/NĐ-CP"``
* ``dieu``        — single article number, e.g. ``"7"`` or ``"7a"``
* ``khoan``       — list of clause numbers (multi-clause queries are common)
* ``diem``        — list of point letters (``["h", "i"]`` for the example)
* ``loai_van_ban``— document type when stated explicitly

The parser is *intentionally permissive*: it accepts spelling variants
(``điểm h, i`` vs ``điểm h và i``), commas, ``và`` connectors and uppercase
section markers.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# Regex patterns
# --------------------------------------------------------------------------

# Document number: 100/2019/NĐ-CP, 64/2025/QH15, 168/2024/NĐ-CP, 22/2014/TT-BCA
# Token must contain at least one slash and at least one alphanumeric segment.
_SO_KY_HIEU = re.compile(
    r"\b(\d+(?:[/\-][\w\u00C0-\u1EF9]+){1,4})", re.UNICODE
)

# Document type — placed in front of the so_ky_hieu typically.
_LOAI_VAN_BAN = re.compile(
    r"\b(Luật|Bộ luật|Nghị định|Nghị quyết|Thông tư(?: liên tịch)?|"
    r"Quyết định|Pháp lệnh|Hiến pháp|Lệnh|Chỉ thị|Công văn|Sắc lệnh)\b",
    re.IGNORECASE,
)

# Section markers. We use named groups for readability.
# "Điều 7", "Điều 7a"
# Negative-lookahead for ``/`` and ``-`` so we don't accidentally pull
# the leading number out of a document number that the user typed
# without an explicit type prefix (e.g. ``"Điều 100/2019/NĐ-CP"`` —
# without the lookahead the regex would match ``Điều 100`` instead of
# letting it fall through to the document-number parser).
_DIEU = re.compile(r"\bĐiều\s+(\d+[a-zA-Z]?)(?![\d/\-])", re.IGNORECASE)

# "Khoản 1", "Khoản 1, 2 và 3", "Khoản 1, 2, 3"
# We match the keyword + a list of numbers separated by commas / "và" / "or".
_KHOAN_LIST = re.compile(
    r"\bKhoản\s+([\d]+[a-zA-Z]?(?:\s*(?:,|và|hoặc|or)\s*[\d]+[a-zA-Z]?)*)",
    re.IGNORECASE,
)

# "Điểm a", "Điểm h, i", "Điểm a và b", "Điểm a, b, c"
_DIEM_LIST = re.compile(
    r"\bĐiểm\s+([a-zđ](?:\s*(?:,|và|hoặc|or)\s*[a-zđ])*)",
    re.IGNORECASE,
)

# Helper: split a list-string like "1, 2 và 3" into ["1", "2", "3"]
_LIST_SEPARATOR = re.compile(r"\s*(?:,|và|hoặc|or)\s*", re.IGNORECASE)


# --------------------------------------------------------------------------
# Data class
# --------------------------------------------------------------------------


@dataclass(slots=True)
class CitationQuery:
    """Parsed legal citation extracted from a user question."""

    so_ky_hieu: str | None = None
    loai_van_ban: str | None = None
    dieu: str | None = None
    khoan: list[str] = field(default_factory=list)
    diem: list[str] = field(default_factory=list)

    @property
    def has_anything(self) -> bool:
        return bool(
            self.so_ky_hieu or self.dieu or self.khoan or self.diem or self.loai_van_ban
        )

    @property
    def is_strong(self) -> bool:
        """True when the citation is specific enough to drive a hard lookup.

        We require at least a document number *or* (Điều + so_ky_hieu).
        Standalone "Điều 7" without context would otherwise pull every
        Article 7 across the entire corpus.
        """
        return bool(self.so_ky_hieu and (self.dieu or self.khoan or self.diem))

    def expand_paths(self) -> list[str]:
        """Return the list of ``hierarchical_path`` substrings that exactly
        match this citation. The strings are *substrings* (not full paths)
        because the parent title varies per document.

        For "Điều 7, Khoản 2, Điểm h" the expansion is::

            ["Điều 7 > Khoản 2 > Điểm h"]

        For "Khoản 2 Điều 7, Điểm h và i"::

            ["Điều 7 > Khoản 2 > Điểm h",
             "Điều 7 > Khoản 2 > Điểm i"]

        When ``diem`` is empty we stop at the deepest specified level so
        all chunks below the cited node match.
        """
        if not (self.dieu or self.khoan or self.diem):
            return []

        dieu_part = f"Điều {self.dieu}" if self.dieu else None
        khoan_list = self.khoan or [None]  # type: ignore[list-item]
        diem_list = self.diem or [None]  # type: ignore[list-item]
        out: list[str] = []
        for k in khoan_list:
            khoan_part = f"Khoản {k}" if k else None
            for d in diem_list:
                diem_part = f"Điểm {d}" if d else None
                pieces = [p for p in (dieu_part, khoan_part, diem_part) if p]
                if pieces:
                    out.append(" > ".join(pieces))
        return out


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def _split_list(raw: str) -> list[str]:
    return [
        token.strip().lower()
        for token in _LIST_SEPARATOR.split(raw)
        if token.strip()
    ]


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def parse_query_citation(query: str) -> CitationQuery:
    """Extract a single :class:`CitationQuery` from a user question.

    Multi-document citations (rare in practice) are collapsed to the first
    document number — the planner's :func:`decompose` step can split such
    queries into sub-questions before calling this parser if needed.
    """
    if not query:
        return CitationQuery()
    text = _nfc(query)

    cit = CitationQuery()

    if m := _SO_KY_HIEU.search(text):
        token = m.group(1).replace(" ", "")
        # Reject obvious year-only matches (e.g. "2024" alone).
        if "/" in token or "-" in token:
            cit.so_ky_hieu = token

    if m := _LOAI_VAN_BAN.search(text):
        # Capitalise as in dataset: "Nghị định" / "Luật" / etc.
        cit.loai_van_ban = m.group(1).capitalize()

    if m := _DIEU.search(text):
        cit.dieu = m.group(1)

    if m := _KHOAN_LIST.search(text):
        cit.khoan = _split_list(m.group(1))

    if m := _DIEM_LIST.search(text):
        cit.diem = _split_list(m.group(1))

    return cit
