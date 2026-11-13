"""Single-user access control, rate limits and deployment settings."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.auth import issue_token, verify_token
from app.api.ratelimit import RateLimitedError, RateLimiter
from app.archive.service import ArchiveService
from app.config import Settings
from app.main import create_app
from app.tailor.pipeline import TailorPipeline
from tests.tailor.conftest import BACKEND_JD, JAKES, FakeGemini

PASSWORD = "correct horse battery staple"
ORIGIN = "https://resume-tailr.vercel.app"
PDF = b"%PDF-1.4\n%test\n"


def make_client(tmp_path: Path, **overrides) -> TestClient:  # type: ignore[no-untyped-def]
    settings = Settings(
        _env_file=None, **{"access_password": PASSWORD, "cors_origins": [ORIGIN], **overrides}
    )
    fake = FakeGemini()
    return TestClient(
        create_app(
            settings=settings,
            tailor_pipeline=TailorPipeline(client_factory=lambda: fake, model_name="m"),
            archive_service=ArchiveService(
                tmp_path / "uploads", f"sqlite+aiosqlite:///{tmp_path / 'app.db'}"
            ),
        )
    )


def login(client: TestClient, password: str = PASSWORD):  # type: ignore[no-untyped-def]
    return client.post("/api/auth/login", json={"password": password})


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- tokens -----------------------------------------------------------------------------------


def test_token_round_trip_expiry_and_tampering() -> None:
    token = issue_token(PASSWORD, now=1_000, ttl=100)
    assert verify_token(token, PASSWORD, now=1_050)
    assert not verify_token(token, PASSWORD, now=1_101)  # expired
    assert not verify_token(token, "another password", now=1_050)  # password changed
    expiry, sig = token.split(".")
    assert not verify_token(f"{int(expiry) + 10_000}.{sig}", PASSWORD, now=1_050)  # forged expiry
    assert not verify_token("garbage", PASSWORD) and not verify_token("", PASSWORD)


# --- protected routes ---------------------------------------------------------------------------


def test_everything_but_health_and_auth_requires_sign_in(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/auth/session").json() == {
            "auth_required": True,
            "authenticated": False,
        }
        calls = [
            client.get("/api/archive"),
            client.get("/api/archive/1/download"),
            client.delete("/api/archive/1"),
            client.post(
                "/api/archive",
                files={"file": ("a.pdf", PDF, "application/pdf")},
                data={"company_name": "Acme"},
            ),  # fmt: skip
            client.post("/api/tailor", json={"latex_source": JAKES, "job_description": BACKEND_JD}),
        ]
        for resp in calls:
            assert resp.status_code == 401, resp.request.url
            assert resp.json()["error"]["code"] == "unauthorized"
            assert resp.headers["www-authenticate"] == "Bearer"
        bad = client.get("/api/archive", headers=bearer("123.deadbeef"))
        assert bad.status_code == 401


def test_login_then_use_the_api(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        assert login(client, "wrong password").status_code == 401
        resp = login(client)
        assert resp.status_code == 200
        token = resp.json()["token"]
        assert resp.json()["expires_at"] == int(token.split(".")[0])
        headers = bearer(token)

        assert client.get("/api/auth/session", headers=headers).json()["authenticated"] is True
        assert client.get("/api/archive", headers=headers).json() == []
        up = client.post(
            "/api/archive",
            headers=headers,
            files={"file": ("a.pdf", PDF, "application/pdf")},
            data={"company_name": "Acme"},
        )
        assert up.status_code == 201
        dl = client.get(f"/api/archive/{up.json()['id']}/download", headers=headers)
        assert dl.status_code == 200 and dl.content == PDF
        tailored = client.post(
            "/api/tailor",
            headers=headers,
            json={"latex_source": JAKES, "job_description": BACKEND_JD},
        )
        assert tailored.status_code == 200


def test_no_password_means_no_auth(tmp_path: Path) -> None:
    with make_client(tmp_path, access_password="") as client:
        assert client.get("/api/auth/session").json() == {
            "auth_required": False,
            "authenticated": True,
        }
        assert client.get("/api/archive").status_code == 200


# --- rate limits ------------------------------------------------------------------------------


def test_login_attempts_are_rate_limited(tmp_path: Path) -> None:
    with make_client(tmp_path, login_attempts_per_15_minutes=3) as client:
        for _ in range(3):
            assert login(client, "guess").status_code == 401
        blocked = login(client)  # even the right password, once over the limit
        assert blocked.status_code == 429
        body = blocked.json()["error"]
        assert body["code"] == "rate_limited" and body["retryable"] is True
        assert body["retry_after_seconds"] > 0 and int(blocked.headers["retry-after"]) >= 1


def test_tailoring_is_rate_limited(tmp_path: Path) -> None:
    with make_client(tmp_path, tailor_rate_limit_per_hour=1) as client:
        headers = bearer(login(client).json()["token"])
        body = {"latex_source": JAKES, "job_description": BACKEND_JD}
        assert client.post("/api/tailor", headers=headers, json=body).status_code == 200
        second = client.post("/api/tailor", headers=headers, json=body)
        assert second.status_code == 429 and second.json()["error"]["code"] == "rate_limited"


def test_sliding_window() -> None:
    now = [0.0]
    limiter = RateLimiter(2, 10, "things", clock=lambda: now[0])
    limiter.hit("a")
    limiter.hit("a")
    limiter.hit("b")  # separate key
    with pytest.raises(RateLimitedError):
        limiter.hit("a")
    now[0] = 10.5
    limiter.hit("a")  # window moved on
    RateLimiter(0, 10, "off").hit("a")  # 0 disables


# --- CORS and deployment settings -----------------------------------------------------------------


def test_cors_preflight_needs_no_auth_and_errors_carry_cors_headers(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        pre = client.options(
            "/api/archive",
            headers={
                "Origin": ORIGIN,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )
        assert pre.status_code == 200
        assert pre.headers["access-control-allow-origin"] == ORIGIN
        denied = client.get("/api/archive", headers={"Origin": ORIGIN})
        assert denied.status_code == 401
        assert denied.headers["access-control-allow-origin"] == ORIGIN
        other = client.get("/api/health", headers={"Origin": "https://evil.example"})
        assert "access-control-allow-origin" not in other.headers


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://a.vercel.app", ["https://a.vercel.app"]),
        ("https://a.vercel.app/, https://b.com", ["https://a.vercel.app", "https://b.com"]),
        ('["https://a.vercel.app"]', ["https://a.vercel.app"]),
    ],
)
def test_cors_origins_from_env(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: list[str]
) -> None:
    monkeypatch.setenv("CORS_ORIGINS", raw)
    assert Settings(_env_file=None).cors_origins == expected


def test_production_requires_a_password() -> None:
    with pytest.raises(ValidationError, match="ACCESS_PASSWORD"):
        Settings(_env_file=None, app_env="production")
    with pytest.raises(ValidationError, match="at least 12"):
        Settings(_env_file=None, app_env="production", access_password="short")
    assert Settings(_env_file=None, app_env="production", access_password=PASSWORD)


def test_production_hides_api_docs(tmp_path: Path) -> None:
    with make_client(tmp_path, app_env="production") as client:
        assert client.get("/docs").status_code == 404
        assert client.get("/openapi.json").status_code == 404
    with make_client(tmp_path / "dev") as client:
        assert client.get("/openapi.json").status_code == 200
