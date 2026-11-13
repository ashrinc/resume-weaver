"""Resume archive service (PRD FR-09): local PDF storage + SQLite metadata.

Fully independent of the tailoring feature: its own tables, engine and
directory, and no imports from ``app.tailor``.

Upload rules
------------
* Company name required (non-blank, max 200 chars); job role optional.
* The file must have a ``.pdf`` extension AND an ``application/pdf`` MIME
  type, start with the ``%PDF-`` signature, be non-empty and be at most
  ``MAX_UPLOAD_BYTES`` (10 MB).
* The upload is streamed to ``<uuid>.pdf.part``, checked chunk by chunk, then
  renamed to ``<uuid>.pdf`` before the DB row is written. Any failure removes
  the file, so rejected uploads leave nothing behind. The client's file name
  is only stored as metadata (sanitised), never used as a path.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import anyio
from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.archive.models import ArchivedResume, ArchivedResumeOut, Base

__all__ = [
    "ALLOWED_MIME_TYPES",
    "MAX_UPLOAD_BYTES",
    "ArchiveNotFoundError",
    "ArchiveService",
    "ArchiveValidationError",
]

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
ALLOWED_MIME_TYPES = {"application/pdf", "application/x-pdf"}
PDF_SIGNATURE = b"%PDF-"
MAX_NAME_CHARS = 200
_CHUNK_BYTES = 1024 * 1024
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")


class ArchiveValidationError(Exception):
    """Invalid upload. Maps to HTTP 422."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field
        self.message = message


class ArchiveNotFoundError(Exception):
    """No such archived resume, or its file is missing. Maps to HTTP 404."""


