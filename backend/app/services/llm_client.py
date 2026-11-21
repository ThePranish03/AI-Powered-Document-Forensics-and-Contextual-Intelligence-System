"""Content-safe Groq client shared by every LLM-dependent feature."""
import socket
import ssl

import httpx

from app.core.config import settings


_GROQ_SCHEMA_KEYWORDS = {
    "$defs", "$ref", "additionalProperties", "anyOf", "description", "enum",
    "items", "properties", "required", "type",
}


def _groq_compatible_schema(value):
    """Keep Groq's documented schema subset; Pydantic enforces omitted bounds locally."""
    if isinstance(value, list):
        return [_groq_compatible_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key == "const":
            result["enum"] = [item]
        elif key in {"properties", "$defs"} and isinstance(item, dict):
            result[key] = {name: _groq_compatible_schema(schema) for name, schema in item.items()}
        elif key in _GROQ_SCHEMA_KEYWORDS:
            result[key] = _groq_compatible_schema(item)
    return result


class LLMConfigurationError(RuntimeError):
    pass


class LLMHTTPError(RuntimeError):
    def __init__(self, status_code: int):
        self.status_code = status_code
        self.provider = "groq"
        super().__init__(f"LLM provider returned HTTP {status_code}")


class LLMRequestError(RuntimeError):
    def __init__(self, category: str = "network"):
        self.category = category
        self.provider = "groq"
        super().__init__(f"LLM provider {category} failure")


class LLMResponseError(RuntimeError):
    def __init__(
        self,
        category: str = "provider_response",
        status_code: int | None = None,
        safe_metadata: dict | None = None,
    ):
        self.category = category
        self.status_code = status_code
        self.safe_metadata = safe_metadata or {}
        self.provider = "groq"
        super().__init__(f"LLM provider returned {category}")


def chat_completion(
    messages: list[dict],
    *,
    max_tokens: int,
    temperature: float | None = None,
    response_format: dict | None = None,
    timeout: float | None = None,
) -> dict:
    """Call Groq using the single centralized credential, endpoint, and model.

    Provider error bodies and credentials are never returned or logged.
    """
    api_key = settings.llm_api_key
    base_url = settings.llm_base_url
    model = settings.llm_model
    if not api_key:
        raise LLMConfigurationError("GROQ_API_KEY is not configured")
    if timeout is None:
        timeout = settings.llm_request_timeout_seconds

    body = {
        "model": model,
        "messages": messages,
        "max_completion_tokens": max_tokens,
        "stream": False,
    }
    if response_format is not None:
        if response_format.get("type") == "json_schema":
            requested_schema = response_format.get("json_schema") or {}
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": requested_schema.get("name", "structured_response"),
                    "strict": requested_schema.get("strict", True),
                    "schema": _groq_compatible_schema(requested_schema.get("schema", {})),
                },
            }
        else:
            body["response_format"] = response_format
    if temperature is not None:
        body["temperature"] = temperature
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout, connect=min(timeout, 10.0))) as client:
            response = client.post(
                base_url.rstrip("/") + "/chat/completions",
                json=body,
                headers=headers,
            )
            response_status = response.status_code
            try:
                payload = response.json()
            except ValueError:
                payload = None
            if not response.is_success:
                raise LLMHTTPError(response_status) from None
            if payload is None:
                raise LLMResponseError("invalid_json", response_status) from None
    except (LLMHTTPError, LLMResponseError):
        raise
    except httpx.ConnectTimeout:
        raise LLMRequestError("connect_timeout") from None
    except httpx.ReadTimeout:
        raise LLMRequestError("read_timeout") from None
    except httpx.TimeoutException:
        raise LLMRequestError("timeout") from None
    except httpx.ConnectError as exc:
        reason = exc.__cause__
        if isinstance(reason, socket.gaierror):
            category = "dns"
        elif isinstance(reason, ssl.SSLError):
            category = "tls"
        else:
            category = "connection"
        raise LLMRequestError(category) from None
    except httpx.TransportError:
        raise LLMRequestError("network") from None

    if not isinstance(payload, dict):
        raise LLMResponseError("unexpected_top_level_type", response_status)
    choices = payload.get("choices")
    if choices is None and "choices" not in payload:
        raise LLMResponseError("missing_choices", response_status)
    if not isinstance(choices, list):
        raise LLMResponseError("invalid_choices_type", response_status)
    if not choices:
        raise LLMResponseError("empty_choices", response_status)
    choice = choices[0]
    if not isinstance(choice, dict):
        raise LLMResponseError("invalid_choice_type", response_status)
    message = choice.get("message")
    if message is None and "message" not in choice:
        raise LLMResponseError("missing_message", response_status)
    if not isinstance(message, dict):
        raise LLMResponseError("invalid_message_type", response_status)
    refusal = message.get("refusal")
    if refusal is not None and refusal != "":
        if isinstance(refusal, str):
            raise LLMResponseError("refusal", response_status)
        raise LLMResponseError("invalid_refusal_type", response_status)
    if choice.get("finish_reason") == "content_filter":
        raise LLMResponseError("refusal", response_status)
    if "content" not in message:
        raise LLMResponseError("missing_content", response_status)
    content = message["content"]
    if content is None or content == "" or (isinstance(content, str) and not content.strip()) or content == []:
        usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
        reasoning_content = message.get("reasoning_content")
        safe_metadata = {
            "content_type": type(content).__name__,
            "reasoning_content_present": bool(reasoning_content),
            "reasoning_content_length": len(reasoning_content) if isinstance(reasoning_content, str) else None,
            "refusal_present": bool(refusal),
            "finish_reason": choice.get("finish_reason") if isinstance(choice.get("finish_reason"), str) else None,
            "completion_tokens": usage.get("completion_tokens") if isinstance(usage.get("completion_tokens"), int) else None,
        }
        raise LLMResponseError("empty_content", response_status, safe_metadata) from None
    if not isinstance(content, str):
        # Keep unsupported part arrays/objects explicit rather than silently
        # dropping non-text blocks or weakening downstream schema validation.
        raise LLMResponseError(f"invalid_content_type:{type(content).__name__}", response_status)

    return {
        "content": content,
        "model": payload.get("model") or model,
        "usage": payload.get("usage"),
        "http_status": response_status,
        "finish_reason": choice.get("finish_reason") if isinstance(choice.get("finish_reason"), str) else None,
    }


