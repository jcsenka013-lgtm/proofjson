"""JSON Schema loading, coercion, and validation."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from jsonschema.validators import validator_for
from pydantic import BaseModel

from proofjson.errors import ProofJsonError

RESERVED_KEYS = frozenset({"_evidence", "_meta", "_review"})


def load_schema(schema: Mapping[str, Any] | type | str | Path) -> dict[str, Any]:
    """Accept a JSON Schema dict, a path to one, or a Pydantic model class."""
    if isinstance(schema, BaseModel):
        raise ProofJsonError("Pass the Pydantic model class, not an instance.")
    if isinstance(schema, type) and issubclass(schema, BaseModel):
        loaded = schema.model_json_schema()
    elif isinstance(schema, (str, Path)):
        path = Path(schema)
        if not path.is_file():
            raise ProofJsonError(f"Schema file not found: {path}")
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ProofJsonError(f"Schema file is not JSON: {path}") from exc
        if not isinstance(loaded, dict):
            raise ProofJsonError("Schema JSON must be an object.")
    elif isinstance(schema, Mapping):
        loaded = dict(schema)
    else:
        raise ProofJsonError(
            "schema must be a JSON Schema dict, a path to one, or a Pydantic model class."
        )
    ensure_object_schema(loaded)
    check_schema_document(loaded)
    return loaded


def resolve_ref(schema: dict[str, Any], root: dict[str, Any]) -> dict[str, Any]:
    seen: set[str] = set()
    current = schema
    while isinstance(current, dict) and "$ref" in current:
        ref = current["$ref"]
        if not isinstance(ref, str) or ref in seen or not ref.startswith("#/"):
            break
        seen.add(ref)
        node: Any = root
        for part in ref[2:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            if not isinstance(node, dict) or part not in node:
                return current
            node = node[part]
        if not isinstance(node, dict):
            break
        current = node
    return current


def ensure_object_schema(schema: dict[str, Any]) -> None:
    root = resolve_ref(schema, schema)
    kind = root.get("type")
    if isinstance(kind, list):
        non_null = [item for item in kind if item != "null"]
        kind = non_null[0] if len(non_null) == 1 else kind
    if kind not in (None, "object"):
        raise ProofJsonError("The top-level schema must describe a JSON object.")
    if kind is None and "properties" not in root:
        raise ProofJsonError("The top-level schema must describe a JSON object.")
    properties = root.get("properties", {})
    if isinstance(properties, dict):
        collision = sorted(RESERVED_KEYS & set(properties))
        if collision:
            raise ProofJsonError(
                "Schema properties collide with proofjson output keys: " + ", ".join(collision)
            )


def check_schema_document(schema: dict[str, Any]) -> None:
    validator = validator_for(schema)
    try:
        validator.check_schema(schema)
    except SchemaError as exc:
        raise ProofJsonError(f"JSON Schema is not usable: {exc.message}") from exc


def schema_at(schema: dict[str, Any], tokens: list[str | int]) -> dict[str, Any]:
    """Return the subschema at a field path, or an empty schema if it cannot be resolved."""
    root = schema
    current: dict[str, Any] = schema
    for token in tokens:
        current = resolve_ref(current, root)
        if isinstance(token, int):
            items = current.get("items", {})
            if not isinstance(items, dict):
                return {}
            current = items
            continue
        properties = current.get("properties", {})
        if not isinstance(properties, dict) or token not in properties:
            return {}
        child = properties[token]
        if not isinstance(child, dict):
            return {}
        current = child
    resolved = resolve_ref(current, root)
    return resolved if isinstance(resolved, dict) else {}


def _kind(schema: dict[str, Any]) -> str | None:
    kind = schema.get("type")
    if isinstance(kind, list):
        non_null = [item for item in kind if item != "null"]
        if len(non_null) == 1:
            return str(non_null[0])
        return None
    if isinstance(kind, str):
        return kind
    if "properties" in schema:
        return "object"
    if "items" in schema:
        return "array"
    return None


def coerce_value(value: Any, schema: dict[str, Any] | None, root: dict[str, Any]) -> Any:
    """Coerce obvious model slips (``\"2\"`` to ``2``) without guessing past the schema."""
    if not schema:
        return value
    schema = resolve_ref(schema, root)
    if value is None:
        return None
    options = schema.get("anyOf") or schema.get("oneOf")
    if isinstance(options, list) and "type" not in schema:
        for option in options:
            if not isinstance(option, dict):
                continue
            resolved = resolve_ref(option, root)
            if resolved.get("type") == "null":
                continue
            return coerce_value(value, resolved, root)
        return value

    kind = _kind(schema)
    if kind == "integer":
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str):
            text = value.strip().replace(",", "")
            if text.startswith("+"):
                text = text[1:]
            if text.isdigit() or (text.startswith("-") and text[1:].isdigit()):
                return int(text)
        return value
    if kind == "number":
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return float(value) if isinstance(value, float) else value
        if isinstance(value, str):
            text = value.strip().replace(",", "").replace("$", "")
            try:
                number = float(text)
            except ValueError:
                return value
            if number.is_integer() and "." not in text and "e" not in text.lower():
                return int(number)
            return number
        return value
    if kind == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().casefold()
            if lowered in {"true", "yes"}:
                return True
            if lowered in {"false", "no"}:
                return False
        return value
    if kind == "string":
        if isinstance(value, str):
            return value
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return str(value)
        return value
    if kind == "object" and isinstance(value, dict):
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            return value
        coerced: dict[str, Any] = {}
        for key, item in value.items():
            child = properties.get(key)
            if isinstance(child, dict):
                coerced[key] = coerce_value(item, child, root)
            else:
                coerced[key] = item
        return coerced
    if kind == "array" and isinstance(value, list):
        item_schema = schema.get("items", {})
        if not isinstance(item_schema, dict):
            return value
        return [coerce_value(item, item_schema, root) for item in value]
    return value


def order_like_schema(value: Any, schema: dict[str, Any], root: dict[str, Any]) -> Any:
    """Stable key order following the schema, then any extras."""
    schema = resolve_ref(schema or {}, root)
    kind = _kind(schema)
    if kind == "object" and isinstance(value, dict):
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            return value
        ordered: dict[str, Any] = {}
        for key in properties:
            if key in value:
                child = properties[key] if isinstance(properties[key], dict) else {}
                ordered[key] = order_like_schema(value[key], child, root)
        for key, item in value.items():
            if key not in ordered:
                ordered[key] = item
        return ordered
    if kind == "array" and isinstance(value, list):
        item_schema = schema.get("items", {})
        if not isinstance(item_schema, dict):
            return value
        return [order_like_schema(item, item_schema, root) for item in value]
    return value


def format_error_path(path: list[Any]) -> str:
    parts: list[str] = []
    for item in path:
        if isinstance(item, int):
            if not parts:
                parts.append(f"[{item}]")
            else:
                parts[-1] = f"{parts[-1]}[{item}]"
        else:
            parts.append(str(item))
    return ".".join(parts) if parts else "(root)"


def validation_messages(schema: dict[str, Any], instance: Any) -> list[str]:
    validator = validator_for(schema)
    try:
        validator.check_schema(schema)
    except SchemaError:
        validator = Draft202012Validator
    errors = sorted(validator(schema).iter_errors(instance), key=lambda err: list(err.absolute_path))
    messages: list[str] = []
    for error in errors:
        where = format_error_path(list(error.absolute_path))
        messages.append(f"{where}: {error.message}")
    return messages
