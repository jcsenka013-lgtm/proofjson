"""Command line: extract, review, and eval."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any, Optional

import typer

from proofjson.errors import ProofJsonError
from proofjson.eval import evaluate, render_markdown
from proofjson.extract import DEFAULT_MODEL, extract, format_summary
from proofjson.packs import get_pack, list_packs
from proofjson.review.app import serve
from proofjson.version import __version__

app = typer.Typer(
    help="A trust layer for LLM extraction. Schema-valid JSON, with a quote and page behind every field.",
    no_args_is_help=True,
    add_completion=False,
)


def _fail(exc: ProofJsonError) -> None:
    typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=2) from exc


def _version(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def callback(
    version: Annotated[
        bool,
        typer.Option("--version", callback=_version, is_eager=True, help="Show the version and exit."),
    ] = False,
) -> None:
    """A trust layer for LLM extraction."""


@app.command("extract")
def extract_cmd(
    pdf: Annotated[Path, typer.Argument(help="PDF to read.", exists=True, dir_okay=False, readable=True)],
    schema: Annotated[
        Optional[Path],
        typer.Option(
            "--schema",
            "-s",
            help="JSON Schema file. Omit this when you pass --pack.",
            exists=True,
            dir_okay=False,
            readable=True,
        ),
    ] = None,
    pack: Annotated[
        Optional[str],
        typer.Option("--pack", help="Built-in schema pack. See `proofjson packs`."),
    ] = None,
    model: Annotated[
        str,
        typer.Option(
            "--model",
            "-m",
            help="provider/name, or mock. Example: gemini/gemini-2.5-flash.",
        ),
    ] = DEFAULT_MODEL,
    output: Annotated[
        Optional[Path],
        typer.Option("--output", "-o", help="Where to write JSON. Use - for stdout."),
    ] = None,
    min_confidence: Annotated[
        float,
        typer.Option("--min-confidence", help="Fields under this confidence need review."),
    ] = 0.8,
    cassette: Annotated[
        Optional[Path],
        typer.Option("--cassette", help="Mock cassette. Defaults to cassette.json beside the PDF."),
    ] = None,
) -> None:
    """Extract schema-valid JSON with a quote, page, and confidence on every field."""
    try:
        chosen = _schema_for_extract(schema, pack)
        result = extract(
            pdf,
            chosen,
            model=model,
            min_confidence=min_confidence,
            cassette=cassette,
        )
        if pack is not None:
            meta = result.document.get("_meta")
            if isinstance(meta, dict):
                meta["pack"] = pack.strip()
        payload = json.dumps(result.document, indent=2, ensure_ascii=False) + "\n"
        summary = format_summary(result)
        if output is not None and output.name == "-":
            typer.echo(payload, nl=False)
            typer.echo(summary, err=True)
        else:
            target = output if output is not None else Path(f"{pdf.stem}.proof.json")
            if not target.parent.exists():
                raise ProofJsonError(f"Output directory not found: {target.parent}")
            target.write_text(payload, encoding="utf-8")
            typer.echo(f"Wrote {target}")
            typer.echo(summary)
    except ProofJsonError as exc:
        _fail(exc)
    if result.validation_errors:
        raise typer.Exit(code=1)


@app.command("review")
def review_cmd(
    json_path: Annotated[
        Path,
        typer.Argument(help="Extraction JSON from proofjson extract.", exists=True, dir_okay=False, readable=True),
    ],
    port: Annotated[int, typer.Option("--port", help="Local port.")] = 8765,
    host: Annotated[str, typer.Option("--host", help="Bind address. Defaults to loopback.")] = "127.0.0.1",
    no_browser: Annotated[bool, typer.Option("--no-browser", help="Do not open a browser.")] = False,
) -> None:
    """Open a local page to approve, edit, or reject each field.

    Saving writes a sibling ``*.reviewed.json``. The command exits 0 when every
    field has a decision, and 1 otherwise.
    """
    try:
        code = serve(json_path, host=host, port=port, open_browser=not no_browser)
    except ProofJsonError as exc:
        _fail(exc)
    raise typer.Exit(code=code)


@app.command("eval")
def eval_cmd(
    directory: Annotated[
        Path,
        typer.Argument(help="Fixture directory, or a folder of fixtures.", exists=True, file_okay=False),
    ],
    model: Annotated[
        Optional[list[str]],
        typer.Option("--model", "-m", help="Model to score. Repeat to compare models. Default: mock."),
    ] = None,
    output: Annotated[
        Optional[Path],
        typer.Option("--output", "-o", help="Also write the markdown report to this path."),
    ] = None,
) -> None:
    """Score fixtures and print per-model, per-fixture, and per-field accuracy."""
    try:
        report = evaluate(directory, model or ["mock"])
        markdown = render_markdown(report)
        if output is not None:
            output.write_text(markdown, encoding="utf-8")
            typer.echo(f"Wrote {output}", err=True)
    except ProofJsonError as exc:
        _fail(exc)
    typer.echo(markdown, nl=False)


@app.command("packs")
def packs_cmd(
    name: Annotated[
        Optional[str],
        typer.Argument(help="Pack id to print. Omit it to list the store."),
    ] = None,
) -> None:
    """List built-in schema packs, or print one schema."""
    try:
        if name is None:
            for pack in list_packs():
                typer.echo(f"{pack.id}\t{pack.title}\t{pack.summary}")
            return
        pack = get_pack(name)
    except ProofJsonError as exc:
        _fail(exc)
    typer.echo(f"{pack.id}\t{pack.title}\tv{pack.version}")
    typer.echo(pack.summary)
    typer.echo(json.dumps(pack.schema(), indent=2, ensure_ascii=False))


def _schema_for_extract(schema: Path | None, pack: str | None) -> Path | dict[str, Any]:
    if schema is not None and pack is not None:
        raise ProofJsonError("Pass either --schema or --pack.")
    if pack is not None:
        return get_pack(pack).schema()
    if schema is not None:
        return schema
    raise ProofJsonError("Pass --schema or --pack. `proofjson packs` lists the store.")


def main() -> None:
    app()
