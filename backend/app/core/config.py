import os
import secrets
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    app_env: str
    database_url: str
    jwt_secret: str
    frontend_origin: str
    self_registration_enabled: bool
    cookie_secure: bool
    access_token_minutes: int
    refresh_token_days: int
    password_reset_minutes: int
    email_sink_file: str
    document_storage_path: str
    llm_api_key: str
    llm_base_url: str
    llm_model: str
    llm_request_timeout_seconds: float
    llm_connectivity_timeout_seconds: float
    llm_relationship_chunk_characters: int
    llm_relationship_chunk_overlap: int
    llm_relationship_completion_tokens: int
    relationship_extraction_enabled: bool
    visual_forensics_enabled: bool
    trufor_license_cleared: bool
    trufor_worker_url: str
    trufor_model_version: str
    trufor_checkpoint_sha256: str
    trufor_device: str
    trufor_max_image_pixels: int
    trufor_render_dpi: int
    trufor_job_timeout_seconds: float
    trufor_max_concurrent_pages: int
    visual_forensics_artifact_dir: str

    @property
    def production(self) -> bool:
        return self.app_env == "production"


def load_settings() -> Settings:
    env = os.getenv("APP_ENV", "development")
    secret = os.getenv("JWT_SECRET", "")
    weak_markers = ("replace", "change-me", "development", "example", "placeholder")
    if env == "production" and (
        len(secret) < 32
        or len(set(secret)) < 16
        or any(marker in secret.lower() for marker in weak_markers)
    ):
        raise RuntimeError("JWT_SECRET must be configured with at least 32 characters in production")
    database_url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./docforensiq.db")
    frontend_origin = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")
    legacy_provider_variables = (
        "LLM_PROVIDER", "LLM_MODEL", "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN",
        "RELATIONSHIP_LLM_PROVIDER", "RELATIONSHIP_LLM_MODEL", "XAI_API_KEY", "XAI_BASE_URL", "XAI_MODEL",
        "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL",
        "OPENROUTER_API_KEY", "OPENROUTER_BASE_URL", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL",
        "GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GEMINI_API_KEY", "OLLAMA_HOST", "OLLAMA_BASE_URL", "GROQ_BASE_URL",
    )
    conflicting = [name for name in legacy_provider_variables if os.getenv(name, "").strip()]
    if conflicting:
        raise RuntimeError(
            "Legacy or conflicting LLM configuration is set; remove obsolete provider variables: "
            + ", ".join(conflicting)
        )
    groq_model = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b").strip()
    if not groq_model:
        raise RuntimeError("GROQ_MODEL must be a non-empty Groq model ID")
    llm_request_timeout_seconds = float(os.getenv("LLM_REQUEST_TIMEOUT_SECONDS", "240"))
    if not 1 <= llm_request_timeout_seconds <= 300:
        raise RuntimeError("LLM_REQUEST_TIMEOUT_SECONDS must be between 1 and 300 seconds")
    llm_connectivity_timeout_seconds = float(os.getenv(
        "LLM_CONNECTIVITY_TIMEOUT_SECONDS", str(llm_request_timeout_seconds),
    ))
    if not 1 <= llm_connectivity_timeout_seconds <= 300:
        raise RuntimeError("LLM_CONNECTIVITY_TIMEOUT_SECONDS must be between 1 and 300 seconds")
    llm_relationship_chunk_characters = int(os.getenv("LLM_RELATIONSHIP_CHUNK_CHARACTERS", "4500"))
    llm_relationship_chunk_overlap = int(os.getenv("LLM_RELATIONSHIP_CHUNK_OVERLAP", "400"))
    llm_relationship_completion_tokens = int(os.getenv("LLM_RELATIONSHIP_COMPLETION_TOKENS", "8192"))
    if not 1000 <= llm_relationship_chunk_characters <= 12000:
        raise RuntimeError("LLM_RELATIONSHIP_CHUNK_CHARACTERS must be between 1000 and 12000")
    if not 0 <= llm_relationship_chunk_overlap < llm_relationship_chunk_characters // 2:
        raise RuntimeError("LLM_RELATIONSHIP_CHUNK_OVERLAP must be less than half the chunk character limit")
    if not 512 <= llm_relationship_completion_tokens <= 32768:
        raise RuntimeError("LLM_RELATIONSHIP_COMPLETION_TOKENS must be between 512 and 32768")
    if env == "production" and database_url.startswith("sqlite"):
        raise RuntimeError("A production DATABASE_URL must use PostgreSQL")
    if env == "production" and not frontend_origin.startswith("https://"):
        raise RuntimeError("FRONTEND_ORIGIN must use HTTPS in production")
    if not secret:
        secret = secrets.token_urlsafe(48)
    return Settings(
        app_env=env,
        database_url=database_url,
        jwt_secret=secret,
        frontend_origin=frontend_origin,
        self_registration_enabled=os.getenv("SELF_REGISTRATION_ENABLED", "false").lower() == "true",
        cookie_secure=os.getenv("COOKIE_SECURE", "false").lower() == "true" or env == "production",
        access_token_minutes=int(os.getenv("ACCESS_TOKEN_MINUTES", "15")),
        refresh_token_days=int(os.getenv("REFRESH_TOKEN_DAYS", "14")),
        password_reset_minutes=int(os.getenv("PASSWORD_RESET_MINUTES", "30")),
        email_sink_file=os.getenv("EMAIL_SINK_FILE", ".dev-mailbox.jsonl"),
        document_storage_path=os.getenv("DOCUMENT_STORAGE_PATH", "./private_uploads"),
        llm_api_key=os.getenv("GROQ_API_KEY", "").strip(),
        llm_base_url="https://api.groq.com/openai/v1",
        llm_model=groq_model,
        llm_request_timeout_seconds=llm_request_timeout_seconds,
        llm_connectivity_timeout_seconds=llm_connectivity_timeout_seconds,
        llm_relationship_chunk_characters=llm_relationship_chunk_characters,
        llm_relationship_chunk_overlap=llm_relationship_chunk_overlap,
        llm_relationship_completion_tokens=llm_relationship_completion_tokens,
        relationship_extraction_enabled=os.getenv("RELATIONSHIP_EXTRACTION_ENABLED", "false").lower() == "true",
        visual_forensics_enabled=os.getenv("VISUAL_FORENSICS_ENABLED", "false").lower() == "true",
        trufor_license_cleared=os.getenv("TRUFOR_LICENSE_CLEARED", "false").lower() == "true",
        trufor_worker_url=os.getenv("TRUFOR_WORKER_URL", "").rstrip("/"),
        trufor_model_version=os.getenv("TRUFOR_MODEL_VERSION", ""),
        trufor_checkpoint_sha256=os.getenv("TRUFOR_CHECKPOINT_SHA256", ""),
        trufor_device=os.getenv("TRUFOR_DEVICE", "cpu"),
        trufor_max_image_pixels=int(os.getenv("TRUFOR_MAX_IMAGE_PIXELS", str(20_000_000))),
        trufor_render_dpi=int(os.getenv("TRUFOR_RENDER_DPI", "200")),
        trufor_job_timeout_seconds=float(os.getenv("TRUFOR_JOB_TIMEOUT_SECONDS", "120")),
        trufor_max_concurrent_pages=int(os.getenv("TRUFOR_MAX_CONCURRENT_PAGES", "1")),
        visual_forensics_artifact_dir=os.getenv("VISUAL_FORENSICS_ARTIFACT_DIR", "./private_uploads/visual_artifacts"),
    )


settings = load_settings()
