"""Corpus loader: parquet → normalized `LegalDocument` records.

Memory-aware ingestion strategy
================================

The dataset on disk looks small (~411 MB compressed) but each parquet file
is shipped with very large row groups (122k + 55k rows for
``content.parquet``). pyarrow's columnar reader decompresses **a full row
group** before yielding any batch, which forces a 9 GB RSS spike just to
return the first 10 rows. ``pyarrow.dataset``, ``ParquetFile.iter_batches``,
and DuckDB's parquet scanner all share the same constraint.

The fix that scales is simple and follows the standard practice
recommended by Apache Arrow [1] and DuckDB [2]: rewrite ``content.parquet``
once into a new file with **small row groups** (~512 rows each), then
stream the small-group file at ingest time. After repack the entire
178k-row corpus iterates in ~250 MiB peak RSS.

We do this transparently: the first time ``iter_documents`` runs we
inspect the source file; if any row group is larger than
``MAX_SAFE_ROW_GROUP_SIZE`` rows, we materialise a ``content.repack.parquet``
sibling and stream from it on subsequent runs. The original file is left
untouched so this is non-destructive and reversible (``rm
content.repack.parquet`` to retry).

[1] https://arrow.apache.org/docs/python/parquet.html#fine-grained-reading-and-writing
[2] https://duckdb.org/docs/stable/guides/performance/how_to_tune_workloads
"""

from __future__ import annotations

import gc
import logging
import os
import time
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from ..domain.entities import (
    TINH_TRANG_LOOKUP,
    EffectiveStatus,
    LegalDocument,
)

log = logging.getLogger(__name__)

# Columns we actually consume from `metadata.parquet`. Kept narrow on
# purpose so the in-memory index stays small.
_META_COLUMNS: tuple[str, ...] = (
    "id",
    "title",
    "so_ky_hieu",
    "loai_van_ban",
    "co_quan_ban_hanh",
    "nganh",
    "linh_vuc",
    "nguoi_ky",
    "chuc_danh",
    "pham_vi",
    "ngay_ban_hanh",
    "ngay_co_hieu_luc",
    "ngay_het_hieu_luc",
    "tinh_trang_hieu_luc",
)

_CONTENT_COLUMNS: tuple[str, ...] = ("id", "content_html")

# A row group with this many rows or fewer can be safely decompressed in
# one shot on a workstation. The vbpl content rows average ~17 KiB of HTML;
# 1024 rows ≈ 17 MiB peak per group.
MAX_SAFE_ROW_GROUP_SIZE = 4096

# Target row group size when we repack. Smaller is friendlier to RAM but
# larger is friendlier to scan throughput; 512 is a comfortable midpoint.
REPACK_ROW_GROUP_SIZE = 512


# ---------------------------------------------------------------------------
# Date and row normalisation
# ---------------------------------------------------------------------------


def _parse_dd_mm_yyyy(value: object) -> tuple[date | None, str | None]:
    if value is None:
        return None, None
    s = str(value).strip()
    if not s or s.lower() in {"nan", "none"}:
        return None, None
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date(), None
        except ValueError:
            continue
    return None, s


def _str_or_none(value: object) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() in {"nan", "none", "<na>"}:
        return None
    return s


