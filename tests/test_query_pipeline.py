"""End-to-end test of the QueryPipeline using in-memory fakes."""

from __future__ import annotations

from datetime import date

import pytest

from chat_legal_vn.adapters.fakes import (
    EchoLLM,
    HashingEmbedding,
    InMemoryDocStore,
    InMemoryGraphStore,
    InMemoryLexicalStore,
    InMemoryVectorStore,
    TokenOverlapReranker,
)
from chat_legal_vn.application.query_pipeline import QueryDeps, QueryPipeline
from chat_legal_vn.domain.entities import EffectiveStatus, LegalDocument
from chat_legal_vn.infra.config import Settings
from chat_legal_vn.services.chunker import HierarchicalChunker
from chat_legal_vn.services.html_parser import parse_html

HTML = """
<div>
  <p>Điều 6. Xử phạt vi phạm hiệu lệnh đèn tín hiệu</p>
  <p>1. Phạt tiền từ 4.000.000 đồng đến 6.000.000 đồng đối với người điều
     khiển xe mô tô không chấp hành hiệu lệnh của đèn tín hiệu giao thông.</p>
  <p>Điều 7. Xử phạt không đội mũ bảo hiểm</p>
  <p>1. Phạt tiền từ 400.000 đồng đến 600.000 đồng đối với người điều khiển,
     người ngồi sau xe mô tô không đội mũ bảo hiểm hoặc đội mũ bảo hiểm
     không cài quai đúng quy cách.</p>
</div>
"""


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    # Force pydantic-settings to skip .env so tests don't read the user file.
    env = {
        "LLM_BASE_URL": "http://x",
        "LLM_API_KEY": "x",
        "LLM_MODEL_NAME": "fake",
        "EMBEDDING_BASE_URL": "http://x",
        "EMBEDDING_API_KEY": "x",
        "EMBEDDING_MODEL_NAME": "fake",
        "EMBEDDING_DIM": "256",
        "RERANKER_BASE_URL": "http://x",
        "RERANKER_API_KEY": "x",
        "RERANKER_MODEL_NAME": "fake",
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return Settings(_env_file=None)  # type: ignore[call-arg]


@pytest.mark.asyncio
async def test_pipeline_returns_grounded_answer(settings: Settings) -> None:
    doc = LegalDocument(
        document_id=1,
        title="Nghị định mẫu",
        so_ky_hieu="100/2019/NĐ-CP",
        loai_van_ban="Nghị định",
        co_quan_ban_hanh="Chính phủ",
        ngay_ban_hanh=date(2019, 12, 30),
        effective_status=EffectiveStatus.EFFECTIVE,
        has_full_text=True,
    )
    root = parse_html(document_id=1, document_title=doc.title, html=HTML)
    chunker = HierarchicalChunker(
        chunking_version="v1",
        embedding_version="emb-v1",
        max_input_tokens=8192,
    )
    parents, children = chunker.chunk(document=doc, root=root)

    embedding = HashingEmbedding(dim=256)
    vec_store = InMemoryVectorStore()
    lex_store = InMemoryLexicalStore()
    doc_store = InMemoryDocStore()
    graph = InMemoryGraphStore()
    reranker = TokenOverlapReranker()

    vectors = await embedding.embed_documents([c.text_for_embedding for c in children])
    await vec_store.upsert(chunks=children, vectors=vectors)
    await lex_store.upsert(children)
    await doc_store.upsert(parents)
    await graph.upsert_documents([doc])

    json_response = {
        "short_answer": "Có hai vi phạm: vượt đèn đỏ và không đội mũ bảo hiểm.",
        "issues": [
            {
                "issue": "Vượt đèn đỏ",
                "rule": "Theo [Nghị định 100/2019/NĐ-CP - Điều 6 - Khoản 1].",
                "application": "Hành vi không chấp hành đèn tín hiệu.",
                "conclusion": "Phạt tiền 4–6 triệu đồng.",
                "citations": ["[Nghị định 100/2019/NĐ-CP - Điều 6 - Khoản 1]"],
            },
            {
                "issue": "Không đội mũ bảo hiểm",
                "rule": "Theo [Nghị định 100/2019/NĐ-CP - Điều 7 - Khoản 1].",
                "application": "Người điều khiển xe mô tô không đội mũ.",
                "conclusion": "Phạt tiền 400–600 nghìn đồng.",
                "citations": ["[Nghị định 100/2019/NĐ-CP - Điều 7 - Khoản 1]"],
            },
        ],
        "legal_basis": ["[Nghị định 100/2019/NĐ-CP - Điều 6]", "[Nghị định 100/2019/NĐ-CP - Điều 7]"],
        "analysis": "...",
        "exceptions_or_conditions": "",
        "final_conclusion": "Tổng tiền phạt sẽ là tổng của hai mức phạt nêu trên.",
        "confidence": 0.9,
        "needs_human_review": False,
        "warnings": [],
    }
    llm = EchoLLM(json_response=json_response)

    pipeline = QueryPipeline(
        QueryDeps(
            settings=settings,
            llm=llm,
            embedding=embedding,
            reranker=reranker,
            vector_store=vec_store,
            lexical_store=lex_store,
            doc_store=doc_store,
            graph_store=graph,
        )
    )

    result = await pipeline.run(
        "Đi xe máy vượt đèn đỏ và không đội mũ bảo hiểm bị phạt thế nào?"
    )

    assert result.answer.short_answer
    assert len(result.answer.issues) == 2
    assert any("Điều 6" in c.canonical() for c in result.answer.citations)
    assert any("Điều 7" in c.canonical() for c in result.answer.citations)
    assert not result.answer.needs_human_review
