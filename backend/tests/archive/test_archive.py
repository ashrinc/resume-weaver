"""Archive API tests. Each test gets its own temporary upload dir and SQLite DB."""

import asyncio
import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.archive.service import MAX_UPLOAD_BYTES, ArchiveService
from app.main import create_app

PDF = b"%PDF-1.7\n1 0 obj << /Type /Catalog >> endobj\ntrailer << /Root 1 0 R >>\n%%EOF\n"


class Clock:
    """Deterministic, controllable upload timestamps."""

    def __init__(self) -> None:
        self.now = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def paths(tmp_path: Path) -> tuple[Path, str]:
    return tmp_path / "uploads" / "resumes", f"sqlite+aiosqlite:///{tmp_path / 'data' / 'app.db'}"


@pytest.fixture
def service(paths, clock) -> ArchiveService:
    upload_dir, db_url = paths
    return ArchiveService(upload_dir, db_url, clock=clock)


@pytest.fixture
def client(service: ArchiveService) -> Iterator[TestClient]:
    with TestClient(create_app(archive_service=service)) as c:
        yield c


def upload(
    client: TestClient,
    *,
    content: bytes = PDF,
    filename: str = "jane_doe_acme.pdf",
    mime: str = "application/pdf",
    company: str | None = "Acme Corp",
    role: str | None = "Backend Engineer",
):
    data = {}
    if company is not None:
        data["company_name"] = company
    if role is not None:
        data["job_role"] = role
    return client.post("/api/archive", data=data, files={"file": (filename, content, mime)})


def stored_files(service: ArchiveService) -> list[str]:
    return (
        sorted(p.name for p in service.upload_dir.iterdir()) if service.upload_dir.exists() else []
    )


def assert_422(resp, field: str, text: str) -> None:
    assert resp.status_code == 422, resp.text
    error = resp.json()["error"]
    assert error["code"] in ("invalid_upload", "invalid_request")
    assert field in [d["field"] for d in error["details"]]
    assert text.lower() in resp.text.lower()


# --- upload ---------------------------------------------------------------------------


def test_upload_happy_path(client: TestClient, service: ArchiveService) -> None:
    resp = upload(client)
    assert resp.status_code == 201
    body = resp.json()
    assert set(body) == {
        "id", "company_name", "job_role", "file_path", "original_filename",
        "file_size_bytes", "upload_timestamp",
    }  # fmt: skip
    assert body["id"] == 1
    assert body["company_name"] == "Acme Corp"
    assert body["job_role"] == "Backend Engineer"
    assert body["original_filename"] == "jane_doe_acme.pdf"
    assert body["file_size_bytes"] == len(PDF)
    assert body["upload_timestamp"] == "2026-11-01T12:00:00Z"

    # Stored under a unique UUID name, not the client's file name.
    assert body["file_path"].endswith(".pdf") and body["file_path"] != "jane_doe_acme.pdf"
    assert stored_files(service) == [body["file_path"]]
    assert (service.upload_dir / body["file_path"]).read_bytes() == PDF


def test_upload_trims_fields_and_role_is_optional(client: TestClient) -> None:
    body = upload(client, company="  Globex  ", role=None).json()
    assert (body["company_name"], body["job_role"]) == ("Globex", None)
    assert upload(client, role="   ").json()["job_role"] is None


def test_same_filename_twice_gets_unique_storage(
    client: TestClient, service: ArchiveService
) -> None:
    a, b = upload(client).json(), upload(client).json()
    assert a["file_path"] != b["file_path"]
    assert len(stored_files(service)) == 2


def test_uppercase_extension_and_x_pdf_mime_are_accepted(client: TestClient) -> None:
    assert upload(client, filename="RESUME.PDF", mime="application/x-pdf").status_code == 201


def test_client_path_in_filename_is_never_used_as_a_path(
    client: TestClient, service: ArchiveService
) -> None:
    body = upload(client, filename="../../etc/evil.pdf").json()
    assert body["original_filename"] == "evil.pdf"
    assert stored_files(service) == [body["file_path"]]
    assert not (service.upload_dir.parent.parent / "etc").exists()


