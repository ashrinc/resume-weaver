"""Gemini client tests. The SDK is always mocked: no network calls."""

import asyncio
import json
import logging
import random
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from google.genai import errors as genai_errors
from google.genai import types

from app.config import Settings
from app.jd.analyzer import analyze_jd
from app.latex.parser import parse_resume
from app.llm.client import GeminiClient
from app.llm.errors import (
    GeminiConfigError,
    GeminiRateLimitError,
    GeminiResponseError,
    GeminiTimeoutError,
)
from app.llm.models import TailoringResponse
from app.llm.prompts import SYSTEM_PROMPT

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

VALID = {
    "bullets": [
        {
            "id": "exp1.b2",
            "text": "Built APIs in \\textbf{Go}",
            "changed": True,
            "reason": "Go is mandatory",
            "position": 1,
        },
        {"id": "exp1.b1", "text": "Wrote migrations", "changed": False, "reason": "aligned"},
    ],
    "skills_sections": [{"section_id": "s4", "latex": "\\section{Skills}\nGo, SQL"}],
    "ats_analysis": {
        "score": 88,
        "reasoning": "Covers most mandatory skills.",
        "matched_keywords": ["Go", "PostgreSQL"],
        "missing_keywords": ["Helm"],
        "improvement_suggestions": ["Gain hands-on Helm experience."],
    },
    "notes": ["Led with Go experience."],
}
VALID_TEXT = json.dumps(VALID)


def response(
    text: str | None, finish_reason=types.FinishReason.STOP
) -> types.GenerateContentResponse:
    parts = [types.Part(text=text)] if text is not None else []
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(role="model", parts=parts), finish_reason=finish_reason
            )
        ]
    )


def api_error(code: int, retry_delay: str | None = None) -> genai_errors.APIError:
    details = [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry_delay}]
    body = {"error": {"code": code, "message": "error", "status": "STATUS",
                      "details": details if retry_delay else []}}  # fmt: skip
    cls = genai_errors.ClientError if code < 500 else genai_errors.ServerError
    return cls(code, body)


def make_client(side_effect, **kwargs):
    sdk = MagicMock()
    sdk.aio.models.generate_content = AsyncMock(side_effect=side_effect)
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    client = GeminiClient(
        sdk_client=sdk, model="test-model", sleep=fake_sleep, rng=random.Random(0), **kwargs
    )
    return client, sdk.aio.models.generate_content, sleeps


def generate(client: GeminiClient, user: str = "prompt") -> TailoringResponse:
    return asyncio.run(
        client.generate_structured(system=SYSTEM_PROMPT, user=user, schema=TailoringResponse)
    )


def sent_prompt(mock: AsyncMock, call: int) -> str:
    return mock.call_args_list[call].kwargs["contents"]


# --- success ----------------------------------------------------------------------


def test_successful_response_is_parsed() -> None:
    client, mock, sleeps = make_client([response(VALID_TEXT)])
    result = generate(client, user="the user prompt")

    assert isinstance(result, TailoringResponse)
    assert [(b.id, b.position) for b in result.bullets] == [("exp1.b2", 1), ("exp1.b1", None)]
    assert result.bullets[0].text == "Built APIs in \\textbf{Go}"
    assert result.bullets[0].reason == "Go is mandatory"
    assert result.bullets[1].changed is False
    assert result.skills_sections[0].section_id == "s4"
    assert result.ats_analysis.score == 88
    assert result.ats_analysis.missing_keywords == ["Helm"]
    assert result.notes == ["Led with Go experience."]

    mock.assert_awaited_once()
    kwargs = mock.call_args.kwargs
    assert kwargs["model"] == "test-model"
    assert kwargs["contents"] == "the user prompt"
    config = kwargs["config"]
    assert config.response_mime_type == "application/json"
    assert config.response_schema is TailoringResponse
    assert config.system_instruction == SYSTEM_PROMPT
    assert sleeps == []


def test_markdown_fenced_json_is_accepted_without_repair() -> None:
    client, mock, _ = make_client([response(f"```json\n{VALID_TEXT}\n```")])
    assert generate(client).ats_analysis.score == 88
    mock.assert_awaited_once()


def test_tailor_resume_sends_built_prompt() -> None:
    client, mock, _ = make_client([response(VALID_TEXT)])
    resume = parse_resume((FIXTURES / "resumes" / "jakes_resume.tex").read_text())
    jd = analyze_jd((FIXTURES / "jds" / "backend_engineer.txt").read_text())
    result = asyncio.run(client.tailor_resume(resume, jd))
    assert result.ats_analysis.score == 88
    prompt = sent_prompt(mock, 0)
    assert "Brightline Logistics" in prompt  # section content is sent
    assert "glyphtounicode" not in prompt  # preamble is not