def _sanitize_filename(name: str | None) -> str:
    """Keep only the base name, drop control characters, cap the length."""
    base = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    base = _CONTROL_CHARS_RE.sub("", base).strip()
    if len(base) > 255:
        stem, dot, ext = base.rpartition(".")
        base = (stem[: 250 - len(ext)] + dot + ext) if dot else base[:255]
    return base or "resume.pdf"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ArchiveService:
    def __init__(
        self,
        upload_dir: Path,
        database_url: str,
        *,
        max_upload_bytes: int = MAX_UPLOAD_BYTES,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self.upload_dir = Path(upload_dir)
        self.database_url = database_url
        self.max_upload_bytes = max_upload_bytes
        self._clock = clock
        # NullPool: SQLite connections are cheap, and pooled aiosqlite
        # connections must not be reused across event loops (tests, reloads).
        self._engine: AsyncEngine = create_async_engine(database_url, poolclass=NullPool)
        self._sessions = async_sessionmaker(self._engine, expire_on_commit=False)
        self._ready = False

    # ------------------------------------------------------------ lifecycle

    async def init(self) -> None:
        """Create the upload directory and tables. Idempotent."""
        if self._ready:
            return
        await anyio.Path(self.upload_dir).mkdir(parents=True, exist_ok=True)
        db_path = make_url(self.database_url).database
        if db_path and db_path != ":memory:":
            await anyio.Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self._ready = True

    async def close(self) -> None:
        await self._engine.dispose()

    # ------------------------------------------------------------ queries

    async def list(self) -> list[ArchivedResumeOut]:
        async with self._sessions() as session:
            rows = await session.scalars(
                select(ArchivedResume).order_by(
                    ArchivedResume.upload_timestamp.desc(), ArchivedResume.id.desc()
                )
            )
            return [ArchivedResumeOut.model_validate(r) for r in rows]

    async def get(self, resume_id: int) -> ArchivedResumeOut:
        async with self._sessions() as session:
            row = await session.get(ArchivedResume, resume_id)
            if row is None:
                raise ArchiveNotFoundError(f"No archived resume with id {resume_id}.")
            return ArchivedResumeOut.model_validate(row)

    def path_for(self, record: ArchivedResumeOut) -> Path:
        """Absolute path of a record's file, guaranteed to be inside upload_dir."""
        root = self.upload_dir.resolve()
        path = (root / record.file_path).resolve()
        if not path.is_relative_to(root):
            raise ArchiveNotFoundError(f"The file for archived resume {record.id} is missing.")
        return path

    async def file_for(self, resume_id: int) -> tuple[Path, ArchivedResumeOut]:
        record = await self.get(resume_id)
        path = self.path_for(record)
        if not await anyio.Path(path).is_file():
            raise ArchiveNotFoundError(f"The file for archived resume {resume_id} is missing.")
        return path, record

    # ------------------------------------------------------------ commands

    async def create(
        self, *, company_name: str, job_role: str | None, upload: UploadFile
    ) -> ArchivedResumeOut:
        company = (company_name or "").strip()
        if not company:
            raise ArchiveValidationError("company_name", "Company name is required.")
        if len(company) > MAX_NAME_CHARS:
            raise ArchiveValidationError(
                "company_name", f"Company name must be at most {MAX_NAME_CHARS} characters."
            )
        role = (job_role or "").strip() or None
        if role is not None and len(role) > MAX_NAME_CHARS:
            raise ArchiveValidationError(
                "job_role", f"Job role must be at most {MAX_NAME_CHARS} characters."
            )

        original = _sanitize_filename(upload.filename)
        if not original.lower().endswith(".pdf"):
            raise ArchiveValidationError("file", "Only .pdf files are accepted.")
        mime = (upload.content_type or "").split(";", 1)[0].strip().lower()
        if mime not in ALLOWED_MIME_TYPES:
            raise ArchiveValidationError(
                "file", f"The file's type must be application/pdf (got {mime or 'none'})."
            )

        stored_name = f"{uuid.uuid4().hex}.pdf"
        final_path = self.upload_dir / stored_name
        size = await self._store(upload, final_path)

        record = ArchivedResume(
            company_name=company,
            job_role=role,
            file_path=stored_name,
            original_filename=original,
            file_size_bytes=size,
            upload_timestamp=self._clock(),
        )
        try:
            async with self._sessions() as session:
                session.add(record)
                await session.commit()
                await session.refresh(record)
        except BaseException:
            await self._unlink(final_path)
            raise
        logger.info("archive: stored resume id=%d (%d bytes)", record.id, size)
        return ArchivedResumeOut.model_validate(record)

    async def delete(self, resume_id: int) -> None:
        async with self._sessions() as session:
            row = await session.get(ArchivedResume, resume_id)
            if row is None:
                raise ArchiveNotFoundError(f"No archived resume with id {resume_id}.")
            record = ArchivedResumeOut.model_validate(row)
            await session.delete(row)
            await session.commit()
        # Remove the file after the row is gone: a failure here leaves an
        # orphaned file (harmless) rather than a record pointing at nothing.
        try:
            await self._unlink(self.path_for(record))
        except ArchiveNotFoundError:
            pass
        logger.info("archive: deleted resume id=%d", resume_id)

    # ------------------------------------------------------------ helpers

    async def _store(self, upload: UploadFile, final_path: Path) -> int:
        """Stream the upload to disk with size/signature checks; return bytes written."""
        part_path = final_path.with_name(final_path.name + ".part")
        size = 0
        try:
            async with await anyio.open_file(part_path, "wb") as out:
                while chunk := await upload.read(_CHUNK_BYTES):
                    if size == 0 and not chunk.startswith(PDF_SIGNATURE):
                        raise ArchiveValidationError(
                            "file", "The file is not a valid PDF (it doesn't start with %PDF-)."
                        )
                    size += len(chunk)
                    if size > self.max_upload_bytes:
                        raise ArchiveValidationError(
                            "file",
                            f"The file is larger than {self.max_upload_bytes // (1024 * 1024)} MB.",
                        )
                    await out.write(chunk)
            if size == 0:
                raise ArchiveValidationError("file", "The file is empty.")
            await anyio.Path(part_path).rename(final_path)
        except BaseException:
            await self._unlink(part_path)
            raise
        return size

    @staticmethod
    async def _unlink(path: Path) -> None:
        try:
            await anyio.Path(path).unlink(missing_ok=True)
        except OSError:
            logger.warning("archive: could not remove a file from disk")
