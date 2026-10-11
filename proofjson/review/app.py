"""FastAPI review UI: approve, edit, or reject each field, then write ``*.reviewed.json``."""

from __future__ import annotations

import json
import webbrowser
from importlib.resources import files
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.background import BackgroundTask

from proofjson.errors import ProofJsonError
from proofjson.paths import get_path, parse_path, set_path
from proofjson.pdf import read_pdf
from proofjson.validate import coerce_value, schema_at, validation_messages

_DECISIONS = {"approved", "edited", "rejected"}


def reviewed_output_path(path: Path) -> Path:
    """``out.json`` becomes ``out.reviewed.json``."""
    name = path.name
    if name.endswith(".reviewed.json"):
        return path
    if name.endswith(".json"):
        return path.with_name(name[: -len(".json")] + ".reviewed.json")
    return path.with_name(name + ".reviewed.json")


def pending_paths(document: dict[str, Any]) -> list[str]:
    evidence = document.get("_evidence")
    if not isinstance(evidence, dict):
        return []
    review = document.get("_review")
    if not isinstance(review, dict):
        review = {}
    pending: list[str] = []
    for path in evidence:
        entry = review.get(path)
        status = entry.get("status") if isinstance(entry, dict) else None
        if status not in _DECISIONS:
            pending.append(path)
    return pending


def load_pages(document: dict[str, Any]) -> tuple[list[str], str | None]:
    meta = document.get("_meta")
    source = meta.get("source") if isinstance(meta, dict) else None
    if not isinstance(source, str) or not source:
        return [], "This extraction has no source PDF path."
    pdf_path = Path(source)
    if not pdf_path.is_file():
        return [], f"Could not open the source PDF at {source}."
    try:
        return [page.text for page in read_pdf(pdf_path)], None
    except ProofJsonError as exc:
        return [], str(exc)


class ReviewSession:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self.output_path = reviewed_output_path(self.path)
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ProofJsonError(f"Review file is not JSON: {self.path}") from exc
        if not isinstance(loaded, dict):
            raise ProofJsonError("Review file must be a JSON object.")
        evidence = loaded.get("_evidence")
        if not isinstance(evidence, dict) or not evidence:
            raise ProofJsonError("This JSON has no _evidence map. Run proofjson extract first.")
        if not isinstance(loaded.get("_review"), dict):
            loaded["_review"] = {}
        self.document = loaded
        self.pages, self.pdf_error = load_pages(loaded)

    def decide(self, path: str, action: str, has_value: bool, value: Any) -> dict[str, Any]:
        evidence = self.document["_evidence"]
        if path not in evidence:
            raise KeyError(path)
        if action not in {"approve", "edit", "reject"}:
            raise ValueError("Action must be approve, edit, or reject.")
        try:
            tokens = parse_path(path)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        review = self.document["_review"]
        if action == "approve":
            review[path] = {"status": "approved"}
            return review[path]
        if action == "reject":
            review[path] = {"status": "rejected"}
            return review[path]
        if not has_value:
            raise ValueError("An edit needs a value.")
        schema = _schema_of(self.document)
        leaf = schema_at(schema, tokens) if schema else {}
        coerced = coerce_value(value, leaf, schema) if schema else value
        errors = _leaf_errors(leaf, coerced, schema)
        if errors:
            raise ValueError(errors[0])
        try:
            previous = get_path(self.document, tokens)
        except KeyError:
            previous = None
        set_path(self.document, tokens, coerced)
        review[path] = {"status": "edited", "previous": previous}
        return review[path]

    def export(self) -> dict[str, Any]:
        pending = pending_paths(self.document)
        meta = self.document.get("_meta")
        if not isinstance(meta, dict):
            meta = {}
            self.document["_meta"] = meta
        meta["review_complete"] = not pending
        meta["pending_fields"] = pending
        text = json.dumps(self.document, indent=2, ensure_ascii=False) + "\n"
        self.output_path.write_text(text, encoding="utf-8")
        return {
            "path": str(self.output_path),
            "pending": pending,
            "complete": not pending,
        }


def create_app(json_path: str | Path) -> FastAPI:
    session = ReviewSession(Path(json_path))
    app = FastAPI(title="proofjson review", docs_url=None, redoc_url=None)
    app.state.session = session
    app.state.server = None
    html = _review_html()

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return html

    @app.get("/api/document")
    def document() -> dict[str, Any]:
        return {
            "document": session.document,
            "pages": session.pages,
            "pdf_error": session.pdf_error,
            "output": str(session.output_path),
        }

    @app.post("/api/decision")
    async def decision(request: Request) -> dict[str, Any]:
        try:
            payload = json.loads(await request.body() or b"{}")
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="Decision body must be JSON.") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Decision body must be an object.")
        path = payload.get("path")
        action = payload.get("action")
        if not isinstance(path, str) or not isinstance(action, str):
            raise HTTPException(status_code=400, detail="path and action are required.")
        try:
            entry = session.decide(path, action, "value" in payload, payload.get("value"))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"No evidence for {path}.") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"review": entry, "pending": pending_paths(session.document)}

    @app.post("/api/export")
    async def export(request: Request) -> JSONResponse:
        try:
            payload = json.loads(await request.body() or b"{}")
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        result = session.export()
        background = None
        if payload.get("shutdown") is True:
            background = BackgroundTask(_request_shutdown, request.app)
        return JSONResponse(result, background=background)

    return app


def serve(
    json_path: str | Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
) -> int:
    """Serve the review UI. Returns 0 when every field was decided and the file was saved."""
    path = Path(json_path)
    if not path.is_file():
        raise ProofJsonError(f"File not found: {path}")
    if port < 1 or port > 65535:
        raise ProofJsonError("Port must be between 1 and 65535.")
    app = create_app(path)
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    app.state.server = server
    url = f"http://{host}:{port}/"
    print(f"Review {path.name} at {url}")
    print("Decide each field, then save. The process exits when you save and close.")
    if host not in {"127.0.0.1", "localhost", "::1"}:
        print(f"warning: {host} is not a loopback address. This document is reachable there.")
    if open_browser:
        webbrowser.open(url)
    server.run()
    output = reviewed_output_path(path)
    if not output.is_file():
        return 1
    saved = json.loads(output.read_text(encoding="utf-8"))
    if not isinstance(saved, dict):
        return 1
    return 0 if not pending_paths(saved) else 1


def _request_shutdown(app: FastAPI) -> None:
    server = getattr(app.state, "server", None)
    if server is not None:
        server.should_exit = True


def _schema_of(document: dict[str, Any]) -> dict[str, Any]:
    meta = document.get("_meta")
    if isinstance(meta, dict) and isinstance(meta.get("schema"), dict):
        return meta["schema"]
    return {}


def _leaf_errors(leaf: dict[str, Any], value: Any, root: dict[str, Any]) -> list[str]:
    if not leaf or not root:
        return []
    wrapped = {
        "$defs": root.get("$defs", {}),
        "type": "object",
        "properties": {"value": leaf},
        "required": ["value"],
        "additionalProperties": False,
    }
    return validation_messages(wrapped, {"value": value})


def _review_html() -> str:
    return files("proofjson.review").joinpath("static/index.html").read_text(encoding="utf-8")
