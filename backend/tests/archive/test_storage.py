"""Deployable persistence: DATABASE_URL normalization and the storage
backends. Supabase is tested against an in-memory httpx.MockTransport, so no
network calls are made."""

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.archive.service import ArchiveService, _engine_for
from app.archive.storage import (
    LocalStorage,
    StorageError,
    StorageNotFoundError,
    SupabaseStorage,
    build_storage,
)
from app.config import Settings, normalize_database_url
from app.main import create_app

PDF = b"%PDF-1.4\n%fake\n" + b"x" * 100
KEY = "0123456789abcdef0123456789abcdef.pdf"
SECRET = "service-role-secret-value"


# --- DATABASE_URL ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "postgres://u:p@db.example.com:5432/postgres",
            "postgresql+psycopg://u:p@db.example.com:5432/postgres",
        ),
        (
            "postgresql://u:p@host/db?sslmode=require",
            "postgresql+psycopg://u:p@host/db?sslmode=require",
        ),
        ("postgresql+psycopg2://u:p@host/db", "postgresql+psycopg://u:p@host/db"),
        ("postgresql+psycopg://u:p@host/db", "postgresql+psycopg://u:p@host/db"),
        ("  postgres://u:p@host/db  ", "postgresql+psycopg://u:p@host/db"),
        ("sqlite+aiosqlite:///data/app.db", "sqlite+aiosqlite:///data/app.db"),
    ],
)
def test_database_url_normalization(raw: str, expected: str) -> None:
    assert normalize_database_url(raw) == expected


def test_settings_read_and_normalize_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@host:5432/postgres")
    assert Settings(_env_file=None).database_url == "postgresql+psycopg://u:p@host:5432/postgres"


