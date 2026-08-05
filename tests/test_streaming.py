"""Streaming path tests: LLM.stream, AnswerEngine.stream_answer, run_stream."""

from __future__ import annotations

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
from chat_legal_vn.application.query_pipeline import QueryDeps, QueryPipeline, StreamHandle
from chat_legal_vn.domain.entities import (
    ChunkMetadata,
    EffectiveStatus,
    ParentChunk,
    RetrievalCandidate,
)
from chat_legal_vn.infra.config import Settings
from chat_legal_vn.services.answer import AnswerEngine


def _meta(status: str = "effective") -> ChunkMetadata:
    return ChunkMetadata(
        document_id=1,
        so_ky_hieu="1/2020/NĐ-CP",
        loai_van_ban="Nghị định",
        co_quan_ban_hanh="Chính phủ",
        nganh=None,
        linh_vuc=None,
        ngay_ban_hanh="2020-01-01",
        ngay_co_hieu_luc="2020-01-01",
        ngay_het_hieu_luc=None,
        effective_status=EffectiveStatus(status),
        hierarchical_path="Điều 5",
        chunking_version="v1",
        embedding_version="v1",
    )


def _settings() -> Settings:
    return Settings(
        llm_base_url="http://x/v1",
        llm_api_key="k",
        llm_model_name="m",
        embedding_base_url="http://x/v1",
        embedding_api_key="k",
        embedding_model_name="e",
        reranker_base_url="http://x/v1",
        reranker_api_key="k",
        reranker_model_name="r",
    )  # type: ignore[call-arg]


async def test_echo_llm_stream_yields_tokens() -> None:
    llm = EchoLLM(stream_text="Điều 5 quy định abc")
    out = "".join([d async for d in llm.stream(system="s", user="u")])
    assert out == "Điều 5 quy định abc"


async def test_answer_engine_stream_no_evidence() -> None:
    engine = AnswerEngine(llm=EchoLLM(stream_text="x"))
    chunks = [
        d
        async for d in engine.stream_answer(query="q", candidates=[], parents={})
    ]
    assert "Không tìm thấy quy định" in "".join(chunks)


async def test_answer_engine_stream_with_evidence() -> None:
    engine = AnswerEngine(llm=EchoLLM(stream_text="## Kết luận\nNội dung trả lời."))
    cand = RetrievalCandidate(
        chunk_id="c1",
        parent_chunk_id="p1",
        document_id=1,
        hierarchical_path="Điều 5",
        body="nội dung điều 5",
        score=0.9,
        source="rerank",
        metadata={"effective_status": "effective", "so_ky_hieu": "1/2020/ND-CP"},
    )
    parent = ParentChunk(
        parent_chunk_id="p1",
        document_id=1,
        hierarchical_path="Điều 5",
        heading="Điều 5",
        body="nội dung điều 5",
        summary="tóm tắt",
        metadata=_meta("effective"),
    )
    text = "".join(
        [
            d
            async for d in engine.stream_answer(
                query="q", candidates=[cand], parents={"p1": parent}
            )
        ]
    )
    assert "Nội dung trả lời." in text


async def test_run_stream_exposes_sources_and_stream() -> None:
    deps = QueryDeps(
        settings=_settings(),
        llm=EchoLLM(stream_text="## Kết luận\nCâu trả lời."),
        embedding=HashingEmbedding(dim=64),
        reranker=TokenOverlapReranker(),
        vector_store=InMemoryVectorStore(),
        lexical_store=InMemoryLexicalStore(),
        doc_store=InMemoryDocStore(),
        graph_store=InMemoryGraphStore(),
    )
    pipeline = QueryPipeline(deps)
    handle = await pipeline.run_stream("vượt đèn đỏ bị phạt bao nhiêu?")
    assert isinstance(handle, StreamHandle)
    # Sources available before streaming text (empty corpus → empty list ok).
    assert isinstance(handle.retained, list)
    text = "".join([d async for d in handle.answer_deltas()])
    assert len(text) > 0
    assert "total" in handle.timings


@pytest.mark.parametrize("status", ["expired", "unknown"])
async def test_stream_prepends_effective_warning(status: str) -> None:
    engine = AnswerEngine(llm=EchoLLM(stream_text="body"))
    cand = RetrievalCandidate(
        chunk_id="c1",
        parent_chunk_id="p1",
        document_id=1,
        hierarchical_path="Điều 5",
        body="x",
        score=0.9,
        source="rerank",
        metadata={"effective_status": status},
    )
    parent = ParentChunk(
        parent_chunk_id="p1",
        document_id=1,
        hierarchical_path="Điều 5",
        heading="Điều 5",
        body="x",
        metadata=_meta(status),
    )
    text = "".join(
        [
            d
            async for d in engine.stream_answer(
                query="q", candidates=[cand], parents={"p1": parent}
            )
        ]
    )
    assert "Cảnh báo" in text or "Lưu ý" in text
