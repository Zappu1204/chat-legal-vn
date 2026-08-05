"""Typer CLI for chat-legal-vn."""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .application.factory import build_adapters, build_ingest_pipeline, build_query_pipeline
from .application.graph_pipeline import run_graph_ingest
from .infra.config import get_settings
from .infra.logging import configure_logging

# Lift Python's recursion ceiling well above the default 1000. Several long
# vbpl.vn HTML documents nest > 800 layers of <p>/<div> inside tables and
# the parser converts the result into a Pydantic tree, whose ``model_rebuild``
# step itself burns frames. Doing this once at CLI start guarantees that any
# downstream code path (ingest, ask, smoke test) gets the headroom.
sys.setrecursionlimit(10000)

app = typer.Typer(help="Vietnamese Legal RAG QA — adapter-first.")
console = Console()
# A separate stderr-bound console so progress bars and other diagnostics
# do not pollute stdout. We let Rich auto-detect whether stderr is a real
# TTY: when it is, the live progress bar renders; otherwise the ingest
# pipeline falls back to periodic structured `ingest.progress` log lines
# which survive ``tee`` / redirection.
err_console = Console(stderr=True)


@app.command()
def doctor() -> None:
    """Health-check remote services."""
    settings = get_settings()
    configure_logging(settings)

    async def _run() -> None:
        adapters = build_adapters(settings)
        try:
            results: dict[str, str] = {}
            try:
                v = await adapters.embedding.embed_query("test")
                results["embedding"] = f"OK (dim={len(v)})"
            except Exception as exc:
                results["embedding"] = f"FAIL: {exc!s}"
            try:
                scores = await adapters.reranker.rerank(query="test", documents=["t"])
                results["reranker"] = f"OK ({len(scores)} score(s))"
            except Exception as exc:
                results["reranker"] = f"FAIL: {exc!s}"
            try:
                txt = await adapters.llm.complete(system="ping", user="say hi")
                results["llm"] = f"OK ({len(txt)} chars)"
            except Exception as exc:
                results["llm"] = f"FAIL: {exc!s}"
            try:
                count = await adapters.vector_store.count()
                results["qdrant"] = f"OK (count={count})"
            except Exception as exc:
                msg = str(exc)
                if "doesn't exist" in msg or "Not found" in msg or "404" in msg:
                    results["qdrant"] = (
                        f"OK (collection '{settings.collection_name}' chưa khởi tạo — chạy `clvn ingest`)"
                    )
                else:
                    results["qdrant"] = f"FAIL: {msg}"
            try:
                count = await adapters.lexical_store.count()
                results["elasticsearch"] = f"OK (count={count})"
            except Exception as exc:
                msg = str(exc)
                if "index_not_found" in msg or "404" in msg:
                    results["elasticsearch"] = (
                        f"OK (index '{settings.index_name}' chưa khởi tạo — chạy `clvn ingest`)"
                    )
                else:
                    results["elasticsearch"] = f"FAIL: {msg}"
            try:
                await adapters.graph_store.ensure_schema()
                results["neo4j"] = "OK"
            except Exception as exc:
                results["neo4j"] = f"FAIL: {exc!s}"

            table = Table(title="chat-legal-vn — doctor")
            table.add_column("Service")
            table.add_column("Status")
            for k, v in results.items():
                table.add_row(k, v)
            console.print(table)
        finally:
            await adapters.aclose()

    asyncio.run(_run())


