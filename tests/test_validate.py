import pytest
from pydantic import BaseModel

from proofjson.errors import ProofJsonError
from proofjson.validate import coerce_value, load_schema, validation_messages


class Line(BaseModel):
    description: str
    qty: int


class Invoice(BaseModel):
    invoice_number: str
    total: float
    line_items: list[Line]


def test_load_pydantic_model_and_coerce() -> None:
    schema = load_schema(Invoice)
    raw = {
        "invoice_number": 1042,
        "total": "12.5",
        "line_items": [{"description": "Tote", "qty": "2"}],
    }
    coerced = coerce_value(raw, schema, schema)
    assert coerced["invoice_number"] == "1042"
    assert coerced["total"] == 12.5
    assert coerced["line_items"][0]["qty"] == 2
    assert validation_messages(schema, coerced) == []


def test_bool_is_not_coerced_to_integer() -> None:
    schema = {"type": "integer"}
    assert coerce_value(True, schema, schema) is True


def test_reserved_keys_rejected() -> None:
    with pytest.raises(ProofJsonError, match="_evidence"):
        load_schema({"type": "object", "properties": {"_evidence": {"type": "string"}}})


def test_top_level_array_rejected() -> None:
    with pytest.raises(ProofJsonError, match="JSON object"):
        load_schema({"type": "array", "items": {"type": "string"}})


def test_validation_message_uses_bracket_paths() -> None:
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "line_items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"qty": {"type": "integer"}},
                    "required": ["qty"],
                },
            }
        },
    }
    messages = validation_messages(schema, {"line_items": [{"qty": "nope"}]})
    assert any(message.startswith("line_items[0].qty:") for message in messages)
