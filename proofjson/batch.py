"""Extract every PDF in a folder and gate the batch on confidence."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from proofjson.errors import ProofJsonError
from proofjson.extract import ExtractionResult, extract

if sys.version_info >= (3, 11):
    from typing import Never
else:
    from typing_extensions import Never


class BatchStatus(str, Enum):
    OK = "ok"
    REVIEW = "review"
    ERROR = "error"


@dataclass(slots=True)
class BatchItem:
    pdf: Path
    output: Path | None
    status: BatchStatus
    fields: int | None
    needs_review: bool
    detail: str


@dataclass(slots=True)
class BatchReport:
    directory: Path
    min_confidence: float
    items: list[BatchItem]

    @property
    def exit_code(self) -> int:
        statuses = {item.status for item in self.items}
        if BatchStatus.ERROR in statuses:
            return 2
        if BatchStatus.REVIEW in statuses:
            return 1
        return 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "directory": str(self.directory),
            "min_confidence": self.min_confidence,
            "passed": self.exit_code == 0,
            "files": [
                {
                    "pdf": str(item.pdf),
                    "output": None if item.output is None else str(item.output),
                    "status": item.status.value,
                    "fields": item.fields,
                    "needs_review": item.needs_review,
                    "detail": item.detail,
                }
                for item in self.items
            ],
        }


def run_batch(
    directory: str | Path,
    schema: dict[str, Any] | type | str | Path,
    *,
    model: str,
    min_confidence: float = 0.8,
    output_dir: str | Path | None = None,
    cassette: str | Path | None = None,
    pack: str | None = None,
) -> BatchReport:
    """Extract each PDF in ``directory`` with the same schema.

    A file is ``ok`` only when the schema validates, every quote verifies, and
    every confidence is at least ``min_confidence``. Files that miss that gate
    are still written, with status ``review``. A PDF that cannot be read is
    ``error`` and is not written; the rest of the folder still runs.
    """
    folder = Path(directory)
    if not folder.is_dir():
        raise ProofJsonError(f"Not a directory: {folder}")
    pdfs = sorted(path for path in folder.iterdir() if path.is_file() and path.suffix.lower() == ".pdf")
    if not pdfs:
        raise ProofJsonError(f"No PDFs in {folder}")
    destination = Path(output_dir) if output_dir is not None else None
    if destination is not None:
        if destination.exists() and not destination.is_dir():
            raise ProofJsonError(f"Output directory is a file: {destination}")
        destination.mkdir(parents=True, exist_ok=True)

    items: list[BatchItem] = []
    for pdf in pdfs:
        target = (destination or pdf.parent) / f"{pdf.stem}.proof.json"
        try:
            result = extract(
                pdf,
                schema,
                model=model,
                min_confidence=min_confidence,
                cassette=cassette,
            )
        except ProofJsonError as exc:
            items.append(
                BatchItem(
                    pdf=pdf,
                    output=None,
                    status=BatchStatus.ERROR,
                    fields=None,
                    needs_review=False,
                    detail=str(exc),
                )
            )
            continue
        _stamp_pack(result, pack)
        target.write_text(json.dumps(result.document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        status = BatchStatus.REVIEW if result.needs_review else BatchStatus.OK
        items.append(
            BatchItem(
                pdf=pdf,
                output=target,
                status=status,
                fields=len(result.evidence),
                needs_review=result.needs_review,
                detail=_detail(result),
            )
        )
    return BatchReport(directory=folder, min_confidence=min_confidence, items=items)


def render_batch(report: BatchReport) -> str:
    """One status line per PDF, then a count of what passed the gate."""
    lines = [f"{'status':<6}  {'fields':>6}  file"]
    for item in report.items:
        fields = "-" if item.fields is None else str(item.fields)
        detail = f"  {item.detail}" if item.detail else ""
        lines.append(f"{_status_label(item.status):<6}  {fields:>6}  {item.pdf.name}{detail}")
    passed = sum(1 for item in report.items if item.status is BatchStatus.OK)
    review = sum(1 for item in report.items if item.status is BatchStatus.REVIEW)
    failed = sum(1 for item in report.items if item.status is BatchStatus.ERROR)
    file_word = "file" if len(report.items) == 1 else "files"
    review_word = "needs review" if review == 1 else "need review"
    lines.append(
        f"{len(report.items)} {file_word}, {passed} passed, {review} {review_word}, {failed} failed"
    )
    return "\n".join(lines) + "\n"


def _stamp_pack(result: ExtractionResult, pack: str | None) -> None:
    if pack is None:
        return
    meta = result.document.get("_meta")
    if isinstance(meta, dict):
        meta["pack"] = pack


def _detail(result: ExtractionResult) -> str:
    parts: list[str] = []
    low = [path for path, item in result.evidence.items() if item["confidence"] < result.min_confidence]
    unverified = [path for path, item in result.evidence.items() if not item["verified"]]
    if low:
        parts.append("below confidence: " + ", ".join(low))
    if unverified:
        parts.append("unverified: " + ", ".join(unverified))
    if result.validation_errors:
        parts.append("schema errors: " + "; ".join(result.validation_errors))
    return "; ".join(parts)


def _status_label(status: BatchStatus) -> str:
    match status:
        case BatchStatus.OK:
            return "ok"
        case BatchStatus.REVIEW:
            return "review"
        case BatchStatus.ERROR:
            return "error"
        case _:
            return _unknown_status(status)


def _unknown_status(status: Never) -> Never:
    raise ProofJsonError(f"Unknown batch status: {status}")
