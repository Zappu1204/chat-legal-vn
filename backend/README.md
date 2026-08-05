# chat-legal-vn — Backend (FastAPI delivery layer)

Wraps the RAG core in `src/chat_legal_vn` with web concerns: authentication,
RBAC, a Postgres-backed **dynamic configuration** store with **hot-reloading**,
and a **streaming chat API** that emits retrieved sources before the answer
text.

## Design decisions (SOTA, 2026)

- **Auth:** argon2id password hashing + PyJWT (access/refresh) with a role
  column for RBAC (`user` / `admin`). Lean and fully controllable vs a heavy
  auth framework.
- **Streaming wire format:** typed **Server-Sent Events** —
  `meta` → `sources` → `text-delta`* → `citations` → `done` (or `error`).
  Stable and self-describing; the frontend branches on the event name. This is
  more robust for a RAG "sources + answer" UX than coupling to a third-party
  stream protocol that is mid-migration.
- **Hot-reload:** `AdapterRegistry` builds a fresh adapter bundle from the new
  settings, swaps the reference atomically under a lock, and drains+closes the
  old bundle so in-flight requests are never interrupted.
- **Clean architecture preserved:** the RAG domain is untouched; this layer
  only consumes the application factory and the typed ports.

## Layout

```
backend/
├── app/
│   ├── main.py                 # app factory + lifespan (schema, admin seed, registry)
│   ├── core/{settings,security,logging}.py
│   ├── db/session.py           # async engine + session factory
│   ├── models/                 # SQLModel tables: user, system_config, conversation
│   ├── schemas/                # Pydantic request/response models
│   ├── services/
│   │   ├── adapter_registry.py # hot-reloadable adapter bundle
│   │   ├── config_service.py   # DB config ↔ core Settings + reload
│   │   ├── chat_service.py     # QueryPipeline stream → SSE events
│   │   └── pipeline_jobs.py    # in-process ingest/graph-build job tracker
│   └── api/v1/                 # auth, chat, conversations, admin_{config,users,pipeline}
├── alembic/                    # migrations (prod); dev uses create_all
└── tests/                      # end-to-end tests on SQLite + fakes
```

## Run (dev)

```bash
# 1. Start backing services (adds Postgres to the existing compose stack)
docker compose up -d postgres qdrant elasticsearch neo4j

# 2. Install backend deps into the project venv
source .venv/bin/activate
uv pip install -e ".[dev]"          # core
uv pip install -e "backend/[dev]"   # backend

# 3. Configure: copy and edit both env files (core + backend)
cp .env.example .env                 # core: LLM/embedding/store endpoints
cp backend/.env.example backend/.env # backend: DB, JWT, CORS, admin

# 4. Run the API (schema auto-created, bootstrap admin seeded on first boot)
uvicorn app.main:app --reload --port 8080 --app-dir backend
# OpenAPI docs: http://localhost:8080/docs
```

## Auth flow

```
POST /api/v1/auth/register  → { access_token, refresh_token }
POST /api/v1/auth/login     → { access_token, refresh_token }
POST /api/v1/auth/refresh   → { access_token, refresh_token }
GET  /api/v1/auth/me        → current user
```

## Streaming chat

```
POST /api/v1/chat/stream   (Authorization: Bearer <access>)
body: { "query": "...", "conversation_id": "<uuid?>" }
→ text/event-stream:
   event: meta        data: {"conversation_id": "..."}
   event: sources     data: {"sources": [SourceCard, ...]}
   event: text-delta  data: {"delta": "..."}     (repeated)
   event: citations   data: {"citations": [...], "warnings": [...]}
   event: done        data: {"timings": {...}, "revision": N}
```

## Admin (role = admin)

```
GET  /api/v1/admin/config            → masked config (secrets as booleans)
PUT  /api/v1/admin/config            → update + hot-reload (returns new revision)
GET  /api/v1/admin/users             → list users
PUT  /api/v1/admin/users/{id}/role   → change role
POST /api/v1/admin/pipeline/ingest   → trigger ingest (async job)
GET  /api/v1/admin/pipeline/jobs/{id}→ poll job status
```

## Tests

```bash
PYTHONPATH=backend pytest backend/tests -q   # SQLite + fakes, no live infra
```

## Security notes

- All `/chat`, `/conversations`, and `/admin` routes require a valid Bearer
  access token; admin routes additionally require the `admin` role.
- Config secrets (LLM/embedding/reranker API keys) are **write-only** over the
  API — reads report only whether a key is set.
- Set a strong `CLVN_JWT_SECRET` (32+ bytes) and change the bootstrap admin
  password before exposing the service.
