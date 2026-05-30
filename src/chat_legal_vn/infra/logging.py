"""Structured logging using `structlog`.

Secrets (API keys, passwords) MUST never appear in log output.
"""

from __future__ import annotations

import logging
import sys

import structlog

from .config import Settings


def configure_logging(settings: Settings) -> None:
    """Configure structlog + stdlib logging with the level from settings."""
    level = getattr(logging, settings.log_level)

    logging.basicConfig(
        level=level,
        stream=sys.stderr,
        format="%(message)s",
    )

    # Silence noisy 3rd-party INFO loggers — they swamp the progress bar
    # and don't add information beyond what our own structured logs cover.
    for noisy in (
        "httpx",
        "httpcore",
        "openai",
        "elastic_transport",
        "elasticsearch",
        "qdrant_client",
        "neo4j",
        "neo4j.io",
        "neo4j.notifications",
        "urllib3",
    ):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
