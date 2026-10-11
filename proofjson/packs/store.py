"""Catalog of schema packs shipped with proofjson."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib.resources import files
from typing import Any

from proofjson.errors import ProofJsonError

_DATA = files("proofjson.packs").joinpath("data")


@dataclass(frozen=True, slots=True)
class Pack:
    """One document schema a caller can pass to ``extract``."""

    id: str
    title: str
    summary: str
    version: str

    def schema(self) -> dict[str, Any]:
        """Return a fresh copy of this pack's JSON Schema."""
        loaded = json.loads(_read(self.id, "schema.json"))
        if not isinstance(loaded, dict):
            raise ProofJsonError(f"Pack {self.id!r} schema must be a JSON object.")
        return loaded


def list_packs() -> list[Pack]:
    """Every shipped pack, sorted by id."""
    packs: list[Pack] = []
    for entry in _DATA.iterdir():
        manifest_path = entry.joinpath("pack.json")
        if not manifest_path.is_file():
            continue
        packs.append(_load_manifest(entry.name, manifest_path.read_text(encoding="utf-8")))
    packs.sort(key=lambda pack: pack.id)
    return packs


def get_pack(pack_id: str) -> Pack:
    """Look up one pack by id."""
    key = pack_id.strip()
    for pack in list_packs():
        if pack.id == key:
            return pack
    known = ", ".join(pack.id for pack in list_packs())
    raise ProofJsonError(f"Unknown pack {pack_id!r}. Known packs: {known}.")


def _load_manifest(directory: str, text: str) -> Pack:
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProofJsonError(f"Pack manifest is not JSON: {directory}") from exc
    if not isinstance(loaded, dict):
        raise ProofJsonError(f"Pack manifest must be an object: {directory}")
    pack_id = loaded.get("id")
    title = loaded.get("title")
    summary = loaded.get("summary")
    version = loaded.get("version")
    if not all(isinstance(value, str) and value.strip() for value in (pack_id, title, summary, version)):
        raise ProofJsonError(f"Pack manifest {directory} needs id, title, summary, and version.")
    if pack_id != directory:
        raise ProofJsonError(f"Pack id {pack_id!r} does not match directory {directory!r}.")
    if not _DATA.joinpath(pack_id, "schema.json").is_file():
        raise ProofJsonError(f"Pack {pack_id!r} is missing schema.json.")
    return Pack(id=pack_id, title=title, summary=summary, version=version)


def _read(pack_id: str, name: str) -> str:
    path = _DATA.joinpath(pack_id, name)
    if not path.is_file():
        raise ProofJsonError(f"Pack {pack_id!r} is missing {name}.")
    return path.read_text(encoding="utf-8")
