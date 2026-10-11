from pathlib import Path

from proofjson.eval import evaluate, render_markdown, values_equal

ROOT = Path(__file__).resolve().parents[1]


def test_values_equal_rejects_bool_and_int_confusion() -> None:
    assert values_equal(True, 1) is False
    assert values_equal(" INV-1042 ", "INV-1042") is True
    assert values_equal(128.4, 128.40) is True


def test_mock_eval_scores_every_fixture(examples_dir: Path) -> None:
    report = evaluate(examples_dir, ["mock"])
    assert [score.name for score in report.scores] == [
        "bank_statement",
        "invoice",
        "packing_slip",
        "pay_stub",
        "purchase_order",
        "receipt",
        "utility_bill",
        "vendor_form",
    ]
    assert all(score.correct == score.field_count and score.field_count > 0 for score in report.scores)
    receipt = next(score for score in report.scores if score.name == "receipt")
    assert receipt.verified == receipt.evidence_count - 1
    markdown = render_markdown(report)
    assert "## Per model" in markdown
    assert "## Per fixture" in markdown
    assert "## Per field" in markdown
    assert "| mock |" in markdown
    assert "invoice_number" in markdown
    row = next(line for line in markdown.splitlines() if line.startswith("| mock |"))
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert row in readme