# --- timeout ----------------------------------------------------------------------


def test_timeout_raises_gemini_timeout_error() -> None:
    async def hang(**_kwargs):
        await asyncio.sleep(5)

    client, mock, sleeps = make_client(hang, timeout=0.01)
    with pytest.raises(GeminiTimeoutError, match="within 0.01 seconds"):
        generate(client)
    assert mock.await_count == 1  # timeouts are not retried
    assert sleeps == []


def test_sdk_level_timeout_also_maps_to_gemini_timeout_error() -> None:
    client, _, _ = make_client([httpx.ReadTimeout("read timed out")])
    with pytest.raises(GeminiTimeoutError):
        generate(client)


def test_timeout_error_is_retryable() -> None:
    assert GeminiTimeoutError("x").retryable is True


# --- 429 / 5xx retries ----------------------------------------------------------------


def test_429_is_retried_with_backoff_and_then_succeeds() -> None:
    client, mock, sleeps = make_client([api_error(429), api_error(429), response(VALID_TEXT)])
    assert generate(client).ats_analysis.score == 88
    assert mock.await_count == 3
    assert len(sleeps) == 2
    assert 1.0 <= sleeps[0] <= 1.5  # base 1s * 2^0 + jitter (<= base/2)
    assert 2.0 <= sleeps[1] <= 2.5  # base 1s * 2^1 + jitter
    assert sleeps[1] > sleeps[0]


def test_429_honours_server_retry_delay() -> None:
    client, _, sleeps = make_client([api_error(429, retry_delay="17s"), response(VALID_TEXT)])
    generate(client)
    assert sleeps == [17.0]


def test_backoff_is_capped() -> None:
    client, _, sleeps = make_client(
        [api_error(429, retry_delay="120s"), response(VALID_TEXT)], max_delay=30.0
    )
    generate(client)
    assert sleeps == [30.0]


def test_429_exhausting_retries_raises_rate_limit_error() -> None:
    client, mock, sleeps = make_client([api_error(429, retry_delay="5s")] * 4)
    with pytest.raises(GeminiRateLimitError) as excinfo:
        generate(client)
    assert mock.await_count == 4  # 1 attempt + 3 retries
    assert len(sleeps) == 3
    assert excinfo.value.status_code == 429
    assert excinfo.value.retry_after == 5.0
    assert excinfo.value.retryable is True


def test_5xx_is_retried_then_succeeds() -> None:
    client, mock, sleeps = make_client([api_error(503), api_error(500), response(VALID_TEXT)])
    assert generate(client).ats_analysis.score == 88
    assert mock.await_count == 3
    assert len(sleeps) == 2


def test_5xx_exhausting_retries_raises_retryable_response_error() -> None:
    client, mock, _ = make_client([api_error(500)] * 4)
    with pytest.raises(GeminiResponseError) as excinfo:
        generate(client)
    assert mock.await_count == 4
    assert (excinfo.value.status_code, excinfo.value.retryable) == (500, True)


@pytest.mark.parametrize("code", [400, 401, 403, 404])
def test_other_4xx_is_not_retried(code: int) -> None:
    client, mock, sleeps = make_client([api_error(code)])
    with pytest.raises(GeminiResponseError) as excinfo:
        generate(client)
    assert mock.await_count == 1
    assert sleeps == []
    assert (excinfo.value.status_code, excinfo.value.retryable) == (code, False)


def test_network_error_maps_to_retryable_response_error() -> None:
    client, _, _ = make_client([httpx.ConnectError("connection refused")])
    with pytest.raises(GeminiResponseError, match="Could not reach Gemini") as excinfo:
        generate(client)
    assert excinfo.value.retryable is True


# --- malformed output and repair --------------------------------------------------------


def test_malformed_json_triggers_one_repair_retry() -> None:
    bad = '{"sections": [{"section_id": "s1", "latex": "\\section{X}"'  # truncated
    client, mock, sleeps = make_client([response(bad), response(VALID_TEXT)])
    result = generate(client)

    assert result.ats_analysis.score == 88
    assert mock.await_count == 2
    repair = sent_prompt(mock, 1)
    assert "could not be used because it was not valid JSON" in repair
    assert "invalid JSON" in repair  # the problem description
    assert bad in repair  # the previous output is echoed for fixing
    assert sleeps == []  # a repair is not a backoff retry


