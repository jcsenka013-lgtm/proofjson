import json

import pytest

from proofjson.errors import ProofJsonError
from proofjson.providers import (
    ENVELOPE_SCHEMA,
    AnthropicProvider,
    GeminiProvider,
    OllamaProvider,
    OpenAIProvider,
    parse_json_object,
    split_model,
    strictify,
    to_gemini_schema,
)


def test_split_model() -> None:
    assert split_model("mock")[0].value == "mock"
    assert split_model("ollama/llama3.2:latest")[1] == "llama3.2:latest"
    with pytest.raises(ProofJsonError, match="provider/name"):
        split_model("gpt-4o-mini")
    with pytest.raises(ProofJsonError, match="Unknown provider"):
        split_model("nope/model")


def test_parse_json_object_strips_fences() -> None:
    assert parse_json_object('```json\n{"fields": []}\n```') == {"fields": []}


def test_schema_conversions() -> None:
    strict = strictify(ENVELOPE_SCHEMA)
    assert strict["additionalProperties"] is False
    gemini = to_gemini_schema(ENVELOPE_SCHEMA)
    assert gemini["type"] == "OBJECT"
    assert gemini["properties"]["fields"]["type"] == "ARRAY"
    assert "additionalProperties" not in gemini


def test_gemini_requires_an_environment_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with pytest.raises(ProofJsonError, match="GEMINI_API_KEY"):
        GeminiProvider("gemini-2.5-flash").complete(
            prompt="page",
            response_schema=ENVELOPE_SCHEMA,
            page_number=1,
            mode="extract",
        )


def test_openai_uses_structured_output_and_keeps_the_key_out_of_the_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    captured: dict[str, object] = {}

    def fake(url: str, headers: dict[str, str], payload: dict[str, object], label: str) -> dict[str, object]:
        captured["url"] = url
        captured["headers"] = headers
        captured["payload"] = payload
        captured["label"] = label
        return {"choices": [{"message": {"content": '{"fields": []}'}}]}

    monkeypatch.setattr("proofjson.providers._post_json", fake)
    result = OpenAIProvider("gpt-4o-mini").complete(
        prompt="page",
        response_schema=ENVELOPE_SCHEMA,
        page_number=1,
        mode="extract",
    )
    assert result == {"fields": []}
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["response_format"]["type"] == "json_schema"  # type: ignore[index]
    assert payload["temperature"] == 0.1
    assert "sk-test" not in json.dumps(payload)
    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert headers["authorization"] == "Bearer sk-test"


def test_anthropic_forces_the_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
    captured: dict[str, object] = {}

    def fake(url: str, headers: dict[str, str], payload: dict[str, object], label: str) -> dict[str, object]:
        captured["payload"] = payload
        return {
            "content": [
                {"type": "tool_use", "name": "emit_extraction", "input": {"fields": []}},
            ]
        }

    monkeypatch.setattr("proofjson.providers._post_json", fake)
    result = AnthropicProvider("claude-sonnet-4-5").complete(
        prompt="page",
        response_schema=ENVELOPE_SCHEMA,
        page_number=1,
        mode="extract",
    )
    assert result == {"fields": []}
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["tool_choice"] == {"type": "tool", "name": "emit_extraction"}
    assert "sk-ant" not in json.dumps(payload)


def test_ollama_posts_to_the_configured_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:11434/")
    captured: dict[str, object] = {}

    def fake(url: str, headers: dict[str, str], payload: dict[str, object], label: str) -> dict[str, object]:
        captured["url"] = url
        captured["payload"] = payload
        return {"message": {"content": '{"fields": []}'}}

    monkeypatch.setattr("proofjson.providers._post_json", fake)
    OllamaProvider("llama3.2").complete(
        prompt="page",
        response_schema=ENVELOPE_SCHEMA,
        page_number=1,
        mode="extract",
    )
    assert captured["url"] == "http://127.0.0.1:11434/api/chat"
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["format"]["type"] == "object"
    assert payload["options"] == {"temperature": 0.1}
