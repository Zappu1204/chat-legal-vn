"""Web-layer settings.

Distinct from `chat_legal_vn.infra.config.Settings` (which configures the RAG
core). These cover only delivery concerns: the database URL, JWT signing, CORS
origins, and the bootstrap admin account. Loaded from environment variables
prefixed with ``CLVN_`` so they never collide with the core's `.env` keys.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve `backend/.env` by absolute path so the file is found regardless of
# the process working directory. Running `uvicorn ... --app-dir backend` does
# NOT change the CWD (it only extends sys.path), so a relative ``.env`` would
# resolve against the repo root and silently miss this file. settings.py lives
# at backend/app/core/settings.py → parents[2] is the `backend/` dir.
_BACKEND_DIR = Path(__file__).resolve().parents[2]
_ENV_FILE = _BACKEND_DIR / ".env"


class WebSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CLVN_",
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ----- Database ----------------------------------------------------------
    # Async SQLAlchemy URL. Defaults to the docker-compose Postgres service.
    database_url: str = Field(
        default="postgresql+asyncpg://clvn:clvn@localhost:5432/clvn",
    )
    db_echo: bool = False

    # ----- JWT ---------------------------------------------------------------
    # MUST be overridden in production via CLVN_JWT_SECRET.
    jwt_secret: SecretStr = Field(default=SecretStr("dev-only-change-me"))
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 30
    refresh_token_ttl_days: int = 14

    # ----- CORS --------------------------------------------------------------
    # Comma-separated origins, e.g. "http://localhost:3000,https://app.example".
    cors_origins: str = "http://localhost:3000"

    # ----- Bootstrap admin ---------------------------------------------------
    # Created on first startup if no admin exists. Change the password
    # immediately after first login in any real deployment.
    bootstrap_admin_email: str = "admin@chat-legal-vn.local"
    bootstrap_admin_password: SecretStr = SecretStr("ChangeMe!123")

    # ----- App ---------------------------------------------------------------
    api_prefix: str = "/api/v1"
    project_name: str = "chat-legal-vn"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_web_settings() -> WebSettings:
    return WebSettings()
