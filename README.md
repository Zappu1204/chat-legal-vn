# chat-legal-vn

Vietnamese Legal Document Question-Answering with Advanced RAG.

- **Dataset:** [`th1nhng0/vietnamese-legal-documents`](https://huggingface.co/datasets/th1nhng0/vietnamese-legal-documents)
- **LLM:** `mimo-v2.5-pro` (MIMO API, OpenAI-compatible)
- **Embedding:** `Qwen/Qwen3-Embedding-8B` (vLLM, 4096-dim, 32k context, MRL-capable)
- **Reranker:** `Qwen/Qwen3-Reranker-8B` (vLLM, `Qwen3ForSequenceClassification`, 32k context)
- **Vector store:** Qdrant — **Lexical:** Elasticsearch — **Graph:** Neo4j

## Architecture

Adapter-first Clean Architecture. Domain and use-case layers depend only on
ports defined in `chat_legal_vn.domain.ports`. Vendor SDKs are confined to
`chat_legal_vn.adapters.*`.

```
src/chat_legal_vn/
├── domain/         # entities, value objects, ports (no SDK imports)
├── application/    # use-case orchestrators (ingest, query, evaluate)
├── services/       # domain services (chunker, query planner, RRF, judge)
├── adapters/       # concrete adapters (Qdrant, ES, Neo4j, vLLM, MIMO)
├── prompts/        # prompt templates with version stamps
├── infra/          # config, logging, observability
├── api/            # optional FastAPI surface
└── cli.py          # Typer CLI: ingest / query / evaluate
```

## Quick start

```bash
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"

# fill in .env from .env.example (do NOT commit .env)
cp .env.example .env

# health-check remote services
chat-legal-vn doctor

# ingest the corpus (offline indexing)
chat-legal-vn ingest --limit 1000     # smoke test
chat-legal-vn ingest                  # full corpus

# build the cross-reference graph
chat-legal-vn graph-build

# ask a question
chat-legal-vn ask "Đi xe máy vượt đèn đỏ và không đội mũ bảo hiểm bị phạt thế nào?"

# launch the API
uvicorn chat_legal_vn.api.main:app --host 0.0.0.0 --port 8080
```

## Pipelines

1. **Offline indexing.** Parquet → metadata normalization → HTML parsing
   (Phần→Chương→Mục→Điều→Khoản→Điểm) → parent-child chunking → embedding (Qwen3
   with task-aware instruction prefix) → Qdrant + Elasticsearch indexing →
   Neo4j cross-reference graph.
2. **Online QA.** User query → query planner (normalize, intent, decompose,
   HyDE) → hybrid retrieval (dense + BM25, RRF fused, metadata filters) →
   cross-encoder reranker (`/v1/rerank`) → optional graph expansion →
   evidence packaging → IRAC-structured answer with strict citations →
   citation resolver + answer judge.
3. **Evaluation.** Recall@k, MRR, nDCG, Article-Hit-Rate, Faithfulness,
   Citation-Precision, Abstention-Rate.

## License

Source code: Apache-2.0. Dataset: CC-BY-4.0.

## Web application (Backend + Frontend)

Beyond the RAG core and CLI, the project ships a production-style web stack:

- **`backend/`** — FastAPI delivery layer: JWT auth + RBAC (`user`/`admin`),
  a Postgres-backed dynamic config store with **zero-downtime hot-reload** of
  the adapter pipeline, and a **streaming chat API** (SSE) that emits retrieved
  source cards *before* the answer text. See `backend/README.md`.
- **`frontend/`** — Next.js 15 (App Router, React 19) chat UI with interactive
  source cards, IRAC markdown rendering, and an admin dashboard (config editor,
  user management, ingest trigger). See `frontend/README.md`.

The core itself gained a streaming path (`LLMAdapter.stream`,
`AnswerEngine.stream_answer`, `QueryPipeline.run_stream`) used by the web layer;
the blocking `run()` JSON path is unchanged.

```
chat-legal-vn/
├── src/chat_legal_vn/   # RAG core (domain, application, adapters)
├── backend/             # FastAPI: auth, dynamic config, streaming chat
└── frontend/            # Next.js 15 web UI + admin dashboard
```

### Quick start (full stack)

```bash
# Backing services (Qdrant, Elasticsearch, Neo4j, Postgres)
docker compose up -d

# Backend
uv pip install -e ".[dev]" && uv pip install -e "backend/[dev]"
cp .env.example .env && cp backend/.env.example backend/.env
uvicorn app.main:app --port 8080 --app-dir backend

# Frontend
cd frontend && npm install && npm run dev   # http://localhost:3000
```
