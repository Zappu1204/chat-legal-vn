"""Test harness: SQLite DB + fakes-backed adapter registry, no live infra.

We patch ``build_adapters`` (used by the AdapterRegistry) to return a bundle
of in-memory fakes seeded with one legal chunk, so the full HTTP surface —
auth, RBAC, SSE streaming, config hot-reload — can be exercised end-to-end
without Qdrant/ES/Neo4j/LLM endpoints.
"""

from __future__ import annotations

import os
import tempfile

import pytest

_DB_FILE = os.path.join(tempfile.gettempdir(), "clvn_backend_test.db")
if os.path.exists(_DB_FILE):
    os.remove(_DB_FILE)

os.environ.setdefault("CLVN_DATABASE_URL", f"sqlite+aiosqlite:///{_DB_FILE}")
os.environ.setdefault("CLVN_JWT_SECRET", "test-secret")
os.environ.setdefault("CLVN_BOOTSTRAP_ADMIN_EMAIL", "admin@example.com")
os.environ.setdefault("CLVN_BOOTSTRAP_ADMIN_PASSWORD", "AdminPass!123")
# Core settings (required fields) — point at unused localhost endpoints.
os.environ.setdefault("LLM_BASE_URL", "http://localhost:1/v1")
os.environ.setdefault("LLM_API_KEY", "k")
os.environ.setdefault("LLM_MODEL_NAME", "m")
os.environ.setdefault("EMBEDDING_BASE_URL", "http://localhost:1/v1")
os.environ.setdefault("EMBEDDING_API_KEY", "k")
os.environ.setdefault("EMBEDDING_MODEL_NAME", "e")
os.environ.setdefault("RERANKER_BASE_URL", "http://localhost:1/v1")
os.environ.setdefault("RERANKER_API_KEY", "k")
os.environ.setdefault("RERANKER_MODEL_NAME", "r")

from chat_legal_vn.adapters import fakes  # noqa: E402
from chat_legal_vn.application import factory  # noqa: E402
from chat_legal_vn.application.factory import LiveAdapters  # noqa: E402
from chat_legal_vn.domain.entities import (  # noqa: E402
    ChildChunk,
    ChunkMetadata,
    EffectiveStatus,
    ParentChunk,
)
from chat_legal_vn.infra.config import get_settings  # noqa: E402


def _seeded_fakes() -> tuple:
    vector = fakes.InMemoryVectorStore()
    lexical = fakes.InMemoryLexicalStore()
    docstore = fakes.InMemoryDocStore()
    graph = fakes.InMemoryGraphStore()
    embedding = fakes.HashingEmbedding(dim=64)

    meta = ChunkMetadata(
        document_id=168,
        so_ky_hieu="168/2024/NĐ-CP",
        loai_van_ban="Nghị định",
        co_quan_ban_hanh="Chính phủ",
        nganh=None,
        linh_vuc=None,
        ngay_ban_hanh="2024-12-26",
        ngay_co_hieu_luc="2025-01-01",
        ngay_het_hieu_luc=None,
        effective_status=EffectiveStatus.EFFECTIVE,
        hierarchical_path="Điều 7 > Khoản 2 > Điểm h",
        chunking_version="v1",
        embedding_version="v1",
    )
    child = ChildChunk(
        chunk_id="c-168-7-2-h",
        parent_chunk_id="p-168-7",
        document_id=168,
        hierarchical_path="Điều 7 > Khoản 2 > Điểm h",
        body="Phạt tiền đối với người điều khiển xe vượt đèn đỏ và không đội mũ bảo hiểm.",
        text_for_embedding="Điều 7 Khoản 2 Điểm h vượt đèn đỏ mũ bảo hiểm phạt tiền",
        metadata=meta,
    )
    parent = ParentChunk(
        parent_chunk_id="p-168-7",
        document_id=168,
        hierarchical_path="Điều 7",
        heading="Điều 7. Xử phạt vi phạm",
        body="Quy định xử phạt các hành vi vi phạm giao thông đường bộ.",
        summary="Xử phạt vi phạm giao thông",
        metadata=meta,
    )

    return vector, lexical, docstore, graph, embedding, child, parent


@pytest.fixture(autouse=True)
def patch_adapters(monkeypatch: pytest.MonkeyPatch) -> None:
    vector, lexical, docstore, graph, embedding, child, parent = _seeded_fakes()

    import asyncio

    async def _seed() -> None:
        v = await embedding.embed_documents([child.text_for_embedding])
        await vector.upsert(chunks=[child], vectors=v)
        await lexical.upsert([child])
        await docstore.upsert([parent])

    asyncio.get_event_loop().run_until_complete(_seed())

    def _fake_build_adapters(settings=None):  # noqa: ANN001, ANN202
        s = settings or get_settings()
        return LiveAdapters(
            settings=s,
            llm=fakes.EchoLLM(
                stream_text=(
                    "**Trả lời nhanh:** Bị phạt tiền.\n\n"
                    "## Kết luận\nTheo [Nghị định 168/2024/NĐ-CP - Điều 7 - Khoản 2 - Điểm h] "
                    "hành vi này bị xử phạt."
                )
            ),
            embedding=embedding,
            reranker=fakes.TokenOverlapReranker(),
            vector_store=vector,
            lexical_store=lexical,
            doc_store=docstore,
            graph_store=graph,
        )

    # Patch the symbol used by the registry (imported into its namespace).
    monkeypatch.setattr(
        "app.services.adapter_registry.build_adapters", _fake_build_adapters
    )
    monkeypatch.setattr(factory, "build_adapters", _fake_build_adapters)