# --- upload validation ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("filename", "mime", "content", "message"),
    [
        ("resume.docx", "application/pdf", PDF, "Only .pdf files"),  # wrong extension
        ("resume.pdf", "text/plain", PDF, "must be application/pdf"),  # wrong MIME
        ("resume.pdf", "application/octet-stream", PDF, "must be application/pdf"),
        ("resume.txt", "text/plain", b"hello", "Only .pdf files"),  # neither
        ("resume.pdf", "application/pdf", b"PK\x03\x04 a zip", "not a valid PDF"),  # spoofed
        ("resume.pdf", "application/pdf", b"", "empty"),
    ],
    ids=["docx-ext", "text-mime", "octet-mime", "txt", "spoofed-content", "empty-file"],
)
def test_upload_rejects_non_pdf(
    client: TestClient, service: ArchiveService, filename, mime, content, message
) -> None:
    resp = upload(client, filename=filename, mime=mime, content=content)
    assert_422(resp, "file", message)
    assert stored_files(service) == []  # nothing left behind
    assert client.get("/api/archive").json() == []


def test_upload_rejects_file_over_10_mb(client: TestClient, service: ArchiveService) -> None:
    too_big = PDF + b"0" * (MAX_UPLOAD_BYTES - len(PDF) + 1)
    assert len(too_big) == MAX_UPLOAD_BYTES + 1
    resp = upload(client, content=too_big)
    assert_422(resp, "file", "larger than 10 MB")
    assert stored_files(service) == []  # partial .part file removed
    assert client.get("/api/archive").json() == []


def test_upload_accepts_exactly_10_mb(client: TestClient) -> None:
    exact = PDF + b"0" * (MAX_UPLOAD_BYTES - len(PDF))
    resp = upload(client, content=exact)
    assert resp.status_code == 201
    assert resp.json()["file_size_bytes"] == MAX_UPLOAD_BYTES


@pytest.mark.parametrize("company", ["", "   ", "\t\n"])
def test_upload_rejects_empty_company_name(
    client: TestClient, service: ArchiveService, company: str
) -> None:
    assert_422(upload(client, company=company), "company_name", "Company name is required")
    assert stored_files(service) == []


def test_upload_rejects_missing_company_or_file(client: TestClient) -> None:
    assert_422(upload(client, company=None), "company_name", "Company name is required")
    resp = client.post("/api/archive", data={"company_name": "Acme"})
    assert_422(resp, "file", "")


def test_upload_rejects_overlong_company(client: TestClient) -> None:
    assert_422(upload(client, company="x" * 201), "company_name", "at most 200")


# --- list ------------------------------------------------------------------------------


def test_list_is_empty_initially(client: TestClient) -> None:
    assert client.get("/api/archive").json() == []


def test_list_returns_newest_first(client: TestClient, clock: Clock) -> None:
    base = clock.now
    # Upload order differs from timestamp order, so this checks the sort key, not ids.
    for company, offset in [("Oldest", 0), ("Newest", 2), ("Middle", 1)]:
        clock.now = base + timedelta(hours=offset)
        assert upload(client, company=company).status_code == 201
    items = client.get("/api/archive").json()
    assert [i["company_name"] for i in items] == ["Newest", "Middle", "Oldest"]
    assert [i["upload_timestamp"] for i in items] == [
        "2026-11-01T14:00:00Z", "2026-11-01T13:00:00Z", "2026-11-01T12:00:00Z",
    ]  # fmt: skip


def test_list_ties_on_timestamp_break_by_newest_id(client: TestClient) -> None:
    for company in ("First", "Second"):
        upload(client, company=company)
    assert [i["company_name"] for i in client.get("/api/archive").json()] == ["Second", "First"]


def test_list_returns_metadata_only(client: TestClient) -> None:
    upload(client)
    resp = client.get("/api/archive")
    assert resp.headers["content-type"].startswith("application/json")
    assert b"%PDF" not in resp.content


# --- download --------------------------------------------------------------------------


def test_download_returns_file_with_headers(client: TestClient) -> None:
    created = upload(client, filename="Jane Doe - Acme.pdf").json()
    resp = client.get(f"/api/archive/{created['id']}/download")
    assert resp.status_code == 200
    assert resp.content == PDF
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.headers["content-length"] == str(len(PDF))
    disposition = resp.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert (
        "Jane%20Doe%20-%20Acme.pdf" in disposition
        or 'filename="Jane Doe - Acme.pdf"' in disposition
    )


