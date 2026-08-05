"""Shared column helpers for SQLModel tables.

`tz_column` declares a timezone-aware TIMESTAMP (``timestamptz`` on Postgres).
Without this, SQLModel maps ``datetime`` to ``TIMESTAMP WITHOUT TIME ZONE`` and
asyncpg refuses to encode timezone-aware values
("can't subtract offset-naive and offset-aware datetimes"). Storing tz-aware
timestamps is the correct behavior and round-trips cleanly on both Postgres and
SQLite (tests).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Column, DateTime


def utcnow() -> datetime:
    return datetime.now(tz=UTC)


def tz_column(*, index: bool = False) -> Column:
    return Column(DateTime(timezone=True), nullable=False, index=index)
