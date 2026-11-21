import json
import socket
import ssl
from dataclasses import replace

import httpx

import pytest

from app.core.config import settings
from app.services import llm_client, relationship_extraction
from app.services.llm_client import LLMHTTPError, LLMRequestError, LLMResponseError


BASE_URL = "https://api.groq.com/openai/v1"
MODEL = "openai/gpt-oss-120b"
TEST_TOKEN = "gsk_synthetic-groq-test-token"


def test_groq_schema_adapter_preserves_shape_and_leaves_bounds_to_local_validation():
    schema = {
        "type": "object", "properties": {
            "version": {"const": "v1"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "items": {"type": "array", "minItems": 1, "items": {"type": "string", "maxLength": 8}},
        },
        "required": ["version", "confidence", "items"], "additionalProperties": False,
    }

    adapted = llm_client._groq_compatible_schema(schema)

    assert adapted == {
        "type": "object", "properties": {
            "version": {"enum": ["v1"]},
            "confidence": {"type": "number"},
            "items": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["version", "confidence", "items"], "additionalProperties": False,
    }
    assert schema["properties"]["confidence"]["minimum"] == 0


def _configure_client(monkeypatch, **updates):
    values = {
        "llm_api_key": TEST_TOKEN,
        "llm_base_url": BASE_URL,
        "llm_model": MODEL,
    }
    values.update(updates)
    monkeypatch.setattr(llm_client, "settings", replace(settings, **values))


def _mock_httpx(monkeypatch, *, response=None, failure=None, captured=None):
    captured = captured if captured is not None else {}

    class FakeClient:
        def __init__(self, timeout):
            captured["timeout"] = timeout

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            if response is not None:
                response.close()

        def post(self, url, *, json, headers):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            if failure is not None:
                if isinstance(failure, tuple):
                    raise failure[0] from failure[1]
                raise failure
            return response

    monkeypatch.setattr(llm_client.httpx, "Client", FakeClient)
    return captured


def test_chat_completion_uses_groq_auth_endpoint_model_and_parses_content(monkeypatch):
    _configure_client(monkeypatch)
    response = httpx.Response(200, json={
            "model": MODEL,
            "choices": [{"message": {"role": "assistant", "content": "OK", "refusal": None}, "finish_reason": "stop"}],
            "usage": {"total_tokens": 7},
        })
    captured = _mock_httpx(monkeypatch, response=response)
    result = llm_client.chat_completion(
        [{"role": "user", "content": "Reply with exactly OK."}],
        max_tokens=4,
        temperature=0,
        response_format={"type": "json_object"},
        timeout=15,
    )

    body = captured["json"]
    assert captured["url"] == BASE_URL + "/chat/completions"
    assert captured["headers"]["Authorization"] == f"Bearer {TEST_TOKEN}"
    assert captured["timeout"].read == 15
    assert body["model"] == MODEL
    assert body["max_completion_tokens"] == 4
    assert "max_tokens" not in body
    assert "chat_template_kwargs" not in body
    assert body["response_format"] == {"type": "json_object"}
    assert TEST_TOKEN not in json.dumps(body)
    assert response.is_closed
    assert result == {"content": "OK", "model": MODEL, "usage": {"total_tokens": 7}, "http_status": 200, "finish_reason": "stop"}


def test_chat_completion_uses_groq_endpoint_and_schema_constrained_output(monkeypatch):
    _configure_client(monkeypatch, llm_api_key="gsk_synthetic-groq-key")
    response = httpx.Response(200, json={
        "model": "openai/gpt-oss-120b",
        "choices": [{"message": {"content": "{}", "refusal": None}, "finish_reason": "stop"}],
        "usage": {"total_tokens": 7},
    })
    captured = _mock_httpx(monkeypatch, response=response)
    schema_format = {"type": "json_schema", "json_schema": {"name": "relationship_extraction_v1", "strict": True, "schema": {"type": "object"}}}

    result = llm_client.chat_completion(
        [{"role": "user", "content": "Return JSON."}], max_tokens=100,
        temperature=0, response_format=schema_format,
    )

    assert captured["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer gsk_synthetic-groq-key"
    assert captured["json"]["model"] == "openai/gpt-oss-120b"
    assert captured["json"]["max_completion_tokens"] == 100
    assert captured["json"]["response_format"] == schema_format
    assert result["content"] == "{}"
    assert "gsk_synthetic-groq-key" not in str(captured["json"])


def test_groq_incomplete_response_is_reported_as_token_truncation(monkeypatch):
    _configure_client(monkeypatch, llm_api_key="gsk_synthetic-groq-key")
    response = httpx.Response(200, json={
        "model": "openai/gpt-oss-120b",
        "choices": [{"message": {"content": "{", "refusal": None}, "finish_reason": "length"}],
    })
    _mock_httpx(monkeypatch, response=response)

    result = llm_client.chat_completion(
        [{"role": "user", "content": "Synthetic prompt"}], max_tokens=4,
    )

    assert result["finish_reason"] == "length"
    assert result["content"] == "{"


def test_groq_auth_error_discards_provider_body_and_credential(monkeypatch):
    secret = "gsk_synthetic-groq-secret"
    _configure_client(monkeypatch, llm_api_key=secret)
    _mock_httpx(monkeypatch, response=httpx.Response(401, json={"error": {"message": secret}}))

    with pytest.raises(LLMHTTPError) as error:
        llm_client.chat_completion(
            [{"role": "user", "content": "Synthetic prompt"}], max_tokens=4,
        )

    assert error.value.status_code == 401
    assert error.value.provider == "groq"
    assert secret not in str(error.value)


def test_chat_completion_requires_groq_key_and_never_falls_back(monkeypatch):
    _configure_client(monkeypatch, llm_api_key="")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "legacy-deepseek-test-key")
    monkeypatch.setenv("XAI_API_KEY", "legacy-xai-test-key")
    called = False

    def forbidden_request(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("request must not be sent without a Groq key")

    monkeypatch.setattr(llm_client.httpx, "Client", forbidden_request)
    with pytest.raises(llm_client.LLMConfigurationError, match="GROQ_API_KEY"):
        llm_client.chat_completion([{"role": "user", "content": "OK"}], max_tokens=4)
    assert not called


@pytest.mark.parametrize(("status", "body"), [
    (401, b'{"error":{"message":"synthetic-token-should-not-leak"}}'),
    (403, b'{"error":{"message":"permission denied"}}'),
    (404, b'{"error":{"message":"model unavailable"}}'),
    (429, b'{"error":{"message":"rate limited"}}'),
    (500, b'{"error":{"message":"provider failure"}}'),
])
def test_chat_completion_safely_classifies_groq_errors(monkeypatch, status, body):
    _configure_client(monkeypatch)
    _mock_httpx(monkeypatch, response=httpx.Response(status, content=body))
    with pytest.raises(LLMHTTPError) as error:
        llm_client.chat_completion([{"role": "user", "content": "OK"}], max_tokens=4)
    assert error.value.status_code == status
    assert TEST_TOKEN not in str(error.value)
    assert "synthetic-token-should-not-leak" not in str(error.value)


@pytest.mark.parametrize(("failure", "category"), [
    ((httpx.ConnectError("synthetic DNS failure"), socket.gaierror("synthetic DNS failure")), "dns"),
    ((httpx.ConnectError("synthetic TLS failure"), ssl.SSLError("synthetic TLS failure")), "tls"),
    (httpx.ConnectError("synthetic connection failure"), "connection"),
    (httpx.ConnectTimeout("synthetic connect timeout"), "connect_timeout"),
    (httpx.ReadTimeout("synthetic read timeout"), "read_timeout"),
    (httpx.TimeoutException("synthetic timeout"), "timeout"),
    (httpx.TransportError("synthetic network failure"), "network"),
])
def test_chat_completion_network_and_timeout_errors_do_not_leak_details(monkeypatch, failure, category):
    _configure_client(monkeypatch)
    _mock_httpx(monkeypatch, failure=failure)
    with pytest.raises(LLMRequestError) as error:
        llm_client.chat_completion([{"role": "user", "content": "OK"}], max_tokens=4)
    assert error.value.category == category
    assert "synthetic network failure" not in str(error.value)
    assert "synthetic timeout" not in str(error.value)
    assert TEST_TOKEN not in str(error.value)


@pytest.mark.parametrize(("raw", "category"), [
    (b"not-json", "invalid_json"),
    (b"{}", "missing_choices"),
    (b'{"choices":[]}', "empty_choices"),
    (b'{"choices":[{}]}', "missing_message"),
    (b'{"choices":[{"message":{}}]}', "missing_content"),
    (b'{"choices":[{"message":{"content":null}}]}', "empty_content"),
    (b'{"choices":[{"message":{"content":""}}]}', "empty_content"),
    (b'{"choices":[{"message":{"content":"   "}}]}', "empty_content"),
    (b'{"choices":[{"message":{"content":[]}}]}', "empty_content"),
    (b'{"choices":[{"message":{"content":[{"type":"text","text":"OK"}]}}]}', "invalid_content_type:list"),
    (b'{"choices":[{"message":{"content":{"text":"OK"}}}]}', "invalid_content_type:dict"),
    (b'{"choices":[{"message":{"content":null,"refusal":"blocked"}}]}', "refusal"),
    (b'{"choices":[{"message":{"content":"blocked"},"finish_reason":"content_filter"}]}', "refusal"),
])
def test_chat_completion_invalid_provider_responses_are_rejected(monkeypatch, raw, category):
    _configure_client(monkeypatch)
    _mock_httpx(monkeypatch, response=httpx.Response(200, content=raw))

    with pytest.raises(LLMResponseError) as error:
        llm_client.chat_completion([{"role": "user", "content": "OK"}], max_tokens=4)
    assert error.value.category == category


def test_empty_content_reports_reasoning_metadata_without_exposing_text(monkeypatch):
    _configure_client(monkeypatch)
    response = httpx.Response(200, json={
        "choices": [{
            "message": {
                "content": None,
                "reasoning_content": "synthetic-private-reasoning-text",
                "refusal": None,
            },
            "finish_reason": "length",
        }],
        "usage": {"completion_tokens": 12},
    })
    _mock_httpx(monkeypatch, response=response)

    with pytest.raises(LLMResponseError) as error:
        llm_client.chat_completion([{"role": "user", "content": "Reply OK"}], max_tokens=128)

    assert error.value.category == "empty_content"
    assert error.value.status_code == 200
    assert error.value.safe_metadata == {
        "content_type": "NoneType",
        "reasoning_content_present": True,
        "reasoning_content_length": len("synthetic-private-reasoning-text"),
        "refusal_present": False,
        "finish_reason": "length",
        "completion_tokens": 12,
    }
    assert "synthetic-private-reasoning-text" not in str(error.value)


def test_relationship_extraction_preserves_schema_validation_and_evidence_grounding(monkeypatch):
    _configure_client(monkeypatch)
    monkeypatch.setattr(relationship_extraction, "settings", replace(
        settings, llm_api_key=TEST_TOKEN, llm_model=MODEL, llm_base_url=BASE_URL,
    ))
    captured = {}
    model_json = {
        "schema_version": "relationship_extraction_v1",
        "entities": [{
            "mention_id": "m1", "entity_type": "PERSON", "mention_text": "Synthetic Person",
            "source_block_ids": ["block-1"], "confidence": 0.96,
        }],
        "relationships": [],
    }

    def fake_chat(messages, **kwargs):
        captured["messages"] = messages
        captured["kwargs"] = kwargs
        return {"content": json.dumps(model_json), "usage": {"total_tokens": 4}, "model": MODEL}

    monkeypatch.setattr(relationship_extraction, "chat_completion", fake_chat)
    graph, metadata = relationship_extraction.extract([{"block_id": "block-1", "text": "Synthetic Person"}])

    assert graph["schema_version"] == "relationship_extraction_v1"
    assert graph["entities"][0]["mention_text"] == "Synthetic Person"
    assert graph["entities"][0]["source_block_ids"] == ["block-1"]
    assert metadata["usage"] == [{"total_tokens": 4}]
    assert captured["kwargs"]["response_format"]["type"] == "json_schema"
    assert captured["kwargs"]["timeout"] == settings.llm_request_timeout_seconds
    assert "Return a JSON object matching this schema" in captured["messages"][0]["content"]


def test_relationship_extraction_requests_strict_groq_schema(monkeypatch):
    monkeypatch.setattr(relationship_extraction, "settings", replace(
        settings, llm_api_key="gsk_synthetic-groq-key", llm_base_url=BASE_URL, llm_model=MODEL,
    ))
    captured = {}

    def fake_chat(_messages, **kwargs):
        captured.update(kwargs)
        return _relationship_response("Synthetic Person", "block-1")

    monkeypatch.setattr(relationship_extraction, "chat_completion", fake_chat)
    relationship_extraction.extract([{"block_id": "block-1", "text": "Synthetic Person"}])

    assert captured["response_format"]["type"] == "json_schema"
    assert captured["response_format"]["json_schema"]["strict"] is True


def test_relationship_extraction_bounds_rate_limit_retries_without_fallback(monkeypatch):
    _configure_client(monkeypatch)
    monkeypatch.setattr(relationship_extraction, "settings", replace(
        settings, llm_api_key=TEST_TOKEN, llm_model=MODEL, llm_base_url=BASE_URL,
    ))
    calls = []

    def rate_limited(*_args, **_kwargs):
        calls.append(True)
        raise LLMHTTPError(429)

    monkeypatch.setattr(relationship_extraction, "chat_completion", rate_limited)
    monkeypatch.setattr(relationship_extraction.time, "sleep", lambda *_args: None)

    with pytest.raises(relationship_extraction.RelationshipExtractionError, match="bounded retries") as exc:
        relationship_extraction.extract([{"block_id": "block-1", "text": "Synthetic Person"}])
    assert exc.value.code == "provider_temporarily_unavailable"
    assert exc.value.retryable is True
    assert len(calls) == 3


def test_unavailable_groq_model_fails_without_model_or_provider_fallback(monkeypatch):
    _configure_client(monkeypatch)
    monkeypatch.setattr(relationship_extraction, "settings", replace(
        settings, llm_api_key=TEST_TOKEN, llm_model=MODEL, llm_base_url=BASE_URL,
    ))
    calls = []

    def unavailable(*_args, **_kwargs):
        calls.append(True)
        raise LLMHTTPError(404)

    monkeypatch.setattr(relationship_extraction, "chat_completion", unavailable)
    with pytest.raises(relationship_extraction.RelationshipExtractionError) as failure:
        relationship_extraction.extract([{"block_id": "block-1", "text": "Synthetic Person"}])

    assert failure.value.code == "provider_model_unavailable"
    assert failure.value.retryable is False
    assert calls == [True]


def _relationship_response(mention_text, block_id, mention_id="m1"):
    return {
        "content": json.dumps({
            "schema_version": "relationship_extraction_v1",
            "entities": [{
                "mention_id": mention_id, "entity_type": "PERSON", "mention_text": mention_text,
                "source_block_ids": [block_id], "confidence": 0.9,
            }],
            "relationships": [],
        }),
        "usage": {"total_tokens": 12}, "model": MODEL,
    }


def test_duplicate_prompt_text_keeps_all_source_citations(monkeypatch):
    _configure_client(monkeypatch)
    monkeypatch.setattr(relationship_extraction, "settings", replace(settings, llm_api_key=TEST_TOKEN, llm_model=MODEL, llm_base_url=BASE_URL))
    captured = {}

    def fake_chat(messages, **_kwargs):
        supplied = json.loads(messages[1]["content"])
        captured["blocks"] = supplied
        return _relationship_response("Synthetic Person", "block-a")

    monkeypatch.setattr(relationship_extraction, "chat_completion", fake_chat)
    graph, _ = relationship_extraction.extract([
        {"block_id": "block-a", "page_index": 0, "text": "Synthetic Person"},
        {"block_id": "block-b", "page_index": 1, "text": "Synthetic   Person"},
    ])

    assert len(captured["blocks"]) == 1
    assert graph["entities"][0]["source_block_ids"] == ["block-a", "block-b"]


def test_relationship_cache_key_tracks_model_input_and_chunk_configuration(monkeypatch):
    blocks = [{"block_id": "block-a", "page_index": 0, "text": "Synthetic Person"}]
    base = relationship_extraction.relationship_cache_key(blocks)
    assert base == relationship_extraction.relationship_cache_key(blocks)
    assert base != relationship_extraction.relationship_cache_key(blocks, model="different-model")
    assert base != relationship_extraction.relationship_cache_key([{**blocks[0], "text": "Changed Person"}])
    assert base != relationship_extraction.relationship_cache_key(blocks, chunk_characters=5000)


def test_relationship_extraction_records_stage_timings_and_uses_compact_prompt(monkeypatch):
    _configure_client(monkeypatch)
    monkeypatch.setattr(relationship_extraction, "settings", replace(settings, llm_api_key=TEST_TOKEN, llm_model=MODEL, llm_base_url=BASE_URL))
    captured = {}

    def fake_chat(messages, **_kwargs):
        captured["blocks"] = json.loads(messages[1]["content"])
        return _relationship_response("Synthetic Person", "block-a")

    monkeypatch.setattr(relationship_extraction, "chat_completion", fake_chat)
    graph, metadata = relationship_extraction.extract([{
        "block_id": "block-a", "page_index": 0, "text": "Synthetic Person",
        "extraction_method": "native_pdf_text", "engine_bbox": [1, 2, 3, 4],
    }])

    assert graph["cache_key"] == metadata["cache_key"]
    assert set(captured["blocks"][0]) == {"block_id", "page_index", "text"}
    assert set(metadata["stage_timings_ms"]) >= {
        "input_preparation_ms", "chunk_creation_ms", "llm_request_ms",
        "response_parsing_ms", "evidence_validation_ms",
        "entity_deduplication_ms", "relationship_matching_ms",
    }
    assert all(value >= 0 for value in metadata["stage_timings_ms"].values())
    assert graph["entities"][0]["source_block_ids"] == ["block-a"]


def test_long_document_resume_reuses_completed_chunks_and_retries_only_failed_chunk(monkeypatch):
    _configure_client(monkeypatch)
    monkeypatch.setattr(relationship_extraction, "settings", replace(settings, llm_api_key=TEST_TOKEN, llm_model=MODEL, llm_base_url=BASE_URL))
    monkeypatch.setattr(relationship_extraction.time, "sleep", lambda *_args: None)
    blocks = [
        {"block_id": "block-a", "page_index": 0, "text": "Entity Alpha " + "a" * 690},
        {"block_id": "block-b", "page_index": 1, "text": "Entity Beta " + "b" * 690},
    ]
    calls = {"block-a": 0, "block-b": 0}
    completed = []

    def first_attempt(messages, **_kwargs):
        block = json.loads(messages[1]["content"])[0]
        block_id = block["block_id"]
        calls[block_id] += 1
        if block_id == "block-b":
            raise LLMHTTPError(503)
        return _relationship_response("Entity Alpha", block_id)

    monkeypatch.setattr(relationship_extraction, "chat_completion", first_attempt)
    with pytest.raises(relationship_extraction.RelationshipExtractionError) as failure:
        relationship_extraction.extract(
            blocks, chunk_characters=1000, chunk_overlap=0,
            on_chunk_completed=completed.append,
        )
    assert failure.value.code == "provider_temporarily_unavailable"
    assert failure.value.retryable is True
    assert failure.value.failed_chunk_id
    assert len(completed) == 1
    assert len(failure.value.completed_chunks) == 1

    def retry_failed_only(messages, **_kwargs):
        block = json.loads(messages[1]["content"])[0]
        calls[block["block_id"]] += 1
        return _relationship_response("Entity Beta", block["block_id"])

    monkeypatch.setattr(relationship_extraction, "chat_completion", retry_failed_only)
    graph, metadata = relationship_extraction.extract(
        blocks, completed_chunks=failure.value.completed_chunks,
        chunk_characters=1000, chunk_overlap=0,
    )

    assert calls == {"block-a": 1, "block-b": 4}
    assert {entity["mention_text"] for entity in graph["entities"]} == {"Entity Alpha", "Entity Beta"}
    assert metadata["chunk_count"] == 2
    assert metadata["attempts_this_run"] == 1


def test_schema_grounding_rejects_invented_mentions_after_one_corrective_retry(monkeypatch):
    _configure_client(monkeypatch)
    monkeypatch.setattr(relationship_extraction, "settings", replace(settings, llm_api_key=TEST_TOKEN, llm_model=MODEL, llm_base_url=BASE_URL))
    monkeypatch.setattr(relationship_extraction.time, "sleep", lambda *_args: None)
    calls = []

    def fabricated(*_args, **_kwargs):
        calls.append(True)
        return _relationship_response("Fabricated Person", "block-a")

    monkeypatch.setattr(relationship_extraction, "chat_completion", fabricated)
    with pytest.raises(relationship_extraction.RelationshipExtractionError) as failure:
        relationship_extraction.extract([{"block_id": "block-a", "text": "Synthetic Person"}])
    assert failure.value.code == "ungrounded_model_output"
    assert failure.value.retryable is False
    assert failure.value.diagnostics["grounding_failure"]
    assert len(calls) == 2


def test_overlapping_chunks_merge_duplicate_entities_and_relationships(monkeypatch):
    _configure_client(monkeypatch)
    monkeypatch.setattr(relationship_extraction, "settings", replace(settings, llm_api_key=TEST_TOKEN, llm_model=MODEL, llm_base_url=BASE_URL))
    calls = []

    def fake_chat(messages, **_kwargs):
        supplied = json.loads(messages[1]["content"])
        calls.append([item["block_id"] for item in supplied])
        cited_blocks = [item["block_id"] for item in supplied]
        person_block = "block-a" if "block-a" in cited_blocks else "block-b"
        company_block = "block-b"
        return {"content": json.dumps({
            "schema_version": "relationship_extraction_v1",
            "entities": [
                {"mention_id": "person", "entity_type": "PERSON", "mention_text": "Person A", "source_block_ids": [person_block], "confidence": 0.9},
                {"mention_id": "company", "entity_type": "ORGANIZATION", "mention_text": "Company B", "source_block_ids": [company_block], "confidence": 0.8},
            ],
            "relationships": [{
                "relationship_id": "owns", "subject_mention_id": "person", "predicate": "OWNS",
                "object_mention_id": "company", "relationship_kind": "explicit",
                "source_block_ids": ["block-b"], "confidence": 0.85,
            }],
        }), "usage": {"total_tokens": 16}}

    monkeypatch.setattr(relationship_extraction, "chat_completion", fake_chat)
    graph, _ = relationship_extraction.extract([
        {"block_id": "block-a", "text": "Person A ownership disclosure " + "a" * 320},
        {"block_id": "block-b", "text": "Person A owns Company B " + "b" * 325},
        {"block_id": "block-c", "text": "Company B registry details " + "c" * 320},
    ], chunk_characters=1000, chunk_overlap=400)

    assert calls == [["block-a", "block-b"], ["block-b", "block-c"]]
    assert len(graph["entities"]) == 2
    assert all(len(entity["mention_ids"]) == 2 for entity in graph["entities"])
    assert len(graph["relationships"]) == 1
    assert graph["relationships"][0]["source_block_ids"] == ["block-b"]


def test_truncated_provider_completion_gets_one_targeted_repair(monkeypatch):
    _configure_client(monkeypatch)
    monkeypatch.setattr(relationship_extraction, "settings", replace(settings, llm_api_key=TEST_TOKEN, llm_model=MODEL, llm_base_url=BASE_URL))
    captured = []

    def truncated_then_complete(messages, **_kwargs):
        captured.append(messages)
        response = _relationship_response("Synthetic Person", "block-a")
        if len(captured) == 1:
            response["finish_reason"] = "length"
        return response

    monkeypatch.setattr(relationship_extraction, "chat_completion", truncated_then_complete)
    graph, metadata = relationship_extraction.extract([{"block_id": "block-a", "text": "Synthetic Person"}])

    assert graph["entities"]
    assert metadata["attempts_this_run"] == 2
    assert "truncated at the completion limit" in captured[1][-1]["content"]
