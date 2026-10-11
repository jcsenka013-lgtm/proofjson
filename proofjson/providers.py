"""Model providers behind one ``complete`` method.

API keys are read from the environment only. Nothing in this module accepts a key
as an argument, and keys are not written into request bodies.
"""

from __future__ import annotations

import json
import os
import re
import sys
from enum import Enum
from pathlib import Path
from typing import Any, Literal, NoReturn, Protocol
from urllib.parse import quote

import httpx

from proofjson.errors import ProofJsonError
from proofjson.version import __version__

if sys.version_info >= (3, 11):
    from typing import Never
else:
    from typing_extensions import Never

TEMPERATURE = 0.1
SYSTEM_PROMPT = (
    "You extract fields from a document page into the requested JSON schema. "
    "Every value needs a short quote copied from the current page, the page number, "
    "and a confidence from 0 to 1. Do not invent quotes."
)

Mode = Literal["extract", "repair"]

ENVELOPE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["fields"],
    "properties": {
        "fields": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["path", "value_json", "quote", "page", "confidence"],
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Dotted path, with bracket indexes for arrays.",
                    },
                    "value_json": {
                        "type": "string",
                        "description": "JSON literal for the value, such as \"\\\"INV-1\\\"\" or \"12.5\".",
                    },
                    "quote": {
                        "type": "string",
                        "description": "Verbatim span from the current page that supports the value.",
                    },
                    "page": {"type": "integer", "description": "1-based page number."},
                    "confidence": {"type": "number", "description": "Confidence from 0 to 1."},
                },
            },
        }
    },
}

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_GEMINI_TYPES = {
    "object": "OBJECT",
    "array": "ARRAY",
    "string": "STRING",
    "number": "NUMBER",
    "integer": "INTEGER",
    "boolean": "BOOLEAN",
}


class ProviderName(str, Enum):
    GEMINI = "gemini"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    OLLAMA = "ollama"
    MOCK = "mock"


class ModelProvider(Protocol):
    """One round trip that returns the extraction envelope."""

    def complete(
        self,
        *,
        prompt: str,
        response_schema: dict[str, Any],
        page_number: int,
        mode: Mode,
    ) -> dict[str, Any]:
        """Return an object with a ``fields`` list."""


def _assert_never(value: Never) -> NoReturn:
    raise ProofJsonError(f"Unhandled provider: {value!r}")


def split_model(model: str) -> tuple[ProviderName, str]:
    text = model.strip()
    if text == "mock":
        return ProviderName.MOCK, "mock"
    if "/" not in text:
        raise ProofJsonError(
            "Model must look like provider/name, for example gemini/gemini-2.5-flash, "
            "or be the literal mock."
        )
    provider, name = text.split("/", 1)
    if not name:
        raise ProofJsonError(f"Model name is missing in {model!r}.")
    try:
        kind = ProviderName(provider)
    except ValueError as exc:
        known = ", ".join(item.value for item in ProviderName)
        raise ProofJsonError(f"Unknown provider {provider!r}. Expected one of: {known}.") from exc
    return kind, name


def require_env(*names: str) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    joined = " or ".join(names)
    raise ProofJsonError(
        f"Set {joined} in the environment. proofjson does not accept API keys as flags or files."
    )


def parse_json_object(text: str) -> dict[str, Any]:
    raw = text.strip()
    if raw.startswith("```"):
        raw = _FENCE.sub("", raw).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProofJsonError(f"Model returned invalid JSON: {exc.msg}") from exc
    if not isinstance(data, dict):
        raise ProofJsonError("Model returned JSON that is not an object.")
    return data


def strictify(schema: dict[str, Any]) -> dict[str, Any]:
    """OpenAI strict json_schema: every object lists all properties as required."""

    def walk(node: Any) -> Any:
        if isinstance(node, list):
            return [walk(item) for item in node]
        if not isinstance(node, dict):
            return node
        copied = {key: walk(value) for key, value in node.items() if key != "additionalProperties"}
        if copied.get("type") == "object" and isinstance(copied.get("properties"), dict):
            copied["additionalProperties"] = False
            copied["required"] = list(copied["properties"].keys())
        return copied

    walked = walk(schema)
    if not isinstance(walked, dict):
        raise ProofJsonError("Response schema must be an object.")
    return walked


