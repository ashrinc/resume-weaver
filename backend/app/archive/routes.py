"""Archive endpoints (PRD FR-09). Independent of the tailoring feature.

POST   /api/archive                 multipart: file, company_name, job_role? -> 201
GET    /api/archive                 metadata list, newest first
GET    /api/archive/{id}/download   the PDF, with its original file name
DELETE /api/archive/{id}            remove the row and the file -> 204
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile
from fastapi.responses import FileResponse

from app.api.schemas import ErrorResponse
from app.archive.models import ArchivedResumeOut
from app.archive.service import ArchiveService

router = APIRouter(prefix="/api/archive", tags=["archive"])

_NOT_FOUND = {404: {"model": ErrorResponse, "description": "No such resume, or file missing"}}


async def get_archive(request: Request) -> ArchiveService:
    service: ArchiveService = request.app.state.archive_service
    await service.init()  # idempotent; creates tables and the upload directory
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


@router.get(
    "/{resume_id}/download",
    response_class=FileResponse,
    responses={200: {"content": {"application/pdf": {}}}, **_NOT_FOUND},
)
async def download_resume(resume_id: int, archive: Archive) -> FileResponse:
    path, record = await archive.file_for(resume_id)
    # FileResponse sets Content-Disposition: attachment, with an RFC 5987
    # filename*= fallback for non-ASCII names.
    return FileResponse(path, media_type="application/pdf", filename=record.original_filename)


@router.delete("/{resume_id}", status_code=204, responses=_NOT_FOUND)
async def delete_resume(resume_id: int, archive: Archive) -> Response:
    await archive.delete(resume_id)
    return Response(status_code=204)
