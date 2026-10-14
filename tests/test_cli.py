import json
from pathlib import Path

from typer.testing import CliRunner

from proofjson.cli import app

ROOT = Path(__file__).resolve().parents[1]
INVOICE = ROOT / "examples" / "invoice"
runner = CliRunner()


def test_help_and_version() -> None:
    help_result = runner.invoke(app, ["--help"])
    assert help_result.exit_code == 0
    assert "extract" in help_result.stdout
    assert "batch" in help_result.stdout
    assert "review" in help_result.stdout
    assert "eval" in help_result.stdout
    assert "packs" in help_result.stdout
    version = runner.invoke(app, ["--version"])
    assert version.exit_code == 0
    assert "0.1.0" in version.stdout


def test_extract_invoice_with_mock(tmp_path: Path) -> None:
    output = tmp_path / "out.json"
    result = runner.invoke(
        app,
        [
            "extract",
            str(INVOICE / "invoice.pdf"),
            "--schema",
            str(INVOICE / "schema.json"),
            "--model",
            "mock",
            "-o",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "needs review" in result.output
    assert "below confidence: notes" in result.output
    document = json.loads(output.read_text(encoding="utf-8"))
    expected = json.loads((INVOICE / "expected.json").read_text(encoding="utf-8"))
    for key, value in expected.items():
        assert document[key] == value
    assert document["_evidence"]["invoice_number"]["quote"]
    assert document["_evidence"]["invoice_number"]["verified"] is True
    assert document["_evidence"]["total"]["page"] == 2
    assert "schema" in document["_meta"]


def test_eval_prints_a_markdown_table() -> None:
    result = runner.invoke(app, ["eval", str(ROOT / "examples")])
    assert result.exit_code == 0, result.output
    assert "| model | fixtures | fields |" in result.stdout
    assert "verified-quote rate" in result.stdout
    assert "line_items[0].description" in result.stdout


def test_unknown_provider_exits_2(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "extract",
            str(INVOICE / "invoice.pdf"),
            "--schema",
            str(INVOICE / "schema.json"),
            "--model",
            "nope/model",
            "-o",
            str(tmp_path / "out.json"),
        ],
    )
    assert result.exit_code == 2
    assert "Unknown provider" in result.output


def test_readme_quickstart_command(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(ROOT)
    output = tmp_path / "out.json"
    result = runner.invoke(
        app,
        [
            "extract",
            "examples/invoice/invoice.pdf",
            "--schema",
            "examples/invoice/schema.json",
            "--model",
            "mock",
            "-o",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "examples/invoice/invoice.pdf" in readme
    assert "--schema examples/invoice/schema.json" in readme
    assert "--model mock" in readme
    assert "proofjson eval examples" in readme
