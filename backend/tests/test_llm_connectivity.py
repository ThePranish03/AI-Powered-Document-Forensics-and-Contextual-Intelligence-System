from dataclasses import replace

import pytest

from app.core.config import settings
from app.services.llm_client import LLMHTTPError, LLMRequestError, LLMResponseError
from scripts import check_llm_connectivity


MODEL = "openai/gpt-oss-120b"


def _set_config(monkeypatch, *, key="gsk_synthetic-groq-test-token", timeout=None):
    updates = {}
    if timeout is not None:
        updates["llm_connectivity_timeout_seconds"] = timeout
    monkeypatch.setattr(check_llm_connectivity, "settings", replace(
        settings,
        llm_api_key=key,
        llm_base_url="https://api.groq.com/openai/v1",
        llm_model=MODEL,
        **updates,
    ))


def test_connectivity_reports_not_configured_without_sending(monkeypatch, capsys):
    _set_config(monkeypatch, key="")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setattr(check_llm_connectivity, "chat_completion", lambda *_args, **_kwargs: pytest.fail("must not send"))

    assert check_llm_connectivity.main() == 2
    output = capsys.readouterr().out
    assert "NOT_CONFIGURED provider=groq" in output
    assert "gsk_synthetic-groq-test-token" not in output


def test_safe_key_diagnostics_reports_only_format_metadata(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "  synthetic-token\n")
    _set_config(monkeypatch, key="synthetic-token")

    diagnostics = check_llm_connectivity.safe_key_diagnostics()

    assert diagnostics == {
        "exists": True,
        "trimmed_length": len("synthetic-token"),
        "whitespace_detected": True,
        "newline_detected": True,
        "quote_detected": False,
        "non_ascii_detected": False,
    }


def test_connectivity_reports_connected_only_for_exact_prompt_response(monkeypatch, capsys):
    _set_config(monkeypatch, timeout=37)
    captured = {}

    def fake_completion(messages, **kwargs):
        captured["messages"] = messages
        captured["kwargs"] = kwargs
        return {"content": " OK\n", "model": MODEL, "usage": {}, "http_status": 200}

    monkeypatch.setattr(check_llm_connectivity, "chat_completion", fake_completion)

    assert check_llm_connectivity.main() == 0
    output = capsys.readouterr().out
    assert f"CONFIGURED provider=groq model={MODEL}" in output
    assert "timeout_seconds=37" in output
    assert "CONNECTED http_status=200 response_parsed=true expected_content_match=true elapsed_ms=" in output
    assert captured["kwargs"]["timeout"] == 37
    assert captured["kwargs"]["max_tokens"] == 128
    assert captured["messages"] == [{"role": "user", "content": "Reply with exactly OK."}]


@pytest.mark.parametrize(("failure", "status"), [
    (LLMHTTPError(401), "AUTHENTICATION_FAILED http_status=401"),
    (LLMHTTPError(403), "PERMISSION_DENIED http_status=403"),
    (LLMHTTPError(404), "MODEL_UNAVAILABLE http_status=404"),
    (LLMHTTPError(410), "MODEL_UNAVAILABLE http_status=410"),
    (LLMHTTPError(408), "PROVIDER_TIMEOUT http_status=408"),
    (LLMHTTPError(429), "RATE_LIMITED http_status=429"),
    (LLMHTTPError(500), "PROVIDER_SERVER_ERROR http_status=500"),
    (LLMRequestError("dns"), "DNS_FAILED error=network_or_timeout"),
    (LLMRequestError("connection"), "CONNECTION_FAILED error=network_or_timeout"),
    (LLMRequestError("tls"), "TLS_FAILED error=network_or_timeout"),
    (LLMRequestError("connect_timeout"), "CONNECT_TIMEOUT error=network_or_timeout"),
    (LLMRequestError("read_timeout"), "READ_TIMEOUT error=network_or_timeout"),
    (LLMRequestError("timeout"), "TIMEOUT error=network_or_timeout"),
    (LLMRequestError("network"), "NETWORK_FAILED error=network_or_timeout"),
    (LLMResponseError("invalid_json", 200), "RESPONSE_INVALID http_status=200 error=invalid_json"),
])
def test_connectivity_classifies_failures_without_leaking_details(monkeypatch, capsys, failure, status):
    _set_config(monkeypatch)

    def fail(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(check_llm_connectivity, "chat_completion", fail)

    assert check_llm_connectivity.main() == 1
    output = capsys.readouterr().out
    assert status in output
    assert "gsk_synthetic-groq-test-token" not in output
    assert "synthetic network failure" not in output


def test_connectivity_reports_empty_model_response_safely(monkeypatch, capsys):
    _set_config(monkeypatch)
    safe_metadata = {
        "content_type": "NoneType",
        "reasoning_content_present": True,
        "reasoning_content_length": 24,
        "refusal_present": False,
        "finish_reason": "length",
        "completion_tokens": 12,
    }

    def empty_response(*_args, **_kwargs):
        raise LLMResponseError("empty_content", 200, safe_metadata)

    monkeypatch.setattr(check_llm_connectivity, "chat_completion", empty_response)
    assert check_llm_connectivity.main() == 1
    output = capsys.readouterr().out
    assert "RESPONSE_INVALID http_status=200 error=empty_content" in output
    assert "content_type=NoneType" in output
    assert "reasoning_content_present=true" in output
    assert "reasoning_content_length=24" in output
    assert "finish_reason=length" in output
    assert "completion_tokens=12" in output