def to_gemini_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Gemini responseSchema uses uppercase type names and ignores extra JSON Schema keys."""

    def walk(node: Any) -> Any:
        if isinstance(node, list):
            return [walk(item) for item in node]
        if not isinstance(node, dict):
            return node
        copied: dict[str, Any] = {}
        for key, value in node.items():
            if key in {"additionalProperties", "$schema", "$id", "title"}:
                continue
            copied[key] = walk(value)
        kind = copied.get("type")
        if isinstance(kind, str) and kind in _GEMINI_TYPES:
            copied["type"] = _GEMINI_TYPES[kind]
        return copied

    walked = walk(schema)
    if not isinstance(walked, dict):
        raise ProofJsonError("Response schema must be an object.")
    return walked


def _post_json(url: str, headers: dict[str, str], payload: dict[str, Any], label: str) -> dict[str, Any]:
    request_headers = {"user-agent": f"proofjson/{__version__}", **headers}
    try:
        with httpx.Client(timeout=120.0) as client:
            response = client.post(url, headers=request_headers, json=payload)
    except httpx.HTTPError as exc:
        raise ProofJsonError(
            f"{label} request failed ({exc.__class__.__name__}). Check the host and your network."
        ) from exc
    if response.status_code >= 400:
        body = response.text.strip().replace("\n", " ")
        if len(body) > 400:
            body = body[:400] + "..."
        raise ProofJsonError(f"{label} returned HTTP {response.status_code}: {body}")
    try:
        data = response.json()
    except json.JSONDecodeError as exc:
        raise ProofJsonError(f"{label} returned a response that is not JSON.") from exc
    if not isinstance(data, dict):
        raise ProofJsonError(f"{label} returned JSON that is not an object.")
    return data


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        if parts:
            return "\n".join(parts)
    raise ProofJsonError("Model response did not include text.")


def parse_openai_body(data: dict[str, Any]) -> dict[str, Any]:
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ProofJsonError("OpenAI response did not include a message.") from exc
    return parse_json_object(_message_text(content))


def parse_gemini_body(data: dict[str, Any]) -> dict[str, Any]:
    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ProofJsonError("Gemini response did not include a candidate.") from exc
    if not isinstance(text, str):
        raise ProofJsonError("Gemini candidate text was not a string.")
    return parse_json_object(text)


def parse_anthropic_body(data: dict[str, Any]) -> dict[str, Any]:
    content = data.get("content")
    if not isinstance(content, list):
        raise ProofJsonError("Anthropic response did not include content blocks.")
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        tool_input = block.get("input")
        if isinstance(tool_input, str):
            return parse_json_object(tool_input)
        if isinstance(tool_input, dict):
            return tool_input
    raise ProofJsonError("Anthropic response did not include the emit_extraction tool call.")


def parse_ollama_body(data: dict[str, Any]) -> dict[str, Any]:
    message = data.get("message")
    if not isinstance(message, dict):
        raise ProofJsonError("Ollama response did not include a message.")
    content = message.get("content")
    if not isinstance(content, str):
        raise ProofJsonError("Ollama response content was not text.")
    return parse_json_object(content)


class GeminiProvider:
    def __init__(self, model: str) -> None:
        self.model = model

    def complete(
        self,
        *,
        prompt: str,
        response_schema: dict[str, Any],
        page_number: int,
        mode: Mode,
    ) -> dict[str, Any]:
        del page_number, mode
        key = require_env("GEMINI_API_KEY", "GOOGLE_API_KEY")
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{quote(self.model, safe='')}:generateContent"
        )
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": TEMPERATURE,
                "maxOutputTokens": 8192,
                "responseMimeType": "application/json",
                "responseSchema": to_gemini_schema(response_schema),
            },
        }
        data = _post_json(url, {"x-goog-api-key": key, "content-type": "application/json"}, payload, "Gemini")
        return parse_gemini_body(data)


class OpenAIProvider:
    def __init__(self, model: str) -> None:
        self.model = model

    def complete(
        self,
        *,
        prompt: str,
        response_schema: dict[str, Any],
        page_number: int,
        mode: Mode,
    ) -> dict[str, Any]:
        del page_number, mode
        key = require_env("OPENAI_API_KEY")
        base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").strip().rstrip("/")
        payload = {
            "model": self.model,
            "temperature": TEMPERATURE,
            "max_completion_tokens": 8192,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "proofjson_extraction",
                    "strict": True,
                    "schema": strictify(response_schema),
                },
            },
        }
        data = _post_json(
            f"{base}/chat/completions",
            {"authorization": f"Bearer {key}", "content-type": "application/json"},
            payload,
            "OpenAI",
        )
        return parse_openai_body(data)


class AnthropicProvider:
    def __init__(self, model: str) -> None:
        self.model = model

    def complete(
        self,
        *,
        prompt: str,
        response_schema: dict[str, Any],
        page_number: int,
        mode: Mode,
    ) -> dict[str, Any]:
        del page_number, mode
        key = require_env("ANTHROPIC_API_KEY")
        payload = {
            "model": self.model,
            "max_tokens": 8192,
            "temperature": TEMPERATURE,
            "system": SYSTEM_PROMPT,
            "tools": [
                {
                    "name": "emit_extraction",
                    "description": "Return extracted fields and their evidence.",
                    "input_schema": response_schema,
                }
            ],
            "tool_choice": {"type": "tool", "name": "emit_extraction"},
            "messages": [{"role": "user", "content": prompt}],
        }
        data = _post_json(
            "https://api.anthropic.com/v1/messages",
            {
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            payload,
            "Anthropic",
        )
        return parse_anthropic_body(data)


class OllamaProvider:
    def __init__(self, model: str) -> None:
        self.model = model

    def complete(
        self,
        *,
        prompt: str,
        response_schema: dict[str, Any],
        page_number: int,
        mode: Mode,
    ) -> dict[str, Any]:
        del page_number, mode
        host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").strip().rstrip("/")
        payload = {
            "model": self.model,
            "stream": False,
            "format": response_schema,
            "options": {"temperature": TEMPERATURE},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        }
        data = _post_json(f"{host}/api/chat", {"content-type": "application/json"}, payload, "Ollama")
        return parse_ollama_body(data)


class MockProvider:
    """Replay a cassette keyed by page number. Used for tests and the local quickstart."""

    def __init__(self, cassette_path: str | Path) -> None:
        path = Path(cassette_path)
        if not path.is_file():
            raise ProofJsonError(
                f"Mock cassette not found: {path}. Place cassette.json next to the PDF "
                "or pass --cassette."
            )
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ProofJsonError(f"Cassette is not JSON: {path}") from exc
        if not isinstance(loaded, dict) or not isinstance(loaded.get("pages"), dict):
            raise ProofJsonError("Cassette must be an object with a pages map.")
        self.cassette = loaded
        self._repaired = False

    def complete(
        self,
        *,
        prompt: str,
        response_schema: dict[str, Any],
        page_number: int,
        mode: Mode,
    ) -> dict[str, Any]:
        del prompt, response_schema
        if mode == "repair":
            if self._repaired:
                raise ProofJsonError("Repair was attempted more than once.")
            self._repaired = True
            if "repair" in self.cassette:
                fields = self.cassette["repair"]
            else:
                fields = []
                for key in sorted(self.cassette["pages"], key=lambda item: int(item)):
                    page_fields = self.cassette["pages"][key]
                    if isinstance(page_fields, list):
                        fields.extend(page_fields)
            if not isinstance(fields, list):
                raise ProofJsonError("Cassette repair entry must be a list of fields.")
            return {"fields": fields}
        if mode != "extract":
            raise ProofJsonError(f"Unknown provider mode: {mode}")
        page_fields = self.cassette["pages"].get(str(page_number), [])
        if not isinstance(page_fields, list):
            raise ProofJsonError(f"Cassette page {page_number} must be a list.")
        return {"fields": page_fields}


def build_provider(model: str, cassette: str | Path | None = None) -> ModelProvider:
    kind, name = split_model(model)
    match kind:
        case ProviderName.GEMINI:
            return GeminiProvider(name)
        case ProviderName.OPENAI:
            return OpenAIProvider(name)
        case ProviderName.ANTHROPIC:
            return AnthropicProvider(name)
        case ProviderName.OLLAMA:
            return OllamaProvider(name)
        case ProviderName.MOCK:
            if cassette is None:
                raise ProofJsonError("The mock provider needs a cassette.json path.")
            return MockProvider(cassette)
        case _:
            _assert_never(kind)