@pytest.mark.parametrize(
    "payload",
    [
        {k: v for k, v in VALID.items() if k != "ats_analysis"},  # missing key
        {**VALID, "ats_analysis": {**VALID["ats_analysis"], "score": 150}},  # out of range
        {**VALID, "bullets": "not a list"},  # wrong type
    ],
    ids=["missing-key", "score-out-of-range", "wrong-type"],
)
def test_schema_mismatch_triggers_repair(payload: dict) -> None:
    client, mock, _ = make_client([response(json.dumps(payload)), response(VALID_TEXT)])
    assert generate(client).ats_analysis.score == 88
    assert mock.await_count == 2
    assert "does not match the schema" in sent_prompt(mock, 1)


def test_empty_response_triggers_repair() -> None:
    client, mock, _ = make_client([response(None), response(VALID_TEXT)])
    assert generate(client).ats_analysis.score == 88
    assert mock.await_count == 2


def test_complete_garbage_raises_response_error_after_one_repair() -> None:
    garbage = "Sure! Here is your resume :) <<<>>> lorem ipsum"
    client, mock, _ = make_client([response(garbage), response("still not json!!!")])
    with pytest.raises(GeminiResponseError, match="malformed output twice") as excinfo:
        generate(client)
    assert mock.await_count == 2  # exactly one repair, no more
    assert excinfo.value.retryable is False


def test_blocked_response_raises_without_repair() -> None:
    client, mock, _ = make_client([response(None, finish_reason=types.FinishReason.SAFETY)])
    with pytest.raises(GeminiResponseError, match="blocked"):
        generate(client)
    mock.assert_awaited_once()


def test_repair_request_is_also_retried_on_429() -> None:
    client, mock, sleeps = make_client([response("{oops"), api_error(429), response(VALID_TEXT)])
    assert generate(client).ats_analysis.score == 88
    assert mock.await_count == 3
    assert len(sleeps) == 1


def test_errors_and_logs_never_contain_resume_text(caplog: pytest.LogCaptureFixture) -> None:
    secret = "SECRET-RESUME-CONTENT-12345"
    bad = json.dumps({**VALID, "ats_analysis": {**VALID["ats_analysis"], "score": secret}})
    client, mock, _ = make_client([response(bad), response(f"{secret} garbage")])
    with caplog.at_level(logging.DEBUG, logger="app.llm.client"):
        with pytest.raises(GeminiResponseError) as excinfo:
            generate(client, user=f"prompt with {secret}")
    assert secret not in str(excinfo.value)
    assert secret not in caplog.text
    # The repair prompt's problem line describes the field, not its value.
    problem_line = sent_prompt(mock, 1).split("Problem:", 1)[1].split("\n", 2)[0]
    assert "ats_analysis.score" in problem_line and secret not in problem_line


# --- configuration --------------------------------------------------------------------


def test_missing_api_key_raises_config_error() -> None:
    with pytest.raises(GeminiConfigError, match="GEMINI_API_KEY"):
        GeminiClient(api_key="")


def test_default_model_is_gemini_2_0_flash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    assert Settings(_env_file=None).gemini_model == "gemini-3.5-flash"


def test_model_and_key_come_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings

    monkeypatch.setenv("GEMINI_MODEL", "gemini-test-model")
    monkeypatch.setenv("GEMINI_API_KEY", "env-key")
    get_settings.cache_clear()
    try:
        with patch("app.llm.client.genai.Client") as sdk_cls:
            client = GeminiClient()
        assert client.model == "gemini-test-model"
        kwargs = sdk_cls.call_args.kwargs
        assert kwargs["api_key"] == "env-key"
        http_options = kwargs["http_options"]
        assert http_options.timeout == 60_000  # milliseconds
        assert http_options.retry_options.attempts == 1  # SDK retries disabled
    finally:
        get_settings.cache_clear()


def test_verify_rewrites_sends_items_and_counts_requests() -> None:
    from app.llm.prompts import VerifyItem

    payload = {
        "results": [
            {"id": "exp1.b1", "verdict": "supported", "evidence": "Wrote migrations",
             "problem": "", "wording_only": False}
        ]
    }  # fmt: skip
    client, mock, _ = make_client([response(json.dumps(payload))])
    items = [VerifyItem(id="exp1.b1", original="Wrote migrations", rewrite="Authored migrations")]
    result = asyncio.run(client.verify_rewrites("Experience Wrote migrations", items))

    assert result.results[0].verdict == "supported"
    sent = sent_prompt(mock, 0)
    assert "Experience Wrote migrations" in sent and '"rewrite": "Authored migrations"' in sent
    assert "strict fact checker" in mock.call_args.kwargs["config"].system_instruction
    assert client.request_count == 1