def _normalize_row(row: dict[str, Any], *, has_full_text: bool) -> LegalDocument:
    raw_dates: dict[str, str] = {}
    ngay_ban_hanh, raw = _parse_dd_mm_yyyy(row.get("ngay_ban_hanh"))
    if raw:
        raw_dates["ngay_ban_hanh"] = raw
    ngay_co_hieu_luc, raw = _parse_dd_mm_yyyy(row.get("ngay_co_hieu_luc"))
    if raw:
        raw_dates["ngay_co_hieu_luc"] = raw
    ngay_het_hieu_luc, raw = _parse_dd_mm_yyyy(row.get("ngay_het_hieu_luc"))
    if raw:
        raw_dates["ngay_het_hieu_luc"] = raw

    tinh_trang = _str_or_none(row.get("tinh_trang_hieu_luc"))
    status = TINH_TRANG_LOOKUP.get(tinh_trang or "", EffectiveStatus.UNKNOWN)

    return LegalDocument(
        document_id=int(row["id"]),
        title=str(row.get("title") or "").strip(),
        so_ky_hieu=_str_or_none(row.get("so_ky_hieu")),
        loai_van_ban=_str_or_none(row.get("loai_van_ban")),
        co_quan_ban_hanh=_str_or_none(row.get("co_quan_ban_hanh")),
        nganh=_str_or_none(row.get("nganh")),
        linh_vuc=_str_or_none(row.get("linh_vuc")),
        nguoi_ky=_str_or_none(row.get("nguoi_ky")),
        chuc_danh=_str_or_none(row.get("chuc_danh")),
        pham_vi=_str_or_none(row.get("pham_vi")),
        ngay_ban_hanh=ngay_ban_hanh,
        ngay_co_hieu_luc=ngay_co_hieu_luc,
        ngay_het_hieu_luc=ngay_het_hieu_luc,
        tinh_trang_hieu_luc=tinh_trang,
        effective_status=status,
        has_full_text=has_full_text,
        raw_dates=raw_dates,
    )


# ---------------------------------------------------------------------------
# Repack
# ---------------------------------------------------------------------------


def _max_row_group_size(parquet_path: Path) -> int:
    pf = pq.ParquetFile(parquet_path)
    if pf.num_row_groups == 0:
        return 0
    return max(pf.metadata.row_group(i).num_rows for i in range(pf.num_row_groups))


def _is_repack_fresh(source: Path, repack: Path) -> bool:
    if not repack.exists():
        return False
    try:
        return repack.stat().st_mtime >= source.stat().st_mtime
    except OSError:
        return False


def repack_parquet(
    *,
    source: Path,
    destination: Path,
    columns: tuple[str, ...] | None = None,
    row_group_size: int = REPACK_ROW_GROUP_SIZE,
    compression: str = "zstd",
    compression_level: int = 3,
) -> dict[str, Any]:
    """Rewrite ``source`` into ``destination`` with small row groups.

    Returns a stats dict for logging. The first read of each source row
    group still spikes RAM (pyarrow has no incremental row-group decoder),
    but this is paid **once** at corpus-prep time. After repack, every
    ingest run streams in O(row_group_size × avg_row_size) memory.
    """
    pf = pq.ParquetFile(source)
    schema = pf.schema_arrow
    if columns is not None:
        # Project only requested columns by reading the whole row group with
        # the projection applied; pyarrow already supports this.
        schema = pa.schema([schema.field(c) for c in columns])

    tmp = destination.with_suffix(destination.suffix + ".tmp")
    if tmp.exists():
        tmp.unlink()
    writer = pq.ParquetWriter(
        tmp,
        schema,
        compression=compression,
        compression_level=compression_level,
    )

    t0 = time.perf_counter()
    rows_total = 0
    try:
        for i in range(pf.num_row_groups):
            table = pf.read_row_group(
                i, columns=list(columns) if columns is not None else None
            )
            for batch in table.to_batches(max_chunksize=row_group_size):
                writer.write_batch(batch)
                rows_total += batch.num_rows
            del table
            gc.collect()
    finally:
        writer.close()

    os.replace(tmp, destination)
    elapsed = time.perf_counter() - t0
    pf2 = pq.ParquetFile(destination)
    return {
        "rows": rows_total,
        "row_groups": pf2.num_row_groups,
        "elapsed_seconds": round(elapsed, 2),
        "size_mb": round(destination.stat().st_size / 1024**2, 1),
    }


