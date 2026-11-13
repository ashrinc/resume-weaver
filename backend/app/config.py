from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/ (this file is backend/app/config.py). Local data paths resolve
# against it, so they don't depend on the directory uvicorn is started from.
BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """App settings loaded from environment variables / backend/.env."""

    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash"
    cors_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]

    # Tailoring latency (M11.3). The semantic verifier is a second LLM call
    # (~12 s plus retries); off by default. Layer A guards always run.
    enable_verify_call: bool = False
    # Overall wall-clock limit for POST /api/tailor, retries included.
    tailor_deadline_seconds: float = 90.0

    # Resume archive (M9): PDFs on the local filesystem, metadata in SQLite.
    archive_upload_dir: Path = BACKEND_DIR / "uploads" / "resumes"
    archive_database_url: str = f"sqlite+aiosqlite:///{BACKEND_DIR / 'data' / 'app.db'}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