@app.command()
def repack(
    force: bool = typer.Option(
        False, "--force", help="Rebuild even if a fresh repack already exists."
    ),
) -> None:
    """Rewrite content.parquet with small row groups for stream-safe ingest.

    Run this once after downloading the dataset. The source file is left
    intact; a `content.repack.parquet` sibling is created and used by all
    subsequent `ingest` calls. Without this step `clvn ingest --limit N`
    must decompress a 1.9 GiB row group on the very first row, which OOMs
    most workstations.
    """
    settings = get_settings()
    configure_logging(settings)

    from .services.corpus import (
        MAX_SAFE_ROW_GROUP_SIZE,
        REPACK_ROW_GROUP_SIZE,
        _CONTENT_COLUMNS,
        _is_repack_fresh,
        _max_row_group_size,
        repack_parquet,
    )

    data_dir = Path(settings.data_dir)
    source = data_dir / "content.parquet"
    destination = data_dir / "content.repack.parquet"

    if not source.exists():
        console.print(f"[red]Source not found:[/red] {source}")
        raise typer.Exit(1)

    src_max = _max_row_group_size(source)
    table = Table(title="content.parquet — repack plan")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("source", str(source))
    table.add_row("destination", str(destination))
    table.add_row("source max row group", str(src_max))
    table.add_row("safe threshold", str(MAX_SAFE_ROW_GROUP_SIZE))
    table.add_row("target row group size", str(REPACK_ROW_GROUP_SIZE))
    console.print(table)

    if src_max <= MAX_SAFE_ROW_GROUP_SIZE and not force:
        console.print(
            "[green]source already has small row groups; nothing to do.[/green]"
        )
        return
    if _is_repack_fresh(source, destination) and not force:
        console.print(
            f"[green]repack already fresh:[/green] {destination}\n"
            "Re-run with --force to rebuild."
        )
        return

    console.print(
        "[yellow]Repacking… first read of each source row group will spike RAM "
        "(~9 GiB on this corpus). This is a one-time cost.[/yellow]"
    )
    stats = repack_parquet(
        source=source,
        destination=destination,
        columns=_CONTENT_COLUMNS,
        row_group_size=REPACK_ROW_GROUP_SIZE,
    )
    console.print_json(json.dumps({"destination": str(destination), **stats}))


@app.command()
def ingest(
    limit: int = typer.Option(0, help="Limit number of documents to process (0 = all)."),
    flush_every: int = typer.Option(
        16, help="Flush buffer to vector/lexical/doc stores every N processed documents."
    ),
    skip_existing: bool = typer.Option(
        True,
        "--skip-existing/--no-skip-existing",
        help=(
            "Skip documents whose document_id already has chunks in Qdrant. "
            "Enables idempotent resume after Ctrl+C / crashes."
        ),
    ),
    progress: bool = typer.Option(
        True,
        "--progress/--no-progress",
        help="Render a Rich progress bar to stderr.",
    ),
    recreate: bool = typer.Option(
        False,
        "--recreate",
        help=(
            "Drop and recreate the vector/lexical/doc stores before ingesting. "
            "Use when changing CHUNKING_VERSION so stale chunks from a previous "
            "scheme don't pollute retrieval. Implies --no-skip-existing."
        ),
    ),
) -> None:
    """Run the offline indexing pipeline."""
    settings = get_settings()
    configure_logging(settings)

    async def _run() -> None:
        adapters = build_adapters(settings)
        try:
            pipeline = build_ingest_pipeline(adapters)
            stats = await pipeline.run(
                limit=limit if limit > 0 else None,
                flush_every=flush_every,
                skip_existing=skip_existing and not recreate,
                show_progress=progress,
                console=err_console,
                recreate=recreate,
            )
            console.print_json(json.dumps(asdict(stats)))
        finally:
            await adapters.aclose()

    asyncio.run(_run())


@app.command("graph-build")
def graph_build() -> None:
    """Build the cross-reference graph in Neo4j."""
    settings = get_settings()
    configure_logging(settings)

    async def _run() -> None:
        adapters = build_adapters(settings)
        try:
            stats = await run_graph_ingest(
                settings=adapters.settings, graph=adapters.graph_store
            )
            console.print_json(json.dumps(asdict(stats)))
        finally:
            await adapters.aclose()

    asyncio.run(_run())


@app.command()
def ask(query: str = typer.Argument(..., help="Câu hỏi pháp luật")) -> None:
    """Ask a legal question."""
    settings = get_settings()
    configure_logging(settings)

    async def _run() -> None:
        adapters = build_adapters(settings)
        try:
            pipeline = build_query_pipeline(adapters)
            result = await pipeline.run(query)
            console.rule("Câu trả lời")
            console.print(result.answer.short_answer, style="bold")
            for issue in result.answer.issues:
                console.rule(issue.issue or "Vấn đề")
                console.print(f"[bold]Quy tắc:[/bold] {issue.rule}")
                console.print(f"[bold]Áp dụng:[/bold] {issue.application}")
                console.print(f"[bold]Kết luận:[/bold] {issue.conclusion}")
                if issue.citations:
                    console.print("Trích dẫn:", ", ".join(issue.citations))
            if result.answer.final_conclusion:
                console.rule("Kết luận cuối")
                console.print(result.answer.final_conclusion)
            if result.answer.warnings:
                console.print("[yellow]Cảnh báo:[/yellow]", result.answer.warnings)
            console.print("[dim]" + result.answer.disclaimer + "[/dim]")
        finally:
            await adapters.aclose()

    asyncio.run(_run())


if __name__ == "__main__":
    app()