def _ensure_content_repacked(data_dir: Path) -> Path:
    """Return a path to a content parquet whose row groups are stream-safe.

    If the source file already has small row groups, it is returned
    unchanged. Otherwise we transparently materialise (or reuse) a
    ``content.repack.parquet`` sibling.
    """
    source = data_dir / "content.parquet"
    repack = data_dir / "content.repack.parquet"

    if _max_row_group_size(source) <= MAX_SAFE_ROW_GROUP_SIZE:
        return source
    if _is_repack_fresh(source, repack):
        return repack

    log.warning(
        "corpus.repacking_content",
        extra={
            "source": str(source),
            "destination": str(repack),
            "max_row_group_size": MAX_SAFE_ROW_GROUP_SIZE,
            "note": "first run only; one-shot RAM spike is expected",
        },
    )
    stats = repack_parquet(
        source=source,
        destination=repack,
        columns=_CONTENT_COLUMNS,
        row_group_size=REPACK_ROW_GROUP_SIZE,
    )
    log.info("corpus.repack_done", extra=stats)
    return repack


# ---------------------------------------------------------------------------
# Index loaders
# ---------------------------------------------------------------------------


def _build_metadata_index(data_dir: Path) -> dict[int, dict[str, Any]]:
    """Return ``{document_id → row dict}`` for fast lookup during streaming.

    Bypasses pandas to keep the working set ~150-180 MB instead of ~5 GB.
    """
    path = data_dir / "metadata.parquet"
    table = pq.read_table(path, columns=list(_META_COLUMNS))
    index: dict[int, dict[str, Any]] = {}
    for batch in table.to_batches(max_chunksize=4096):
        rows = batch.to_pylist()
        for r in rows:
            raw_id = r.get("id")
            if raw_id is None:
                continue
            try:
                index[int(raw_id)] = r
            except (TypeError, ValueError):
                continue
    del table
    gc.collect()
    return index


# ---------------------------------------------------------------------------
# Public iteration API
# ---------------------------------------------------------------------------


def iter_documents(
    *,
    data_dir: Path,
    limit: int | None = None,
    require_html: bool = True,
    batch_size: int = 64,
) -> Iterator[tuple[LegalDocument, str | None]]:
    """Yield ``(LegalDocument, content_html or None)`` pairs lazily.

    Args:
        data_dir: directory containing the three parquet files.
        limit: stop after yielding this many documents. The generator
            short-circuits — rows beyond ``limit`` are never read.
        require_html: when True (ingest path) only documents whose
            ``content_html`` is non-empty are yielded. When False (graph
            ingest path) we stream ``metadata.parquet`` only and yield
            ``html=None``.
        batch_size: pyarrow row-group batch size. Only used when
            ``require_html=True``. Default 64 ≈ ~1 MiB per batch.
    """
    if require_html:
        yield from _iter_with_content(
            data_dir=data_dir, limit=limit, batch_size=batch_size
        )
    else:
        yield from _iter_metadata_only(data_dir=data_dir, limit=limit)


def _iter_with_content(
    *,
    data_dir: Path,
    limit: int | None,
    batch_size: int,
) -> Iterator[tuple[LegalDocument, str | None]]:
    """Stream the JOIN metadata × content with bounded memory."""
    content_path = _ensure_content_repacked(data_dir)
    meta_index = _build_metadata_index(data_dir)

    pf = pq.ParquetFile(content_path)
    yielded = 0
    try:
        for batch in pf.iter_batches(
            batch_size=batch_size,
            columns=list(_CONTENT_COLUMNS),
        ):
            ids = batch.column("id").to_pylist()
            htmls = batch.column("content_html").to_pylist()
            for raw_id, html in zip(ids, htmls, strict=True):
                doc_id = _coerce_int(raw_id)
                if doc_id is None:
                    continue
                meta_row = meta_index.get(doc_id)
                if meta_row is None:
                    continue  # orphan content row
                if not html or not str(html).strip():
                    continue
                document = _normalize_row(meta_row, has_full_text=True)
                yield document, str(html)
                yielded += 1
                if limit is not None and yielded >= limit:
                    return
            del batch, ids, htmls
    finally:
        try:
            pf.close()  # type: ignore[no-untyped-call]
        except AttributeError:
            pass
        meta_index.clear()
        gc.collect()


