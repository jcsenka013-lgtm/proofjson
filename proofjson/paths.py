"""Field paths such as ``vendor.name`` and ``line_items[0].amount``."""

from __future__ import annotations

from typing import Any


def parse_path(path: str) -> list[str | int]:
    """Split a field path into string keys and list indexes."""
    if not isinstance(path, str) or not path or path.endswith(".") or path.endswith("["):
        raise ValueError(f"Invalid field path: {path!r}")
    if ".." in path or ".[" in path:
        raise ValueError(f"Invalid field path: {path!r}")

    tokens: list[str | int] = []
    index = 0
    length = len(path)
    while index < length:
        char = path[index]
        if char == ".":
            if not tokens:
                raise ValueError(f"Invalid field path: {path!r}")
            index += 1
            if index >= length or path[index] in ".[":
                raise ValueError(f"Invalid field path: {path!r}")
            continue
        if char == "[":
            if not tokens:
                raise ValueError(f"Invalid field path: {path!r}")
            end = path.find("]", index)
            if end < 0:
                raise ValueError(f"Invalid field path: {path!r}")
            number = path[index + 1 : end]
            if not number.isdigit():
                raise ValueError(f"Invalid field path: {path!r}")
            tokens.append(int(number))
            index = end + 1
            continue
        end = index
        while end < length and path[end] not in ".[":
            end += 1
        token = path[index:end]
        if not token:
            raise ValueError(f"Invalid field path: {path!r}")
        tokens.append(token)
        index = end
    if not tokens:
        raise ValueError(f"Invalid field path: {path!r}")
    return tokens


def set_path(root: dict[str, Any], tokens: list[str | int], value: Any) -> None:
    """Write ``value`` at ``tokens``, creating objects and arrays along the way."""
    if not tokens:
        raise ValueError("empty path")
    current: Any = root
    for index, token in enumerate(tokens[:-1]):
        nxt = tokens[index + 1]
        if isinstance(token, str):
            if not isinstance(current, dict):
                raise ValueError(f"Cannot set {token!r} on a non-object")
            if token not in current or current[token] is None:
                current[token] = [] if isinstance(nxt, int) else {}
            current = current[token]
            continue
        if not isinstance(current, list):
            raise ValueError("Cannot set an index on a non-array")
        while len(current) <= token:
            current.append(None)
        if current[token] is None:
            current[token] = [] if isinstance(nxt, int) else {}
        current = current[token]
    last = tokens[-1]
    if isinstance(last, str):
        if not isinstance(current, dict):
            raise ValueError("Cannot set a field on a non-object")
        current[last] = value
        return
    if not isinstance(current, list):
        raise ValueError("Cannot set an index on a non-array")
    while len(current) <= last:
        current.append(None)
    current[last] = value


def get_path(root: Any, tokens: list[str | int]) -> Any:
    current = root
    for token in tokens:
        if isinstance(token, int):
            if not isinstance(current, list) or token >= len(current) or token < 0:
                raise KeyError(token)
            current = current[token]
            continue
        if not isinstance(current, dict) or token not in current:
            raise KeyError(token)
        current = current[token]
    return current


def unflatten(flat: dict[str, Any]) -> dict[str, Any]:
    """Turn leaf paths into a nested object."""
    root: dict[str, Any] = {}
    for path, value in flat.items():
        set_path(root, parse_path(path), value)
    return root


def flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    """Turn a nested object into leaf paths. Dicts and lists are not leaves."""
    if isinstance(value, dict):
        leaves: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("Only string object keys can be flattened")
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(item, (dict, list)):
                leaves.update(flatten(item, path))
            else:
                leaves[path] = item
        return leaves
    if isinstance(value, list):
        leaves = {}
        for index, item in enumerate(value):
            path = f"{prefix}[{index}]"
            if isinstance(item, (dict, list)):
                leaves.update(flatten(item, path))
            else:
                leaves[path] = item
        return leaves
    if prefix:
        return {prefix: value}
    return {}
