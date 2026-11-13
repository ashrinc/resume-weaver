import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# backend/ (this file is backend/app/config.py). Local data paths resolve
# against it, so they don't depend on the directory uvicorn is started from.
BACKEND_DIR = Path(__file__).resolve().parent.parent

_POSTGRES_PREFIXES = ("postgres://", "postgresql://", "postgresql+psycopg2://")


def normalize_database_url(url: str) -> str:
    """Hosted Postgres URLs (Supabase, Heroku-style "postgres://") -> the async
    psycopg driver. Anything else (SQLite, explicit drivers) is unchanged."""
    url = url.strip()
    for prefix in _POSTGRES_PREFIXES:
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


def is_postgres_url(url: str) -> bool:
    return url.startswith("postgresql")


class Settings(BaseSettings):
    """App settings loaded from environment variables / backend/.env."""

    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash"
    # Comma-separated or a JSON list, e.g. "https://resume-tailr.vercel.app".
    cors_origins: Annotated[list[str], NoDecode] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]

    # Deployment. "production" requires ACCESS_PASSWORD and hides /docs.
    app_env: Literal["development", "production"] = "development"
    # Single-user access control: empty = off (local development only).
    access_password: str = ""
    # Per client IP; 0 disables. Protects the free-tier Gemini quota.
    tailor_rate_limit_per_hour: int = 30
    login_attempts_per_15_minutes: int = 10

    # Tailoring latency (M11.3). The semantic verifier is a second LLM call
    # (~12 s plus retries); off by default. Layer A guards always run.
    enable_verify_call: bool = False
    # Overall wall-clock limit for POST /api/tailor, retries included.
    tailor_deadline_seconds: float = 240.0

    # Resume archive metadata. Local SQLite by default; set DATABASE_URL to a
    # Postgres URL (e.g. Supabase) to deploy. ARCHIVE_DATABASE_URL still works.
    database_url: str = Field(
        default=f"sqlite+aiosqlite:///{BACKEND_DIR / 'data' / 'app.db'}",
        validation_alias=AliasChoices("DATABASE_URL", "ARCHIVE_DATABASE_URL"),
    )

    # Archive file storage: "local" (archive_upload_dir) or "supabase" (private bucket).
    storage_backend: Literal["local", "supabase"] = "local"
    archive_upload_dir: Path = BACKEND_DIR / "uploads" / "resumes"
    supabase_url: str = ""
    supabase_service_key: str = ""
    supabase_bucket: str = ""

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_origins(cls, value: object) -> object:
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("["):
                return json.loads(text)
            return [o.strip().rstrip("/") for o in text.split(",") if o.strip()]
        return value

    @model_validator(mode="after")
    def _production_needs_password(self) -> "Settings":
        if self.app_env == "production" and len(self.access_password) < 12:
            raise ValueError(
                "APP_ENV=production requires ACCESS_PASSWORD (at least 12 characters)."
            )
        return self

    @field_validator("database_url")
    @classmethod
    def _normalize_database_url(cls, value: str) -> str:
        return normalize_database_url(value)


@lru_cache
def get_settings() -> Settings:
    return Settings()
