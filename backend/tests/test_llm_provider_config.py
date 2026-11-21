import pytest

from app.core.config import load_settings


LEGACY_VARIABLES = (
    "LLM_PROVIDER", "LLM_MODEL", "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN",
    "RELATIONSHIP_LLM_PROVIDER", "RELATIONSHIP_LLM_MODEL", "XAI_API_KEY", "XAI_BASE_URL", "XAI_MODEL",
    "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL",
    "OPENROUTER_API_KEY", "OPENROUTER_BASE_URL", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL",
    "GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GEMINI_API_KEY", "OLLAMA_HOST", "OLLAMA_BASE_URL", "GROQ_BASE_URL",
)


@pytest.fixture(autouse=True)
def clean_llm_environment(monkeypatch):
    for name in LEGACY_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_MODEL", raising=False)


def test_groq_is_the_only_provider_and_uses_central_configuration(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_synthetic-groq-key")
    monkeypatch.setenv("GROQ_MODEL", "synthetic-groq-model")

    configured = load_settings()

    assert not hasattr(configured, "llm_provider")
    assert configured.llm_api_key == "gsk_synthetic-groq-key"
    assert configured.llm_base_url == "https://api.groq.com/openai/v1"
    assert configured.llm_model == "synthetic-groq-model"


@pytest.mark.parametrize("name", LEGACY_VARIABLES)
def test_legacy_provider_configuration_is_rejected(monkeypatch, name):
    monkeypatch.setenv(name, "legacy-synthetic-value")
    with pytest.raises(RuntimeError, match="Legacy or conflicting LLM configuration"):
        load_settings()


def test_groq_model_defaults_explicitly(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_synthetic-groq-key")
    configured = load_settings()
    assert configured.llm_model == "openai/gpt-oss-120b"
    assert configured.llm_relationship_completion_tokens == 8192


def test_empty_groq_model_is_rejected_without_fallback(monkeypatch):
    monkeypatch.setenv("GROQ_MODEL", "  ")
    with pytest.raises(RuntimeError, match="GROQ_MODEL must be a non-empty"):
        load_settings()


def test_connectivity_timeout_can_be_configured_with_a_safe_range(monkeypatch):
    monkeypatch.setenv("LLM_CONNECTIVITY_TIMEOUT_SECONDS", "90")
    assert load_settings().llm_connectivity_timeout_seconds == 90
    assert load_settings().llm_request_timeout_seconds == 240


def test_connectivity_timeout_rejects_out_of_range_values(monkeypatch):
    monkeypatch.setenv("LLM_CONNECTIVITY_TIMEOUT_SECONDS", "900")
    with pytest.raises(RuntimeError, match="between 1 and 300 seconds"):
        load_settings()


def test_request_timeout_can_be_configured_and_bounds_are_validated(monkeypatch):
    monkeypatch.setenv("LLM_REQUEST_TIMEOUT_SECONDS", "210")
    monkeypatch.delenv("LLM_CONNECTIVITY_TIMEOUT_SECONDS", raising=False)
    assert load_settings().llm_request_timeout_seconds == 210
    assert load_settings().llm_connectivity_timeout_seconds == 210
    monkeypatch.setenv("LLM_REQUEST_TIMEOUT_SECONDS", "301")
    with pytest.raises(RuntimeError, match="between 1 and 300 seconds"):
        load_settings()


def test_relationship_chunking_and_completion_limits_are_configurable(monkeypatch):
    monkeypatch.setenv("LLM_RELATIONSHIP_CHUNK_CHARACTERS", "6000")
    monkeypatch.setenv("LLM_RELATIONSHIP_CHUNK_OVERLAP", "500")
    monkeypatch.setenv("LLM_RELATIONSHIP_COMPLETION_TOKENS", "5000")
    configured = load_settings()
    assert configured.llm_relationship_chunk_characters == 6000
    assert configured.llm_relationship_chunk_overlap == 500
    assert configured.llm_relationship_completion_tokens == 5000

    monkeypatch.setenv("LLM_RELATIONSHIP_CHUNK_OVERLAP", "3000")
    with pytest.raises(RuntimeError, match="less than half"):
        load_settings()


def test_local_and_docker_workers_share_backend_provider_environment():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
    assert compose.count("./backend/.env") == 2  # API and OCR worker
    assert "LLM_PROVIDER:" not in compose
    assert "RELATIONSHIP_LLM_PROVIDER:" not in compose
    assert "GROQ_API_KEY:" not in compose
    frontend_env = root / "frontend" / "DocForensIQ-frontend" / ".env.example"
    assert "GROQ_API_KEY=" not in frontend_env.read_text(encoding="utf-8")
