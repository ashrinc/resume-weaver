"""POST /api/tailor endpoint tests. Gemini is always faked."""

import logging

import pytest
from fastapi.testclient import TestClient

from app.latex.parser import parse_resume
from app.llm.errors import (
    GeminiConfigError,
    GeminiRateLimitError,
    GeminiResponseError,
    GeminiTimeoutError,
)
from app.main import create_app
from app.tailor.pipeline import TailorPipeline
from tests.tailor.conftest import BACKEND_JD, FRONTEND_JD, JAKES, FakeGemini


def post(client: TestClient, latex: str = JAKES, jd: str = BACKEND_JD):
    return client.post("/api/tailor", json={"latex_source": latex, "job_description": jd})


def app_with(fake: FakeGemini) -> TestClient:
    pipeline = TailorPipeline(client_factory=lambda: fake, model_name="test-model")
    return TestClient(create_app(tailor_pipeline=pipeline))


# --- happy path ---------------------------------------------------------------------------


def test_tailor_happy_path(client: TestClient, fake: FakeGemini) -> None:
    resp = post(client)
    assert resp.status_code == 200
    body = resp.json()
    assert fake.calls == 1

    assert body["latex"].startswith(parse_resume(JAKES).preamble)
    assert body["llm_score"]["score"] == 87
    assert body["llm_score"]["reasoning"]
    score = body["programmatic_score"]
    assert 0 <= score["total"] <= 100
    assert len(score["criteria"]) == 5
    assert {m["skill"] for m in score["matched_mandatory"]} >= {"Go", "Python", "PostgreSQL"}
    assert isinstance(score["matched_preferred"], list)
    assert all(m["priority"] in ("high", "medium") for m in score["missing_keywords"])
    assert body["validation"] == {"errors": [], "warnings": []}
    assert body["improvement_summary"]
    assert body["changes_summary"]
    assert body["original_programmatic_score"] <= score["total"]
    assert body["model_notes"] == ["Put the most payments-relevant bullet first."]
    assert body["content_check"]["restored"] == 0
    assert body["cached"] is False


def test_preamble_is_verbatim_in_response(client: TestClient) -> None:
    latex = post(client).json()["latex"]
    preamble = JAKES[: JAKES.index("\\begin{document}") + len("\\begin{document}")]
    assert latex.startswith(preamble)


# --- cache ---------------------------------------------------------------------------------


def test_cache_hit_second_identical_request(client: TestClient, fake: FakeGemini) -> None:
    first = post(client).json()
    second = post(client).json()
    assert fake.calls == 1
    assert (first["cached"], second["cached"]) == (False, True)
    assert first["latex"] == second["latex"]


def test_cache_miss_different_input(client: TestClient, fake: FakeGemini) -> None:
    post(client)
    post(client, jd=FRONTEND_JD)
    assert fake.calls == 2


# --- 422 / 400 -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({"latex_source": "", "job_description": BACKEND_JD}, "latex_source"),
        ({"latex_source": JAKES, "job_description": ""}, "job_description"),
        ({"latex_source": "  \n\t ", "job_description": BACKEND_JD}, "latex_source"),
        ({"latex_source": JAKES, "job_description": "   "}, "job_description"),
        ({"job_description": BACKEND_JD}, "latex_source"),
        ({"latex_source": JAKES}, "job_description"),
    ],
    ids=["empty-latex", "empty-jd", "blank-latex", "blank-jd", "missing-latex", "missing-jd"],
)
def test_empty_or_missing_input_returns_422(
    client: TestClient, fake: FakeGemini, payload: dict, field: str
) -> None:
    resp = client.post("/api/tailor", json=payload)
    assert resp.status_code == 422
    error = resp.json()["error"]
    assert error["code"] == "invalid_request"
    assert error["retryable"] is False
    assert field in [d["field"] for d in error["details"]]
    assert fake.calls == 0


def test_422_does_not_echo_submitted_text(client: TestClient) -> None:
    resp = client.post("/api/tailor", json={"latex_source": JAKES, "job_description": ""})
    assert "Jordan Rivera" not in resp.text


def test_oversized_input_returns_422(client: TestClient) -> None:
    resp = post(client, jd="x" * 50_001)
    assert resp.status_code == 422
    assert resp.json()["error"]["details"][0]["field"] == "job_description"


