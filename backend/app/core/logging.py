"""Thin logging shim over the RAG core's structlog setup."""

from __future__ import annotations

from chat_legal_vn.infra.logging import get_logger

__all__ = ["get_logger"]