def _iter_metadata_only(
    *,
    data_dir: Path,
    limit: int | None,
) -> Iterator[tuple[LegalDocument, str | None]]:
    """Stream ``metadata.parquet`` only — used by the graph ingest path."""
    path = data_dir / "metadata.parquet"
    pf = pq.ParquetFile(path)
    yielded = 0
    try:
        for batch in pf.iter_batches(
            batch_size=4096, columns=list(_META_COLUMNS)
        ):
            rows = batch.to_pylist()
            for r in rows:
                if _coerce_int(r.get("id")) is None:
                    continue
                document = _normalize_row(r, has_full_text=False)
                yield document, None
                yielded += 1
                if limit is not None and yielded >= limit:
                    return
            del batch, rows
    finally:
        try:
            pf.close()  # type: ignore[no-untyped-call]
        except AttributeError:
            pass
        gc.collect()


def _coerce_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(str(value)))
        except (TypeError, ValueError):
            return None


# ---------------------------------------------------------------------------
# Bulk loaders kept for ad-hoc tooling. Production paths use the streaming
# iterators above.
# ---------------------------------------------------------------------------


def load_metadata(data_dir: Path) -> pd.DataFrame:
    return pd.read_parquet(data_dir / "metadata.parquet")


def load_content(data_dir: Path) -> pd.DataFrame:
    """WARNING: materialises ~3.7 GB. Prefer ``iter_documents()``."""
    df = pd.read_parquet(data_dir / "content.parquet")
    df["id"] = pd.to_numeric(df["id"], errors="coerce").astype("Int64")
    df = df.dropna(subset=["id"])
    df["id"] = df["id"].astype("int64")
    return df


def load_relationships(data_dir: Path) -> pd.DataFrame:
    df = pd.read_parquet(data_dir / "relationships.parquet")
    df["other_doc_id"] = pd.to_numeric(df["other_doc_id"], errors="coerce")
    return df


def iter_relationships(
    *,
    data_dir: Path,
    batch_size: int = 8192,
    limit: int | None = None,
) -> Iterator[tuple[int, int, str]]:
    """Stream ``(doc_id, other_doc_id, relationship)`` triples lazily."""
    path = data_dir / "relationships.parquet"
    pf = pq.ParquetFile(path)
    yielded = 0
    try:
        for batch in pf.iter_batches(
            batch_size=batch_size,
            columns=["doc_id", "other_doc_id", "relationship"],
        ):
            rows = batch.to_pylist()
            for r in rows:
                a = _coerce_int(r.get("doc_id"))
                b = _coerce_int(r.get("other_doc_id"))
                if a is None or b is None:
                    continue
                rel = r.get("relationship") or "RELATED"
                yield a, b, str(rel)
                yielded += 1
                if limit is not None and yielded >= limit:
                    return
            del batch, rows
    finally:
        try:
            pf.close()  # type: ignore[no-untyped-call]
        except AttributeError:
            pass
        gc.collect()


# ---------------------------------------------------------------------------
# Counting helpers (used by ingest progress UI)
# ---------------------------------------------------------------------------


def count_content_documents(data_dir: Path) -> int:
    """Return the number of rows in the (possibly repacked) content parquet.

    This is the upper bound on documents the ingest pipeline will process;
    the actual yielded count is slightly lower because rows with empty
    HTML or orphan IDs are filtered.
    """
    path = _ensure_content_repacked(data_dir) if (data_dir / "content.parquet").exists() else None
    if path is None:
        return 0
    pf = pq.ParquetFile(path)
    return int(pf.metadata.num_rows)


def count_metadata_documents(data_dir: Path) -> int:
    path = data_dir / "metadata.parquet"
    if not path.exists():
        return 0
    pf = pq.ParquetFile(path)
    return int(pf.metadata.num_rows)
