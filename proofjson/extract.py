"""Page-by-page extraction with one schema-repair retry."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from proofjson.errors import ProofJsonError
from proofjson.evidence import verify_quote
from proofjson.paths import flatten, parse_path, unflatten
from proofjson.pdf import Page, last_context, read_pdf
from proofjson.providers import (
    ENVELOPE_SCHEMA,
    ModelProvider,
    ProviderName,
    build_provider,
    split_model,
)
from proofjson.validate import coerce_value, load_schema, order_like_schema, validation_messages
from proofjson.version import __version__

DEFAULT_MODEL = "gemini/gemini-2.5-flash"
MAX_PAGE_CHARS = 80_000


@dataclass(slots=True)
class Field:
    path: str
    value: Any
    quote: str
    page: int
    confidence: float


@dataclass(slots=True)
class ExtractionResult:
    """Schema-valid values, plus the parallel evidence map and the file-shaped document."""

    data: dict[str, Any]
    evidence: dict[str, dict[str, Any]]
    needs_review: bool
    validation_errors: list[str]
    document: dict[str, Any]
    min_confidence: float
    model: str


def extract(
    pdf_path: str | Path,
    schema: dict[str, Any] | type | str | Path,
    model: str = DEFAULT_MODEL,
    *,
    min_confidence: float = 0.8,
    cassette: str | Path | None = None,
    provider: ModelProvider | None = None,
) -> ExtractionResult:
    """Extract schema-valid JSON from a PDF, with evidence on every field.

    ``schema`` is a JSON Schema dict, a path to one, or a Pydantic model class.
    ``model`` is ``provider/name`` (for example ``gemini/gemini-2.5-flash``) or
    ``mock`` to replay ``cassette.json`` beside the PDF. API keys come from the
    environment. See the README for the variable names.
    """
    if not 0.0 <= min_confidence <= 1.0:
        raise ProofJsonError("min_confidence must be between 0 and 1.")
    path = Path(pdf_path)
    loaded_schema = copy.deepcopy(load_schema(schema))
    pages = read_pdf(path)
    if provider is None:
        provider = _provider_for(model, path, cassette)
    return extract_document(
        pages,
        loaded_schema,
        provider=provider,
        model=model,
        source=str(path.resolve()),
        min_confidence=min_confidence,
    )


def extract_document(
    pages: list[Page],
    schema: dict[str, Any],
    *,
    provider: ModelProvider,
    model: str,
    source: str,
    min_confidence: float = 0.8,
) -> ExtractionResult:
    """Run extraction on pages that are already text. ``schema`` must already be loaded."""
    accumulated: dict[str, Field] = {}
    carry = ""
    total = len(pages)
    for page in pages:
        prompt = build_extract_prompt(
            schema=schema,
            page=page,
            total_pages=total,
            carry=carry,
            so_far=accumulated,
        )
        payload = provider.complete(
            prompt=prompt,
            response_schema=ENVELOPE_SCHEMA,
            page_number=page.number,
            mode="extract",
        )
        merge_fields(accumulated, parse_fields(payload, default_page=page.number))
        carry = last_context(page.text)

    data, errors = materialize(accumulated, schema)
    if errors:
        prompt = build_repair_prompt(schema, pages, accumulated, errors)
        payload = provider.complete(
            prompt=prompt,
            response_schema=ENVELOPE_SCHEMA,
            page_number=0,
            mode="repair",
        )
        accumulated = {}
        merge_fields(accumulated, parse_fields(payload, default_page=0))
        data, errors = materialize(accumulated, schema)

    evidence = build_evidence(accumulated, pages, data)
    needs_review = _needs_review(evidence, errors, min_confidence)
    meta = {
        "proofjson_version": __version__,
        "model": model,
        "source": source,
        "page_count": len(pages),
        "min_confidence": min_confidence,
        "needs_review": needs_review,
        "validation_errors": errors,
        "empty_text_pages": [page.number for page in pages if not page.text.strip()],
        "schema": schema,
    }
    document = dict(data)
    document["_evidence"] = evidence
    document["_meta"] = meta
    return ExtractionResult(
        data=data,
        evidence=evidence,
        needs_review=needs_review,
        validation_errors=list(errors),
        document=document,
        min_confidence=min_confidence,
        model=model,
    )


def format_summary(result: ExtractionResult) -> str:
    verified = sum(1 for item in result.evidence.values() if item["verified"])
    unverified = [path for path, item in result.evidence.items() if not item["verified"]]
    low = [
        path
        for path, item in result.evidence.items()
        if item["confidence"] < result.min_confidence
    ]
    lines = [f"{len(result.evidence)} fields, {verified} verified, {len(unverified)} unverified"]
    if unverified:
        lines.append("unverified: " + ", ".join(unverified))
    if low:
        lines.append("below confidence: " + ", ".join(low))
    if result.validation_errors:
        lines.append("schema errors:")
        lines.extend(f"  {error}" for error in result.validation_errors)
    if result.needs_review:
        lines.append("needs review")
    return "\n".join(lines)


def parse_fields(payload: dict[str, Any], *, default_page: int) -> list[Field]:
    fields = payload.get("fields")
    if not isinstance(fields, list):
        raise ProofJsonError("Model response is missing a fields array.")
    parsed: list[Field] = []
    for item in fields:
        if not isinstance(item, dict):
            raise ProofJsonError("A field entry was not an object.")
        path = item.get("path")
        if not isinstance(path, str) or not path.strip():
            raise ProofJsonError("A field is missing its path.")
        path = path.strip()
        try:
            parse_path(path)
        except ValueError as exc:
            raise ProofJsonError(str(exc)) from exc
        value = _field_value(item, path)
        quote = item.get("quote", "")
        if quote is None:
            quote = ""
        if not isinstance(quote, str):
            quote = str(quote)
        page = _field_page(item.get("page"), default_page)
        confidence = _field_confidence(item.get("confidence", 0))
        parsed.append(Field(path=path, value=value, quote=quote, page=page, confidence=confidence))
    return parsed


def merge_fields(accumulated: dict[str, Field], fields: list[Field]) -> None:
    """Keep the higher-confidence value. Ties go to the later page."""
    for field in fields:
        current = accumulated.get(field.path)
        if current is None or field.confidence >= current.confidence:
            accumulated[field.path] = field


def materialize(fields: dict[str, Field], schema: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    flat = {path: field.value for path, field in fields.items()}
    try:
        data = unflatten(flat)
    except ValueError as exc:
        raise ProofJsonError(str(exc)) from exc
    data = coerce_value(data, schema, schema)
    if not isinstance(data, dict):
        raise ProofJsonError("Extraction did not produce an object.")
    data = order_like_schema(data, schema, schema)
    if not isinstance(data, dict):
        raise ProofJsonError("Extraction did not produce an object.")
    return data, validation_messages(schema, data)


def build_evidence(
    fields: dict[str, Field],
    pages: list[Page],
    data: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    by_number = {page.number: page.text for page in pages}
    ordered = list(flatten(data))
    for path in fields:
        if path not in ordered:
            ordered.append(path)
    evidence: dict[str, dict[str, Any]] = {}
    for path in ordered:
        field = fields.get(path)
        if field is None:
            continue
        match = verify_quote(field.quote, by_number.get(field.page, ""))
        evidence[path] = {
            "quote": field.quote,
            "page": field.page,
            "confidence": field.confidence,
            "verified": match.verified,
        }
    return evidence


def build_extract_prompt(
    *,
    schema: dict[str, Any],
    page: Page,
    total_pages: int,
    carry: str,
    so_far: dict[str, Field],
) -> str:
    if so_far:
        lines = [
            f"- {path} = {json.dumps(field.value, ensure_ascii=False)} (page {field.page})"
            for path, field in so_far.items()
        ]
        extracted = "\n".join(lines)
    else:
        extracted = "(none)"
    previous = carry if carry else "(none)"
    return (
        "Extract fields for the CURRENT PAGE only.\n"
        "Return JSON matching the response schema. Put each value in value_json as a JSON literal.\n"
        "Rules:\n"
        "- Quote only from CURRENT PAGE TEXT. Do not quote PREVIOUS PAGE CONTEXT.\n"
        "- The quote must be a short verbatim span a person can find on that page.\n"
        "- Omit fields that are not on this page.\n"
        "- Paths use dots and bracket indexes, for example vendor.name or line_items[0].amount.\n"
        "- Continue array indexes from FIELDS ALREADY EXTRACTED. Do not restart at 0 on a later page.\n"
        "- page is the current 1-based page number.\n"
        "- confidence is a number from 0 to 1.\n"
        "- Do not invent values. If the page does not say it, leave it out.\n"
        f"\nPAGE {page.number} OF {total_pages}\n"
        "\nUSER SCHEMA:\n"
        f"{json.dumps(schema, indent=2, ensure_ascii=False)}\n"
        "\nFIELDS ALREADY EXTRACTED:\n"
        f"{extracted}\n"
        "\nPREVIOUS PAGE CONTEXT:\n"
        f"{previous}\n"
        "END PREVIOUS PAGE CONTEXT\n"
        "\nCURRENT PAGE TEXT:\n"
        f"{_clip(page.text)}\n"
        "END CURRENT PAGE TEXT\n"
    )


def build_repair_prompt(
    schema: dict[str, Any],
    pages: list[Page],
    fields: dict[str, Field],
    errors: list[str],
) -> str:
    current = [
        {
            "path": path,
            "value": field.value,
            "quote": field.quote,
            "page": field.page,
            "confidence": field.confidence,
        }
        for path, field in fields.items()
    ]
    page_blocks = []
    for page in pages:
        page_blocks.append(f"--- PAGE {page.number} ---\n{_clip(page.text)}")
    shown = errors[:30]
    return (
        "The extraction failed JSON Schema validation. Return the FULL fields array, not a delta.\n"
        "Fix types, required fields, and additional properties. Keep a verbatim quote and page "
        "for every value you keep. Drop fields that are not in the schema. Do not invent quotes.\n"
        "value_json must be a JSON literal.\n"
        "\nVALIDATION ERRORS:\n"
        + "\n".join(f"- {error}" for error in shown)
        + "\n\nUSER SCHEMA:\n"
        + json.dumps(schema, indent=2, ensure_ascii=False)
        + "\n\nCURRENT FIELDS:\n"
        + json.dumps(current, indent=2, ensure_ascii=False)
        + "\n\nDOCUMENT:\n"
        + "\n\n".join(page_blocks)
        + "\n"
    )


def _provider_for(model: str, pdf_path: Path, cassette: str | Path | None) -> ModelProvider:
    kind, _name = split_model(model)
    if kind is ProviderName.MOCK and cassette is None:
        cassette = pdf_path.resolve().parent / "cassette.json"
    return build_provider(model, cassette)


def _needs_review(
    evidence: dict[str, dict[str, Any]],
    errors: list[str],
    min_confidence: float,
) -> bool:
    if errors:
        return True
    for item in evidence.values():
        if not item["verified"] or item["confidence"] < min_confidence:
            return True
    return False


def _clip(text: str) -> str:
    if len(text) <= MAX_PAGE_CHARS:
        return text
    return text[:MAX_PAGE_CHARS] + "\n[truncated]"


def _field_value(item: dict[str, Any], path: str) -> Any:
    if "value_json" in item:
        raw = item["value_json"]
        if isinstance(raw, str):
            try:
                return json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ProofJsonError(f"Field {path} has invalid value_json.") from exc
        return raw
    if "value" in item:
        return item["value"]
    raise ProofJsonError(f"Field {path} is missing a value.")


def _field_page(value: Any, default_page: int) -> int:
    if isinstance(value, bool) or value is None:
        return default_page
    if isinstance(value, int):
        return value if value >= 1 else default_page
    if isinstance(value, float) and value.is_integer():
        page = int(value)
        return page if page >= 1 else default_page
    if isinstance(value, str) and value.strip().isdigit():
        page = int(value.strip())
        return page if page >= 1 else default_page
    return default_page


def _field_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    if confidence < 0:
        return 0.0
    if confidence > 1:
        return 1.0
    return confidence
