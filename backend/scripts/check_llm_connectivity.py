"""Prompt-only Groq connectivity check; never prints secrets or response bodies."""
import sys
import os
import time

from app.core.config import settings
from app.services.llm_client import LLMConfigurationError, LLMHTTPError, LLMRequestError, LLMResponseError, chat_completion


def safe_key_diagnostics() -> dict[str, bool | int]:
    """Return metadata only; never return or log the credential value."""
    raw_key = os.environ.get("GROQ_API_KEY", "")
    return {
        "exists": bool(settings.llm_api_key),
        "trimmed_length": len(raw_key.strip()),
        "whitespace_detected": any(character.isspace() for character in raw_key),
        "newline_detected": "\n" in raw_key or "\r" in raw_key,
        "quote_detected": any(character in raw_key for character in ("'", '"')),
        "non_ascii_detected": any(ord(character) > 127 for character in raw_key),
    }


def main() -> int:
    diagnostics = safe_key_diagnostics()
    print(
        "API_KEY_DIAGNOSTICS "
        f"exists={str(diagnostics['exists']).lower()} "
        f"trimmed_length={diagnostics['trimmed_length']} "
        f"whitespace_detected={str(diagnostics['whitespace_detected']).lower()} "
        f"newline_detected={str(diagnostics['newline_detected']).lower()} "
        f"quote_detected={str(diagnostics['quote_detected']).lower()} "
        f"non_ascii_detected={str(diagnostics['non_ascii_detected']).lower()}"
    )
    if not settings.llm_api_key or settings.llm_base_url != "https://api.groq.com/openai/v1":
        missing = "GROQ_API_KEY" if not settings.llm_api_key else "GROQ_PROVIDER_SETTINGS"
        print(f"NOT_CONFIGURED provider=groq missing_configuration={missing}")
        return 2
    print(
        f"CONFIGURED provider=groq model={settings.llm_model} "
        f"timeout_seconds={settings.llm_connectivity_timeout_seconds:g}"
    )
    started = time.monotonic()
    try:
        result = chat_completion(
            [{"role": "user", "content": "Reply with exactly OK."}],
            max_tokens=128,
            temperature=0,
            timeout=settings.llm_connectivity_timeout_seconds,
        )
    except LLMConfigurationError:
        print("NOT_CONFIGURED provider=groq missing_configuration=provider_settings")
        return 2
    except LLMHTTPError as exc:
        if exc.status_code == 401:
            status = "AUTHENTICATION_FAILED"
        elif exc.status_code == 403:
            status = "PERMISSION_DENIED"
        elif exc.status_code in (404, 410):
            status = "MODEL_UNAVAILABLE"
        elif exc.status_code == 408:
            status = "PROVIDER_TIMEOUT"
        elif exc.status_code == 429:
            status = "RATE_LIMITED"
        elif exc.status_code >= 500:
            status = "PROVIDER_SERVER_ERROR"
        else:
            status = "REQUEST_FAILED"
        elapsed_ms = round((time.monotonic() - started) * 1000)
        print(f"{status} http_status={exc.status_code} elapsed_ms={elapsed_ms}")
        return 1
    except LLMRequestError as exc:
        status = {
            "dns": "DNS_FAILED",
            "connection": "CONNECTION_FAILED",
            "tls": "TLS_FAILED",
            "connect_timeout": "CONNECT_TIMEOUT",
            "read_timeout": "READ_TIMEOUT",
            "timeout": "TIMEOUT",
        }.get(exc.category, "NETWORK_FAILED")
        elapsed_ms = round((time.monotonic() - started) * 1000)
        print(f"{status} error=network_or_timeout elapsed_ms={elapsed_ms}")
        return 1
    except LLMResponseError as exc:
        http_status = f" http_status={exc.status_code}" if exc.status_code is not None else ""
        elapsed_ms = round((time.monotonic() - started) * 1000)
        details = exc.safe_metadata
        safe_detail = ""
        if details:
            safe_detail = (
                f" content_type={details.get('content_type', 'unknown')}"
                f" reasoning_content_present={str(bool(details.get('reasoning_content_present'))).lower()}"
                f" refusal_present={str(bool(details.get('refusal_present'))).lower()}"
            )
            if isinstance(details.get("reasoning_content_length"), int):
                safe_detail += f" reasoning_content_length={details['reasoning_content_length']}"
            finish_reason = details.get("finish_reason")
            if isinstance(finish_reason, str) and finish_reason and all(c.isalnum() or c in "_-" for c in finish_reason):
                safe_detail += f" finish_reason={finish_reason[:32]}"
            if isinstance(details.get("completion_tokens"), int):
                safe_detail += f" completion_tokens={details['completion_tokens']}"
        print(f"RESPONSE_INVALID{http_status} error={exc.category}{safe_detail} elapsed_ms={elapsed_ms}")
        return 1

    elapsed_ms = round((time.monotonic() - started) * 1000)
    status_code = result["http_status"]
    content = result["content"]
    if content.strip() != "OK":
        print(
            f"RESPONSE_INVALID http_status={status_code} response_parsed=true "
            f"expected_content_match=false content_nonempty={str(bool(content.strip())).lower()} "
            f"content_length={len(content)} elapsed_ms={elapsed_ms}"
        )
        return 1
    print(f"CONNECTED http_status={status_code} response_parsed=true expected_content_match=true elapsed_ms={elapsed_ms}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
