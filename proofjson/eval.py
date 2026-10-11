"""Score fixture directories and render a markdown report."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from proofjson.errors import ProofJsonError
from proofjson.extract import extract
from proofjson.paths import flatten


@dataclass(slots=True)
class FixtureScore:
    name: str
    model: str
    hits: dict[str, bool]
    verified: int
    evidence_count: int

    @property
    def correct(self) -> int:
        return sum(1 for hit in self.hits.values() if hit)

    @property
    def field_count(self) -> int:
        return len(self.hits)


@dataclass(slots=True)
class EvalReport:
    directory: str
    models: list[str]
    scores: list[FixtureScore] = field(default_factory=list)


def discover_fixtures(directory: str | Path) -> list[Path]:
    root = Path(directory)
    if not root.is_dir():
        raise ProofJsonError(f"Fixture directory not found: {root}")
    if _is_fixture(root):
        return [root]
    found = [path for path in sorted(root.iterdir()) if path.is_dir() and _is_fixture(path)]
    if not found:
        raise ProofJsonError(
            f"No fixtures in {root}. Each fixture directory needs schema.json, expected.json, and one PDF."
        )
    return found


def evaluate(directory: str | Path, models: list[str] | None = None) -> EvalReport:
    """Run each fixture through each model and score it against expected.json."""
    chosen = list(models) if models else ["mock"]
    if not chosen:
        raise ProofJsonError("Pass at least one model to eval.")
    fixtures = discover_fixtures(directory)
    report = EvalReport(directory=str(Path(directory)), models=chosen)
    for model in chosen:
        for fixture in fixtures:
            report.scores.append(_score_fixture(fixture, model))
    return report


def render_markdown(report: EvalReport) -> str:
    """Markdown tables: per model, per fixture, and per field."""
    lines = [
        f"# proofjson eval",
        "",
        f"Directory: `{report.directory}`",
        "",
        "## Per model",
        "",
        "| model | fixtures | fields | correct | field accuracy | verified | verified-quote rate |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    by_model: dict[str, list[FixtureScore]] = {model: [] for model in report.models}
    for score in report.scores:
        by_model.setdefault(score.model, []).append(score)
    for model, scores in by_model.items():
        fields = sum(item.field_count for item in scores)
        correct = sum(item.correct for item in scores)
        verified = sum(item.verified for item in scores)
        evidence = sum(item.evidence_count for item in scores)
        lines.append(
            "| "
            + " | ".join(
                [
                    _cell(model),
                    str(len(scores)),
                    str(fields),
                    str(correct),
                    _percent(correct, fields),
                    str(verified),
                    _percent(verified, evidence),
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## Per fixture",
            "",
            "| fixture | model | field accuracy | verified-quote rate |",
            "| --- | --- | ---: | ---: |",
        ]
    )
    for score in report.scores:
        lines.append(
            "| "
            + " | ".join(
                [
                    _cell(score.name),
                    _cell(score.model),
                    f"{_percent(score.correct, score.field_count)} ({score.correct}/{score.field_count})",
                    f"{_percent(score.verified, score.evidence_count)} ({score.verified}/{score.evidence_count})",
                ]
            )
            + " |"
        )

    field_paths: list[str] = []
    seen: set[str] = set()
    for score in report.scores:
        for path in score.hits:
            if path not in seen:
                seen.add(path)
                field_paths.append(path)
    field_paths.sort()

    lines.extend(["", "## Per field", "", "| field | " + " | ".join(_cell(model) for model in report.models) + " |"])
    lines.append("| --- | " + " | ".join("---:" for _ in report.models) + " |")
    for path in field_paths:
        cells = [_cell(path)]
        for model in report.models:
            correct = 0
            total = 0
            for score in report.scores:
                if score.model != model or path not in score.hits:
                    continue
                total += 1
                if score.hits[path]:
                    correct += 1
            cells.append(f"{_percent(correct, total)} ({correct}/{total})" if total else "n/a")
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    return "\n".join(lines)


def values_equal(expected: Any, actual: Any) -> bool:
    if isinstance(expected, bool) or isinstance(actual, bool):
        return isinstance(expected, bool) and isinstance(actual, bool) and expected == actual
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return abs(float(expected) - float(actual)) <= 1e-6
    if isinstance(expected, str) and isinstance(actual, str):
        return expected.strip() == actual.strip()
    return expected == actual


def _score_fixture(fixture: Path, model: str) -> FixtureScore:
    pdf = _one_pdf(fixture)
    expected_path = fixture / "expected.json"
    try:
        expected = json.loads(expected_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProofJsonError(f"Expected JSON is invalid: {expected_path}") from exc
    if not isinstance(expected, dict):
        raise ProofJsonError(f"Expected JSON must be an object: {expected_path}")
    cassette = fixture / "cassette.json" if _is_mock(model) else None
    result = extract(pdf, fixture / "schema.json", model=model, cassette=cassette)
    expected_leaves = flatten(expected)
    actual_leaves = flatten(result.data)
    hits = {
        path: path in actual_leaves and values_equal(value, actual_leaves[path])
        for path, value in expected_leaves.items()
    }
    verified = sum(1 for item in result.evidence.values() if item.get("verified") is True)
    return FixtureScore(
        name=fixture.name,
        model=model,
        hits=hits,
        verified=verified,
        evidence_count=len(result.evidence),
    )


def _is_fixture(path: Path) -> bool:
    return (path / "schema.json").is_file() and (path / "expected.json").is_file()


def _is_mock(model: str) -> bool:
    return model.strip() == "mock" or model.strip().startswith("mock/")


def _one_pdf(fixture: Path) -> Path:
    pdfs = sorted(fixture.glob("*.pdf"))
    if len(pdfs) != 1:
        raise ProofJsonError(f"{fixture} must contain exactly one PDF (found {len(pdfs)}).")
    return pdfs[0]


def _percent(correct: int, total: int) -> str:
    if total == 0:
        return "n/a"
    return f"{(100.0 * correct / total):.1f}%"


def _cell(value: str) -> str:
    return value.replace("|", "\\|")
