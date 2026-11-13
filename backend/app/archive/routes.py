"""Archive endpoints (PRD FR-09). Independent of the tailoring feature.

POST   /api/archive                 multipart: file, company_name, job_role? -> 201
GET    /api/archive                 metadata list, newest first
GET    /api/archive/{id}/download   the PDF, streamed through the backend (no public URLs)
DELETE /api/archive/{id}            remove the row and the file -> 204
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile
from fastapi.responses import StreamingResponse

from app.api.schemas import ErrorResponse
from app.archive.models import ArchivedResumeOut
from app.archive.service import ArchiveService

router = APIRouter(prefix="/api/archive", tags=["archive"])

_NOT_FOUND = {404: {"model": ErrorResponse, "description": "No such resume, or file missing"}}


async def get_archive(request: Request) -> ArchiveService:
    service: ArchiveService = request.app.state.archive_service
    await service.init()  # idempotent; prepares storage and creates tables
    return service


Archive = Annotated[ArchiveService, Depends(get_archive)]


@router.post(
    "",
    status_code=201,
    response_model=ArchivedResumeOut,
    responses={422: {"model": ErrorResponse, "description": "Invalid file or fields"}},
)
async def upload_resume(
    archive: Archive,
    file: Annotated[UploadFile, File(description="PDF, max 10 MB")],
    # Required, but validated by the service so that missing, empty and blank
    # values all get the same clear message (FastAPI treats "" as missing).
    company_name: Annotated[str, Form(description="Required")] = "",
    job_role: Annotated[str | None, Form()] = None,
) -> ArchivedResumeOut:
    try:
        return await archive.create(company_name=company_name, job_role=job_role, upload=file)
    finally:
        await file.close()


@router.get("", response_model=list[ArchivedResumeOut])
async def list_resumes(archive: Archive) -> list[ArchivedResumeOut]:
    return await archive.list()


def _content_disposition(filename: str) -> str:
    """attachment; ASCII names quoted, others RFC 5987 (filename*=utf-8''...)."""
    quoted = quote(filename)
    if quoted != filename:
        return f"attachment; filename*=utf-8''{quoted}"
    return f'attachment; filename="{filename}"'


@router.get(
    "/{resume_id}/download",
    response_class=StreamingResponse,
    responses={200: {"content": {"application/pdf": {}}}, **_NOT_FOUND},
)
async def download_resume(resume_id: int, archive: Archive) -> StreamingResponse:
    # Existence is checked before streaming starts, so a missing object is a 404.
    stream, record = await archive.open_file(resume_id)
    return StreamingResponse(
        stream,
        media_type="application/pdf",
        headers={
            "Content-Disposition": _content_disposition(record.original_filename),
            "Content-Length": str(record.file_size_bytes),
        },
    )


@router.delete("/{resume_id}", status_code=204, responses=_NOT_FOUND)
async def delete_resume(resume_id: int, archive: Archive) -> Response:
    await archive.delete(resume_id)
    return Response(status_code=204)