def test_download_non_ascii_filename(client: TestClient) -> None:
    created = upload(client, filename="Résumé_Zoë.pdf").json()
    disposition = client.get(f"/api/archive/{created['id']}/download").headers[
        "content-disposition"
    ]
    assert "filename*=utf-8''R%C3%A9sum%C3%A9_Zo%C3%AB.pdf" in disposition


def test_download_404_for_nonexistent_id(client: TestClient) -> None:
    resp = client.get("/api/archive/999/download")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


def test_download_404_when_file_missing_from_disk(
    client: TestClient, service: ArchiveService
) -> None:
    created = upload(client).json()
    (service.upload_dir / created["file_path"]).unlink()
    resp = client.get(f"/api/archive/{created['id']}/download")
    assert resp.status_code == 404
    assert "missing" in resp.json()["error"]["message"]


def test_non_integer_id_is_422(client: TestClient) -> None:
    assert client.get("/api/archive/abc/download").status_code == 422


# --- delete ----------------------------------------------------------------------------


def test_delete_removes_db_record_and_file(client: TestClient, service: ArchiveService) -> None:
    keep = upload(client, company="Keep").json()
    gone = upload(client, company="Gone").json()
    gone_file = service.upload_dir / gone["file_path"]
    assert gone_file.exists()

    resp = client.delete(f"/api/archive/{gone['id']}")
    assert resp.status_code == 204
    assert resp.content == b""

    assert not gone_file.exists()
    assert [i["id"] for i in client.get("/api/archive").json()] == [keep["id"]]
    assert stored_files(service) == [keep["file_path"]]
    assert client.get(f"/api/archive/{gone['id']}/download").status_code == 404
    assert client.delete(f"/api/archive/{gone['id']}").status_code == 404  # already gone


def test_delete_404_for_nonexistent_id(client: TestClient) -> None:
    resp = client.delete("/api/archive/999")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


def test_delete_succeeds_when_file_already_missing(
    client: TestClient, service: ArchiveService
) -> None:
    created = upload(client).json()
    (service.upload_dir / created["file_path"]).unlink()
    assert client.delete(f"/api/archive/{created['id']}").status_code == 204
    assert client.get("/api/archive").json() == []


# --- persistence & independence ------------------------------------------------------------


def test_data_persists_across_restarts(paths, clock) -> None:
    upload_dir, db_url = paths
    with TestClient(
        create_app(archive_service=ArchiveService(upload_dir, db_url, clock=clock))
    ) as c:
        created = upload(c).json()
    # A brand-new service and app over the same directory and database file.
    with TestClient(create_app(archive_service=ArchiveService(upload_dir, db_url))) as c:
        assert [i["id"] for i in c.get("/api/archive").json()] == [created["id"]]
        assert c.get(f"/api/archive/{created['id']}/download").content == PDF


def test_archive_works_without_startup_hook(paths) -> None:
    # Without `with`, TestClient skips the lifespan; the service initialises lazily.
    upload_dir, db_url = paths
    c = TestClient(create_app(archive_service=ArchiveService(upload_dir, db_url)))
    assert upload(c).status_code == 201
    assert len(c.get("/api/archive").json()) == 1


def test_archive_package_does_not_import_tailoring() -> None:
    root = Path(__file__).resolve().parents[2] / "app" / "archive"
    imports = re.compile(r"^\s*(?:from|import)\s+(app\.[\w.]+)", re.MULTILINE)
    forbidden = ("app.tailor", "app.llm", "app.jd", "app.latex", "app.scoring")
    for source in root.glob("*.py"):
        for module in imports.findall(source.read_text()):
            assert not module.startswith(forbidden), f"{source.name} imports {module}"


def test_service_list_directly(service: ArchiveService) -> None:
    assert asyncio.run(_init_and_list(service)) == []


async def _init_and_list(service: ArchiveService):
    await service.init()
    try:
        return await service.list()
    finally:
        await service.close()
