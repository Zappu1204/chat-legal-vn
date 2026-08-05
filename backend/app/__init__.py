"""chat-legal-vn web delivery layer (FastAPI).

This package wraps the RAG core in `chat_legal_vn` with web concerns:
authentication, RBAC, a Postgres-backed dynamic configuration store with
hot-reloading, and a streaming chat API that emits retrieved sources before
the answer text. The RAG domain itself is never imported into the web layer
except through the application factory and the typed ports.
"""
