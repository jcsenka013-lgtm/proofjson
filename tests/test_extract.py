import json
from pathlib import Path

import pytest
from pypdf import PdfWriter

from proofjson.errors import ProofJsonError
from proofjson.extract import extract, extract_document
from proofjson.pdf import Page, read_pdf
from proofjson.providers import MockProvider
from tests.fakes import ScriptedProvider

INVOICE = Path(__file__).resolve().parents[1] / "examples" / "invoice"

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["invoice_number"],
    "properties": {"invoice_number": {"type": "string"}, "total": {"type": "number"}},
}


def _field(path: str, value: object, quote: str, page: int, confidence: float = 1) -> dict[str, object]:
    return {"path": path, "value": value, "quote": quote, "page": page, "confidence": confidence}


def test_repair_drops_unknown_field_and_is_called_once() -> None:
    pages = [Page(1, "Invoice number: INV-1042")]
    provider = ScriptedProvider(
        [
            {
                "fields": [
                    _field("invoice_number", "INV-1042", "Invoice number: INV-1042", 1),
                    _field("nickname", "Ada", "Invoice number: INV-1042", 1),
                ]
            },
            {"fields": [_field("invoice_number", "INV-1042", "Invoice number: INV-1042", 1)]},
        ]
    )
    result = extract_document(pages, SCHEMA, provider=provider, model="scripted", source="memory")
    assert result.data == {"invoice_number": "INV-1042"}
    assert result.validation_errors == []
    assert [call["mode"] for call in provider.calls] == ["extract", "repair"]
    assert "nickname" in provider.calls[1]["prompt"]
    assert result.evidence["invoice_number"]["verified"] is True


def test_valid_extraction_does_not_repair() -> None:
    pages = [Page(1, "Invoice number: INV-1042")]
    provider = ScriptedProvider(
        [{"fields": [_field("invoice_number", "INV-1042", "Invoice number: INV-1042", 1)]}]
    )
    result = extract_document(pages, SCHEMA, provider=provider, model="scripted", source="memory")
    assert result.validation_errors == []
    assert len(provider.calls) == 1


def test_second_page_prompt_carries_the_previous_tail() -> None:
    pages = [
        Page(1, "Header line\n\nUnique tail paragraph alpha."),
        Page(2, "Total due: 5.00"),
    ]
    provider = ScriptedProvider(
        [
            {"fields": []},
            {"fields": [_field("total", 5, "Total due: 5.00", 2)]},
        ]
    )
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"total": {"type": "number"}},
    }
    extract_document(pages, schema, provider=provider, model="scripted", source="memory")
    first = _section(provider.calls[0]["prompt"], "PREVIOUS PAGE CONTEXT:", "END PREVIOUS PAGE CONTEXT")
    second = _section(provider.calls[1]["prompt"], "PREVIOUS PAGE CONTEXT:", "END PREVIOUS PAGE CONTEXT")
    assert first == "(none)"
    assert "Unique tail paragraph alpha." in second
    current = _section(provider.calls[1]["prompt"], "CURRENT PAGE TEXT:", "END CURRENT PAGE TEXT")
    assert "Total due: 5.00" in current
    assert "value_json" in json.dumps(provider.calls[0]["response_schema"])


def test_higher_confidence_wins_and_ties_go_to_the_later_page() -> None:
    pages = [Page(1, "Name: Ada"), Page(2, "Name: Ada Example")]
    provider = ScriptedProvider(
        [
            {"fields": [_field("name", "Ada", "Name: Ada", 1, 0.95)]},
            {"fields": [_field("name", "Ada Example", "Name: Ada Example", 2, 0.4)]},
        ]
    )
    schema = {"type": "object", "additionalProperties": False, "properties": {"name": {"type": "string"}}}
    kept = extract_document(pages, schema, provider=provider, model="scripted", source="memory")
    assert kept.data["name"] == "Ada"

    provider = ScriptedProvider(
        [
            {"fields": [_field("name", "Ada", "Name: Ada", 1, 0.5)]},
            {"fields": [_field("name", "Ada Example", "Name: Ada Example", 2, 0.5)]},
        ]
    )
    replaced = extract_document(pages, schema, provider=provider, model="scripted", source="memory")
    assert replaced.data["name"] == "Ada Example"


