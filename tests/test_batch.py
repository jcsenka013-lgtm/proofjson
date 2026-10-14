import json
import shutil
from pathlib import Path

from pypdf import PdfWriter
from typer.testing import CliRunner

from proofjson.batch import BatchStatus, run_batch
from proofjson.cli import app

ROOT = Path(__file__).resolve().parents[1]
INVOICE = ROOT / "examples" / "invoice"
runner = CliRunner()


def _stage_invoice(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copy(INVOICE / "invoice.pdf", folder / "invoice.pdf")
    shutil.copy(INVOICE / "cassette.json", folder / "cassette.json")


def _blank_pdf(path: Path) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    with path.open("wb") as handle:
        writer.write(handle)


def test_batch_holds_the_invoice_at_the_default_gate(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    out = tmp_path / "out"
    _stage_invoice(inbox)
    report = run_batch(inbox, INVOICE / "schema.json", model="mock", output_dir=out, pack="invoice")
    assert report.exit_code == 1
    assert report.items[0].status is BatchStatus.REVIEW
    assert report.items[0].fields == 20
    assert "notes" in report.items[0].detail
    written = json.loads((out / "invoice.proof.json").read_text(encoding="utf-8"))
    assert written["_meta"]["pack"] == "invoice"
    assert written["_meta"]["needs_review"] is True


def test_batch_passes_when_the_gate_is_lowered(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    _stage_invoice(inbox)
    report = run_batch(
        inbox,
        INVOICE / "schema.json",
        model="mock",
        min_confidence=0.7,
        output_dir=tmp_path / "out",
    )
    assert report.exit_code == 0
    assert report.items[0].status is BatchStatus.OK
    assert report.items[0].needs_review is False


def test_batch_keeps_going_when_one_pdf_has_no_text(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    out = tmp_path / "out"
    _stage_invoice(inbox)
    _blank_pdf(inbox / "scan.pdf")
    report = run_batch(inbox, INVOICE / "schema.json", model="mock", output_dir=out)
    by_name = {item.pdf.name: item for item in report.items}
    assert report.exit_code == 2
    assert by_name["invoice.pdf"].status is BatchStatus.REVIEW
    assert (out / "invoice.proof.json").is_file()
    assert by_name["scan.pdf"].status is BatchStatus.ERROR
    assert "OCR" in by_name["scan.pdf"].detail
    assert not (out / "scan.proof.json").exists()


def test_batch_cli_report_and_readme_shape(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    out = tmp_path / "out"
    report_path = tmp_path / "batch.json"
    _stage_invoice(inbox)
    result = runner.invoke(
        app,
        [
            "batch",
            str(inbox),
            "--pack",
            "invoice",
            "--model",
            "mock",
            "-o",
            str(out),
            "--report",
            str(report_path),
        ],
    )
    assert result.exit_code == 1, result.output
    assert "review      20  invoice.pdf  below confidence: notes" in result.stdout
    assert "1 file, 0 passed, 1 needs review, 0 failed" in result.stdout
    summary = json.loads(report_path.read_text(encoding="utf-8"))
    assert summary["passed"] is False
    assert summary["files"][0]["status"] == "review"
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "proofjson batch examples/invoice --pack invoice --model mock --min-confidence 0.7" in readme


def test_batch_rejects_an_empty_folder(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["batch", str(tmp_path), "--schema", str(INVOICE / "schema.json"), "--model", "mock"],
    )
    assert result.exit_code == 2
    assert "No PDFs" in result.output