def test_malformed_json_body_returns_422(client: TestClient) -> None:
    resp = client.post(
        "/api/tailor", content="{not json", headers={"Content-Type": "application/json"}
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_request"


def test_untailorable_latex_returns_400(client: TestClient, fake: FakeGemini) -> None:
    resp = post(client, latex="Not LaTeX at all, just my resume in plain text.")
    assert resp.status_code == 400
    error = resp.json()["error"]
    assert error["code"] == "invalid_input"
    assert "\\begin{document}" in error["message"]
    assert fake.calls == 0


# --- Gemini errors -> 502 --------------------------------------------------------------------


def test_gemini_timeout_returns_502_with_clear_message() -> None:
    client = app_with(
        FakeGemini(error=GeminiTimeoutError("Gemini did not respond within 60 seconds."))
    )
    resp = post(client)
    assert resp.status_code == 502
    error = resp.json()["error"]
    assert error["code"] == "gemini_timeout"
    assert error["message"] == "Gemini did not respond within 60 seconds. Please try again."
    assert error["retryable"] is True


def test_gemini_rate_limit_returns_502() -> None:
    client = app_with(FakeGemini(error=GeminiRateLimitError("429 persisted", retry_after=17.0)))
    resp = post(client)
    assert resp.status_code == 502
    error = resp.json()["error"]
    assert error["code"] == "gemini_rate_limited"
    assert "rate limit" in error["message"]
    assert error["retryable"] is True
    assert error["retry_after_seconds"] == 17.0


@pytest.mark.parametrize("retryable", [True, False])
def test_gemini_bad_response_returns_502(retryable: bool) -> None:
    exc = GeminiResponseError("Gemini returned malformed output twice.", retryable=retryable)
    resp = post(app_with(FakeGemini(error=exc)))
    assert resp.status_code == 502
    error = resp.json()["error"]
    assert (error["code"], error["retryable"]) == ("gemini_bad_response", retryable)
    assert "malformed output" in error["message"]


def test_missing_api_key_returns_500_with_setup_hint() -> None:
    def factory():
        raise GeminiConfigError("GEMINI_API_KEY is not set. Add it to backend/.env.")

    client = TestClient(
        create_app(tailor_pipeline=TailorPipeline(client_factory=factory, model_name="m"))
    )
    resp = post(client)
    assert resp.status_code == 500
    error = resp.json()["error"]
    assert error["code"] == "gemini_not_configured"
    assert "GEMINI_API_KEY" in error["message"]


def test_gemini_failure_keeps_working_on_retry() -> None:
    fake = FakeGemini(error=GeminiTimeoutError("Gemini did not respond within 60 seconds."))
    client = app_with(fake)
    assert post(client).status_code == 502
    fake.error = None
    assert post(client).status_code == 200


# --- unexpected errors and logging ----------------------------------------------------------


def test_unexpected_error_returns_generic_500_without_leaking(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "SECRET-JD-TEXT-98765"

    def explode(resume, jd):
        raise ValueError(f"boom while processing {secret}")

    client = app_with(FakeGemini(behaviour=explode))
    with caplog.at_level(logging.DEBUG):
        resp = post(client, jd=BACKEND_JD + f"\n{secret}")
    assert resp.status_code == 500
    error = resp.json()["error"]
    assert error["code"] == "internal_error"
    assert secret not in resp.text
    assert "ValueError" in caplog.text  # type is logged...
    assert secret not in caplog.text  # ...but not the message


def test_logs_never_contain_resume_or_jd_text(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    secret_jd = BACKEND_JD + "\nUNIQUE-JD-MARKER-4242"
    with caplog.at_level(logging.DEBUG):
        post(client, jd=secret_jd)
        post(client, jd=secret_jd)  # cache hit path
        client.post("/api/tailor", json={"latex_source": "", "job_description": secret_jd})
    assert "tailor: start" in caplog.text and "tailor: cache hit" in caplog.text
    for marker in ("UNIQUE-JD-MARKER-4242", "Jordan Rivera", "Brightline Logistics"):
        assert marker not in caplog.text


def test_health_still_works(client: TestClient) -> None:
    assert client.get("/api/health").json() == {"status": "ok"}


# --- overall deadline (TAILOR_DEADLINE_SECONDS) ---------------------------------------------


class SlowPipeline:
    """Fake pipeline that never finishes within the deadline."""

    def __init__(self) -> None:
        self.cancelled = False

    async def run(self, latex: str, jd: str):  # type: ignore[no-untyped-def]
        import asyncio

        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            self.cancelled = True
            raise


def test_deadline_returns_structured_retryable_error() -> None:
    slow = SlowPipeline()
    client = TestClient(create_app(tailor_pipeline=slow, tailor_deadline_seconds=0.05))  # type: ignore[arg-type]
    resp = post(client)
    assert resp.status_code == 504
    error = resp.json()["error"]
    assert error["code"] == "gemini_deadline"
    assert error["retryable"] is True
    assert error["message"] == "Tailoring took longer than 0.05 seconds and was stopped. Try again."
    assert slow.cancelled  # the run is cancelled, not left running


def test_deadline_message_for_the_default_240_seconds() -> None:
    from app.api.errors import TailorDeadlineError
    from app.config import Settings

    assert Settings(_env_file=None).tailor_deadline_seconds == 240
    assert str(TailorDeadlineError(240.0)) == (
        "Tailoring took longer than 240 seconds and was stopped. Try again."
    )


def test_fast_runs_are_unaffected_by_the_deadline(fake: FakeGemini) -> None:
    pipeline = TailorPipeline(client_factory=lambda: fake, model_name="test-model")
    client = TestClient(create_app(tailor_pipeline=pipeline, tailor_deadline_seconds=30))
    assert post(client).status_code == 200


def test_gemini_timeout_is_not_reported_as_deadline() -> None:
    fake = FakeGemini(error=GeminiTimeoutError("Gemini did not respond within 60 seconds."))
    resp = post(app_with(fake))
    assert resp.status_code == 502 and resp.json()["error"]["code"] == "gemini_timeout"