def test_quote_on_the_wrong_page_is_unverified() -> None:
    pages = [Page(1, "Nothing here"), Page(2, "Invoice number: INV-1042")]
    provider = ScriptedProvider(
        [
            {"fields": []},
            {"fields": [_field("invoice_number", "INV-1042", "Invoice number: INV-1042", 1)]},
        ]
    )
    result = extract_document(pages, SCHEMA, provider=provider, model="scripted", source="memory")
    assert result.evidence["invoice_number"]["verified"] is False
    assert result.evidence["invoice_number"]["page"] == 1
    assert result.needs_review is True


def test_low_confidence_needs_review_even_when_the_quote_matches() -> None:
    pages = [Page(1, "Invoice number: INV-1042")]
    provider = ScriptedProvider(
        [{"fields": [_field("invoice_number", "INV-1042", "Invoice number: INV-1042", 1, 0.5)]}]
    )
    result = extract_document(
        pages,
        SCHEMA,
        provider=provider,
        model="scripted",
        source="memory",
        min_confidence=0.8,
    )
    assert result.evidence["invoice_number"]["verified"] is True
    assert result.needs_review is True


def test_value_json_is_decoded() -> None:
    pages = [Page(1, "Invoice number: INV-1042")]
    provider = ScriptedProvider(
        [
            {
                "fields": [
                    {
                        "path": "invoice_number",
                        "value_json": '"INV-1042"',
                        "quote": "Invoice number: INV-1042",
                        "page": 1,
                        "confidence": 1,
                    }
                ]
            }
        ]
    )
    result = extract_document(pages, SCHEMA, provider=provider, model="scripted", source="memory")
    assert result.data["invoice_number"] == "INV-1042"


def test_blank_pdf_explains_ocr(tmp_path: Path) -> None:
    path = tmp_path / "blank.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    with path.open("wb") as handle:
        writer.write(handle)
    with pytest.raises(ProofJsonError, match="OCR"):
        read_pdf(path)


def test_encrypted_pdf(tmp_path: Path) -> None:
    path = tmp_path / "secret.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.encrypt("secret")
    with path.open("wb") as handle:
        writer.write(handle)
    with pytest.raises(ProofJsonError, match="Encrypted"):
        read_pdf(path)


def test_invoice_mock_matches_expected_and_carries_context() -> None:
    schema = json.loads((INVOICE / "schema.json").read_text(encoding="utf-8"))
    expected = json.loads((INVOICE / "expected.json").read_text(encoding="utf-8"))
    pages = read_pdf(INVOICE / "invoice.pdf")
    inner = MockProvider(INVOICE / "cassette.json")

    class Recording:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def complete(self, **kwargs: object) -> dict[str, object]:
            self.calls.append(kwargs)
            return inner.complete(**kwargs)  # type: ignore[arg-type]

    recording = Recording()
    result = extract_document(
        pages,
        schema,
        provider=recording,  # type: ignore[arg-type]
        model="mock",
        source=str((INVOICE / "invoice.pdf").resolve()),
    )
    assert result.data == expected
    assert result.validation_errors == []
    assert result.evidence["notes"]["confidence"] == 0.75
    assert result.evidence["notes"]["verified"] is True
    assert result.evidence["total"]["page"] == 2
    assert result.needs_review is True
    assert len(recording.calls) == 2
    second = str(recording.calls[1]["prompt"])
    assert "PREVIOUS PAGE CONTEXT:\n(none)" not in second
    assert "Steel water bottle" in second
    document = result.document
    assert set(document) >= set(expected)
    assert "_evidence" in document and "_meta" in document


def test_extract_helper_reads_the_pdf() -> None:
    result = extract(INVOICE / "invoice.pdf", INVOICE / "schema.json", model="mock")
    assert result.data["invoice_number"] == "INV-1042"
    assert result.model == "mock"


def _section(prompt: str, start: str, end: str) -> str:
    begin = prompt.index(start) + len(start)
    finish = prompt.index(end, begin)
    return prompt[begin:finish].strip()
