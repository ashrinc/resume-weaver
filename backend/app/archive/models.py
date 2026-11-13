"""Archive data models: the SQLAlchemy table and the API schema (PRD FR-09)."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for the archive's own tables (independent of tailoring)."""


class ArchivedResume(Base):
    __tablename__ = "archived_resumes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_name: Mapped[str] = mapped_column(String(200), nullable=False)
    job_role: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Stored file name relative to the archive upload directory, e.g. "<uuid>.pdf".
    file_path: Mapped[str] = mapped_column(String(500), nullable=False, unique=True)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    upload_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )


class ArchivedResumeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_name: str
    job_role: str | None
    file_path: str
    original_filename: str
    file_size_bytes: int
    upload_timestamp: datetime

    @field_validator("upload_timestamp")
    @classmethod
    def _as_utc(cls, value: datetime) -> datetime:
        # SQLite drops tzinfo; every stored value is UTC, so re-attach it.
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
