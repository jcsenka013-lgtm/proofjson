import json
from importlib.resources import files
from pathlib import Path

from fastapi.testclient import TestClient

from proofjson.extract import extract
from proofjson.review.app import create_app, pending_paths, reviewed_output_path

INVOICE = Path(__file__).resolve().parents[1] / "examples" / "invoice"


def _extraction(tmp_path: Path) -> Path:
    result = extract(INVOICE / "invoice.pdf", INVOICE / "schema.json", model="mock")
    path = tmp_path / "out.json"
    path.write_text(json.dumps(result.document), encoding="utf-8")
    return path


def test_reviewed_name() -> None:
    assert reviewed_output_path(Path("out.json")).name == "out.reviewed.json"
    assert reviewed_output_path(Path("out.reviewed.json")).name == "out.reviewed.json"


def test_review_round_trip(tmp_path: Path) -> None:
    path = _extraction(tmp_path)
    app = create_app(path)
    client = TestClient(app)
    home = client.get("/")
    assert home.status_code == 200
    assert "Save and close" in home.text
    assert "proofjson" in home.text

    document = client.get("/api/document")
    assert document.status_code == 200
    body = document.json()
    assert body["pdf_error"] is None
    assert "INV-1042" in body["pages"][0]
    assert body["document"]["_evidence"]["total"]["page"] == 2

    bad = client.post("/api/decision", json={"path": "line_items[0].qty", "action": "edit", "value": "nope"})
    assert bad.status_code == 400

    missing = client.post("/api/decision", json={"path": "missing", "action": "approve"})
    assert missing.status_code == 404

    edited = client.post(
        "/api/decision",
        json={"path": "notes", "action": "edit", "value": "Paid in the demo."},
    )
    assert edited.status_code == 200
    rejected = client.post("/api/decision", json={"path": "due_date", "action": "reject"})
    assert rejected.status_code == 200

    exported = client.post("/api/export", json={"shutdown": False})
    assert exported.status_code == 200
    saved = json.loads((tmp_path / "out.reviewed.json").read_text(encoding="utf-8"))
    assert saved["notes"] == "Paid in the demo."
    assert saved["_review"]["notes"]["status"] == "edited"
    assert saved["_review"]["notes"]["previous"] == "Thank you for your demo order."
    assert saved["_review"]["due_date"]["status"] == "rejected"
    assert saved["_meta"]["review_complete"] is False
    assert pending_paths(saved)

    evidence = saved["_evidence"]
    for field in evidence:
        if field in {"notes", "due_date"}:
            continue
        approved = client.post("/api/decision", json={"path": field, "action": "approve"})
        assert approved.status_code == 200, field

    final = client.post("/api/export", json={"shutdown": False})
    assert final.json()["complete"] is True
    finished = json.loads((tmp_path / "out.reviewed.json").read_text(encoding="utf-8"))
    assert pending_paths(finished) == []
    assert finished["_evidence"]["notes"]["quote"] == "Notes: Thank you for your demo order."


def test_package_includes_the_review_page() -> None:
    html = files("proofjson.review").joinpath("static/index.html").read_text(encoding="utf-8")
    assert "Approve" in html
    assert "Reject" in html