def test_settings_default_is_local_sqlite(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("ARCHIVE_DATABASE_URL", raising=False)
    settings = Settings(_env_file=None)
    assert settings.database_url.startswith("sqlite+aiosqlite:///")
    assert settings.storage_backend == "local"


def test_legacy_archive_database_url_still_works(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("ARCHIVE_DATABASE_URL", "sqlite+aiosqlite:///tmp/x.db")
    assert Settings(_env_file=None).database_url == "sqlite+aiosqlite:///tmp/x.db"


def test_postgres_engine_uses_psycopg_without_connecting() -> None:
    engine = _engine_for("postgresql+psycopg://u:p@db.invalid:5432/postgres")
    assert engine.dialect.name == "postgresql"
    assert engine.dialect.driver == "psycopg"
    asyncio.run(engine.dispose())


# --- storage factory --------------------------------------------------------------------------


def test_build_storage_local_by_default(tmp_path: Path) -> None:
    storage = build_storage(Settings(_env_file=None, archive_upload_dir=tmp_path))
    assert isinstance(storage, LocalStorage) and storage.root == tmp_path


def test_build_storage_supabase_requires_settings_and_never_echoes_them() -> None:
    settings = Settings(_env_file=None, storage_backend="supabase", supabase_service_key=SECRET)
    with pytest.raises(ValueError, match="SUPABASE_URL, SUPABASE_BUCKET") as excinfo:
        build_storage(settings)
    assert SECRET not in str(excinfo.value)


def test_build_storage_supabase() -> None:
    settings = Settings(
        _env_file=None,
        storage_backend="supabase",
        supabase_url="https://proj.supabase.co/",
        supabase_service_key=SECRET,
        supabase_bucket="resumes",
    )
    storage = build_storage(settings)
    assert isinstance(storage, SupabaseStorage)
    assert storage.base == "https://proj.supabase.co/storage/v1"


# --- Supabase storage against a mocked HTTP API ------------------------------------------------


class FakeSupabase:
    """Minimal in-memory Supabase Storage API (private bucket)."""

    def __init__(self, bucket: str = "resumes") -> None:
        self.bucket = bucket
        self.objects: dict[str, bytes] = {}
        self.requests: list[httpx.Request] = []
        self.fail_with: int | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.headers["authorization"] == f"Bearer {SECRET}"
        assert request.headers["apikey"] == SECRET
        if self.fail_with:
            return httpx.Response(self.fail_with, json={"message": "boom"})
        path = request.url.path
        prefix_auth = f"/storage/v1/object/authenticated/{self.bucket}/"
        prefix = f"/storage/v1/object/{self.bucket}/"
        not_found = httpx.Response(
            400, json={"statusCode": "404", "error": "not_found", "message": "Object not found"}
        )
        if request.method == "POST" and path.startswith(prefix):
            key = path[len(prefix) :]
            assert request.headers["content-type"] == "application/pdf"
            assert request.headers["x-upsert"] == "false"
            self.objects[key] = request.content
            return httpx.Response(200, json={"Key": f"{self.bucket}/{key}"})
        if request.method == "GET" and path.startswith(prefix_auth):
            key = path[len(prefix_auth) :]
            if key not in self.objects:
                return not_found
            return httpx.Response(200, content=self.objects[key])
        if request.method == "DELETE" and path.startswith(prefix):
            key = path[len(prefix) :]
            if self.objects.pop(key, None) is None:
                return not_found
            return httpx.Response(200, json={"message": "Successfully deleted"})
        return httpx.Response(405)


@pytest.fixture
def api() -> FakeSupabase:
    return FakeSupabase()


@pytest.fixture
def storage(api: FakeSupabase) -> SupabaseStorage:
    client = httpx.AsyncClient(transport=httpx.MockTransport(api.handler))
    return SupabaseStorage("https://proj.supabase.co", SECRET, "resumes", client=client)


async def chunks(*parts: bytes) -> AsyncIterator[bytes]:
    for part in parts:
        yield part


async def read_all(stream: AsyncIterator[bytes]) -> bytes:
    return b"".join([c async for c in stream])


def test_supabase_save_open_delete(storage: SupabaseStorage, api: FakeSupabase) -> None:
    async def scenario() -> None:
        assert await storage.save(KEY, chunks(PDF[:10], PDF[10:])) == len(PDF)
        assert api.objects == {KEY: PDF}
        assert await read_all(await storage.open(KEY)) == PDF
        await storage.delete(KEY)
        assert api.objects == {}
        await storage.delete(KEY)  # idempotent: missing object is fine

    asyncio.run(scenario())
    urls = [str(r.url) for r in api.requests]
    assert urls[0] == f"https://proj.supabase.co/storage/v1/object/resumes/{KEY}"
    assert urls[1] == f"https://proj.supabase.co/storage/v1/object/authenticated/resumes/{KEY}"


def test_supabase_missing_object_is_not_found(storage: SupabaseStorage) -> None:
    with pytest.raises(StorageNotFoundError):
        asyncio.run(storage.open(KEY))


def test_supabase_invalid_upload_sends_nothing(storage: SupabaseStorage, api: FakeSupabase) -> None:
    async def failing() -> AsyncIterator[bytes]:
        yield PDF
        raise ValueError("validation failed mid-stream")

    with pytest.raises(ValueError):
        asyncio.run(storage.save(KEY, failing()))
    assert api.requests == [] and api.objects == {}


@pytest.mark.parametrize("key", ["../etc/passwd", "a/b.pdf", "notes.txt", ""])
def test_supabase_rejects_unexpected_keys_without_a_request(
    storage: SupabaseStorage, api: FakeSupabase, key: str
) -> None:
    with pytest.raises(StorageNotFoundError):
        asyncio.run(storage.open(key))
    assert api.requests == []


def test_supabase_server_errors_are_storage_errors_without_secrets(
    storage: SupabaseStorage, api: FakeSupabase
) -> None:
    api.fail_with = 500
    with pytest.raises(StorageError) as excinfo:
        asyncio.run(storage.save(KEY, chunks(PDF)))
    assert "HTTP 500" in str(excinfo.value) and SECRET not in str(excinfo.value)


def test_archive_api_round_trip_with_supabase_storage(
    tmp_path: Path, storage: SupabaseStorage, api: FakeSupabase
) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"
    service = ArchiveService(storage, db_url)
    with TestClient(create_app(archive_service=service)) as client:
        resp = client.post(
            "/api/archive",
            files={"file": ("Jane Doe.pdf", PDF, "application/pdf")},
            data={"company_name": "Acme"},
        )
        assert resp.status_code == 201
        created = resp.json()
        key = created["file_path"]
        # The DB stores only the object key: no local path, no URL.
        assert len(key) == 36 and key.endswith(".pdf") and "/" not in key
        assert api.objects == {key: PDF}
        assert not any(tmp_path.glob("**/*.pdf"))

        download = client.get(f"/api/archive/{created['id']}/download")
        assert download.status_code == 200 and download.content == PDF
        assert download.headers["content-disposition"] == (
            "attachment; filename*=utf-8''Jane%20Doe.pdf"
        )

        api.objects.clear()  # object vanished from the bucket
        missing = client.get(f"/api/archive/{created['id']}/download")
        assert missing.status_code == 404

        assert client.delete(f"/api/archive/{created['id']}").status_code == 204
        assert client.get("/api/archive").json() == []

        bad = client.post(
            "/api/archive",
            files={"file": ("x.pdf", b"not a pdf", "application/pdf")},
            data={"company_name": "Acme"},
        )
        assert bad.status_code == 422
        assert not [r for r in api.requests if r.method == "POST" and r.content == b"not a pdf"]
    # Nothing reached the real network: every request went through the mock.
    assert all(r.url.host == "proj.supabase.co" for r in api.requests)


def test_local_storage_round_trip(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path / "resumes")

    async def scenario() -> None:
        await storage.init()
        assert await storage.save(KEY, chunks(PDF)) == len(PDF)
        assert (tmp_path / "resumes" / KEY).read_bytes() == PDF
        assert await read_all(await storage.open(KEY)) == PDF
        await storage.delete(KEY)
        await storage.delete(KEY)
        with pytest.raises(StorageNotFoundError):
            await storage.open(KEY)

    asyncio.run(scenario())
    assert json.dumps([p.name for p in (tmp_path / "resumes").iterdir()]) == "[]"
