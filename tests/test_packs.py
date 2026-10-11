import json
from pathlib import Path

from typer.testing import CliRunner

from proofjson.cli import app
from proofjson.errors import ProofJsonError
from proofjson.packs import get_pack, list_packs

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
runner = CliRunner()

PACK_IDS = [
    "bank_statement",
    "invoice",
    "packing_slip",
    "pay_stub",
    "purchase_order",
    "receipt",
    "utility_bill",
    "vendor_form",
]


def test_store_lists_every_pack() -> None:
    packs = list_packs()
    assert [pack.id for pack in packs] == PACK_IDS
    for pack in packs:
        assert pack.title
        assert pack.summary
        assert pack.version == "1"
        schema = pack.schema()
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert schema["required"]


def test_example_schemas_match_the_store() -> None:
    for pack in list_packs():
        example = json.loads((EXAMPLES / pack.id / "schema.json").read_text(encoding="utf-8"))
        assert example == pack.schema()


def test_unknown_pack_names_the_store() -> None:
    try:
        get_pack("not-a-pack")
    except ProofJsonError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected ProofJsonError")
    assert "Unknown pack" in message
    assert "invoice" in message


def test_packs_command_lists_and_shows() -> None:
    listed = runner.invoke(app, ["packs"])
    assert listed.exit_code == 0, listed.output
    for pack_id in PACK_IDS:
        assert pack_id in listed.stdout
    shown = runner.invoke(app, ["packs", "pay_stub"])
    assert shown.exit_code == 0, shown.output
    assert "employee_id" in shown.stdout
    assert "000-00-0000" in shown.stdout
    missing = runner.invoke(app, ["packs", "not-a-pack"])
    assert missing.exit_code == 2
    assert "Unknown pack" in missing.output


def test_extract_with_pack(tmp_path: Path) -> None:
    output = tmp_path / "out.json"
    pdf = EXAMPLES / "purchase_order" / "purchase_order.pdf"
    result = runner.invoke(
        app,
        ["extract", str(pdf), "--pack", "purchase_order", "--model", "mock", "-o", str(output)],
    )
    assert result.exit_code == 0, result.output
    document = json.loads(output.read_text(encoding="utf-8"))
    expected = json.loads((EXAMPLES / "purchase_order" / "expected.json").read_text(encoding="utf-8"))
    for key, value in expected.items():
        assert document[key] == value
    assert document["_meta"]["pack"] == "purchase_order"
    assert document["_evidence"]["total"]["verified"] is True


def test_extract_rejects_schema_and_pack_together(tmp_path: Path) -> None:
    pdf = EXAMPLES / "invoice" / "invoice.pdf"
    result = runner.invoke(
        app,
        [
            "extract",
            str(pdf),
            "--schema",
            str(EXAMPLES / "invoice" / "schema.json"),
            "--pack",
            "invoice",
            "--model",
            "mock",
            "-o",
            str(tmp_path / "out.json"),
        ],
    )
    assert result.exit_code == 2
    assert "either --schema or --pack" in result.output


def test_extract_requires_a_schema_or_pack(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "extract",
            str(EXAMPLES / "invoice" / "invoice.pdf"),
            "--model",
            "mock",
            "-o",
            str(tmp_path / "out.json"),
        ],
    )
    assert result.exit_code == 2
    assert "Pass --schema or --pack" in result.output
