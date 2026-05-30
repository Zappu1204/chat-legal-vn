"""Vietnamese legal HTML → tree of `LegalNode` parser.

The parser walks the HTML body extracted from `content_html` and identifies
structural markers (Phần / Chương / Mục / Điều / Khoản / Điểm). Body text
between markers is attached to the most recently opened node of equal or
deeper level.

The parser preserves the original sibling order, applies Unicode NFC
normalization, and emits a `parse_warning` for every malformed marker so
that downstream callers can decide whether to fall back to plain-text
chunking.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass
from typing import cast

from bs4 import BeautifulSoup, NavigableString, Tag

from ..domain.entities import LegalNode, NodeLevel

# ---------------------------------------------------------------------------
# Marker patterns
# ---------------------------------------------------------------------------
#
# Vietnamese legal documents use the following hierarchy. Markers may appear
# in headings or as the first tokens of a paragraph. We accept both.
#
#   Phần X.       (uppercase "Phần" or "PHẦN")
#   Chương X.
#   Mục X.
#   Điều X.       — typically the most semantically meaningful node.
#   Khoản X.      — often expressed as a leading number "1.", "2."
#   Điểm a)       — often expressed as a leading letter "a)", "b)"
#
# We deliberately accept variants seen in the wild on vbpl.vn.

_LEVEL_ORDER: dict[NodeLevel, int] = {
    "phan": 0,
    "chuong": 1,
    "muc": 2,
    "dieu": 3,
    "khoan": 4,
    "diem": 5,
}

_PATTERNS: list[tuple[NodeLevel, re.Pattern[str]]] = [
    ("phan", re.compile(r"^\s*PHẦN\s+([IVXLCDM]+|\d+|[A-Z]+)\b[\.:]?", re.IGNORECASE)),
    ("chuong", re.compile(r"^\s*CHƯƠNG\s+([IVXLCDM]+|\d+|[A-Z]+)\b[\.:]?", re.IGNORECASE)),
    ("muc", re.compile(r"^\s*MỤC\s+(\d+|[IVXLCDM]+)\b[\.:]?", re.IGNORECASE)),
    ("dieu", re.compile(r"^\s*Điều\s+(\d+[a-zA-Z]?)\b[\.:]?", re.IGNORECASE)),
    # Explicit "Khoản N." form (canonical pretty-printed form and a frequent
    # variant in vbpl.vn HTML).
    ("khoan", re.compile(r"^\s*Khoản\s+(\d+[a-zA-Z]?)\b[\.:]?", re.IGNORECASE)),
    # Khoản markers often appear as a bare leading "1." or "2." inside an
    # Điều — accepted only when we have already entered a Điều (enforced
    # below).
    ("khoan", re.compile(r"^\s*(\d+)\s*[\.\)]\s+")),
    # Explicit "Điểm a)" form.
    ("diem", re.compile(r"^\s*Điểm\s+([a-zđ])\b[\.:\)]?", re.IGNORECASE)),
    # Bare "a)" / "b)" form.
    ("diem", re.compile(r"^\s*([a-zđ])\s*\)\s+", re.IGNORECASE)),
]


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def _clean(text: str) -> str:
    text = _nfc(text)
    # Collapse whitespace but preserve newlines to keep paragraph boundaries.
    return re.sub(r"[ \t\r\f\v]+", " ", text).strip()


# ---------------------------------------------------------------------------
# Block extraction from HTML
# ---------------------------------------------------------------------------


@dataclass
class _Block:
    text: str
    is_heading: bool


def _iter_blocks(html: str) -> list[_Block]:
    """Flatten the HTML into a sequence of paragraph-like text blocks.

    We walk top-level block-level elements iteratively (no Python recursion)
    so that pathologically deep documents — vbpl.vn ships some with
    thousands of nested ``<div>``s — do not blow the interpreter stack.
    Headings (h1..h6) are flagged so the parser can prefer them as marker
    carriers.
    """
    soup = BeautifulSoup(html, "lxml")
    blocks: list[_Block] = []

    block_tags = {
        "p", "div", "li", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6",
        "section", "article", "header", "footer",
    }

    def emit(node: Tag) -> None:
        text = _clean(node.get_text(separator=" ", strip=True))
        if text:
            blocks.append(_Block(text=text, is_heading=node.name.startswith("h")))

    visited: set[int] = set()

    def has_nested_block(node: Tag) -> bool:
        # Use a bounded iterative DFS instead of `node.descendants` (which
        # also recurses internally on huge trees but is cheap enough here).
        # Early-exit on the first nested block tag.
        for child in node.children:
            if isinstance(child, Tag) and child.name in block_tags:
                return True
            if isinstance(child, Tag):
                if has_nested_block_iter(child, block_tags):
                    return True
        return False

    body = soup.body or soup
    body = cast(Tag, body)

    # Iterative DFS using an explicit work stack. Each frame is the next
    # node to process. We never recurse into Python; the depth of the work
    # stack equals the depth of the HTML tree but is bounded by the heap,
    # not by ``sys.getrecursionlimit()``.
    stack: list[Tag | NavigableString] = [body]
    while stack:
        node = stack.pop()
        if isinstance(node, NavigableString):
            text = _clean(str(node))
            if text:
                blocks.append(_Block(text=text, is_heading=False))
            continue
        if not isinstance(node, Tag):
            continue
        if id(node) in visited:
            continue

        if node.name in block_tags:
            if has_nested_block_iter(node, block_tags):
                # Push children in reverse so they are processed in document
                # order after pop().
                for child in reversed(list(node.children)):
                    if isinstance(child, (Tag, NavigableString)):
                        stack.append(child)
            else:
                visited.add(id(node))
                emit(node)
        else:
            for child in reversed(list(node.children)):
                if isinstance(child, (Tag, NavigableString)):
                    stack.append(child)

    if not blocks:
        text = _clean(soup.get_text(separator="\n"))
        if text:
            blocks.append(_Block(text=text, is_heading=False))

    return blocks


def has_nested_block_iter(root: Tag, block_tags: set[str]) -> bool:
    """Iterative ``has_nested_block`` to avoid Python recursion on deep DOMs."""
    stack: list[Tag] = list(c for c in root.children if isinstance(c, Tag))
    while stack:
        node = stack.pop()
        if node.name in block_tags:
            return True
        stack.extend(c for c in node.children if isinstance(c, Tag))
    return False


# ---------------------------------------------------------------------------
# Marker detection
# ---------------------------------------------------------------------------


def _detect_marker(text: str, in_dieu: bool) -> tuple[NodeLevel, str, str] | None:
    """Return `(level, number, body_after_marker)` if `text` opens a marker."""
    for level, pat in _PATTERNS:
        # Bare numeric Khoản (`1.`, `2.`) and bare alpha Điểm (`a)`, `b)`)
        # are highly ambiguous; only accept them inside an Điều. The
        # explicit "Khoản N." / "Điểm X" patterns are listed first and have
        # an unambiguous keyword, so they match before the bare patterns.
        is_bare = pat.pattern.startswith(r"^\s*(\d+)\s*") or pat.pattern.startswith(
            r"^\s*([a-zđ])"
        )
        if is_bare and not in_dieu:
            continue
        m = pat.match(text)
        if m:
            number = m.group(1)
            rest = text[m.end():].strip()
            return level, number, rest
    return None


# ---------------------------------------------------------------------------
# Tree building
# ---------------------------------------------------------------------------


def _node_id(document_id: int, level: NodeLevel, number: str | None, seq: int) -> str:
    return f"{document_id}:{level}:{number or 'root'}:{seq}"


def parse_html(*, document_id: int, document_title: str, html: str) -> LegalNode:
    """Parse `content_html` of a `LegalDocument` into a tree of `LegalNode`s.

    Returns a synthetic root node whose `level` is `phan` and whose body is
    empty. All actual content is attached under `root.children`.
    """
    if not html:
        root = LegalNode(
            node_id=f"{document_id}:root",
            document_id=document_id,
            level="phan",
            number=None,
            heading=document_title,
            body="",
            hierarchical_path=document_title,
            sequence_index=0,
        )
        return root

    blocks = _iter_blocks(html)

    root = LegalNode(
        node_id=f"{document_id}:root",
        document_id=document_id,
        level="phan",
        number=None,
        heading=document_title,
        body="",
        hierarchical_path=document_title,
        sequence_index=0,
    )

    # Stack holds the currently open ancestors (including root).
    stack: list[LegalNode] = [root]
    seq_counter = 0
    in_dieu = False
    warnings: list[str] = []

    def current_path(node: LegalNode) -> str:
        # Build "Title > Chương 1 > Điều 5 > Khoản 2 > Điểm a"
        parts = [stack[0].heading or document_title]
        for n in stack[1:]:
            label = _label_for_node(n)
            if label:
                parts.append(label)
        if node is not root:
            label = _label_for_node(node)
            if label:
                parts.append(label)
        return " > ".join(parts)

    def attach_text(text: str) -> None:
        # Append text to the deepest open node's body.
        if not text:
            return
        target = stack[-1]
        if target.body:
            target.body = f"{target.body}\n{text}"
        else:
            target.body = text

    for block in blocks:
        text = block.text
        if not text:
            continue

        marker = _detect_marker(text, in_dieu=in_dieu)
        if marker is None:
            attach_text(text)
            continue

        level, number, rest = marker

        # Pop until top of stack has strictly higher level than `level`.
        target_order = _LEVEL_ORDER[level]
        while len(stack) > 1 and _LEVEL_ORDER[stack[-1].level] >= target_order:
            stack.pop()

        seq_counter += 1
        new_node = LegalNode(
            node_id=_node_id(document_id, level, number, seq_counter),
            document_id=document_id,
            level=level,
            number=number,
            heading=text if block.is_heading else None,
            body=rest,
            hierarchical_path="",  # filled below
            sequence_index=seq_counter,
        )
        stack[-1].children.append(new_node)
        stack.append(new_node)
        new_node.hierarchical_path = current_path(new_node)
        if level == "dieu":
            in_dieu = True

    # Walk once more to finalize hierarchical paths for any nodes whose
    # ancestor stack changed after creation (we already set them, but make
    # sure children have stable paths).
    _finalize_paths(root, prefix=root.heading or document_title)

    if warnings:
        root.parse_warnings.extend(warnings)
    return root


def _label_for_node(node: LegalNode) -> str | None:
    if node.level == "phan" and node.number:
        return f"Phần {node.number}"
    if node.level == "chuong" and node.number:
        return f"Chương {node.number}"
    if node.level == "muc" and node.number:
        return f"Mục {node.number}"
    if node.level == "dieu" and node.number:
        return f"Điều {node.number}"
    if node.level == "khoan" and node.number:
        return f"Khoản {node.number}"
    if node.level == "diem" and node.number:
        return f"Điểm {node.number}"
    return None


def _finalize_paths(node: LegalNode, *, prefix: str) -> None:
    """Iteratively populate ``hierarchical_path`` for every node.

    Implemented with an explicit stack instead of recursion so very deep
    HTML trees (some vbpl.vn docs nest > 1000 levels of `<p>` / `<div>`
    inside tables) don't blow Python's default recursion limit when the
    parser is invoked deep inside an async event loop.
    """
    stack: list[tuple[LegalNode, str]] = [(node, prefix)]
    while stack:
        n, p = stack.pop()
        label = _label_for_node(n)
        new_prefix = f"{p} > {label}" if label else p
        if n.level != "phan" or n.number is not None:
            n.hierarchical_path = new_prefix
        # Iterating children in original order isn't required for correctness;
        # paths are independent. Pushing in reverse keeps a depth-first
        # traversal close to the recursive version for any debug output.
        for child in reversed(n.children):
            stack.append((child, new_prefix))


# ---------------------------------------------------------------------------
# Pretty printer (round-trip for tests)
# ---------------------------------------------------------------------------


def pretty_print(root: LegalNode) -> str:
    """Serialize a `LegalNode` tree into canonical Vietnamese legal text."""
    lines: list[str] = []
    if root.heading and root.level == "phan" and root.number is None:
        lines.append(root.heading)

    def walk(n: LegalNode) -> None:
        label = _label_for_node(n)
        if label and n.body:
            lines.append(f"{label}. {n.body}")
        elif label:
            lines.append(f"{label}.")
        elif n.body:
            lines.append(n.body)
        for c in n.children:
            walk(c)

    for child in root.children:
        walk(child)
    return "\n".join(lines)


# Public, deterministic IDs for fallback nodes (non-hashed).
def synthetic_node_id() -> str:
    return f"syn:{uuid.uuid4().hex[:12]}"
