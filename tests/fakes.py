"""In-memory model provider for tests."""

from __future__ import annotations

from typing import Any


class ScriptedProvider:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def complete(
        self,
        *,
        prompt: str,
        response_schema: dict[str, Any],
        page_number: int,
        mode: str,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "prompt": prompt,
                "response_schema": response_schema,
                "page_number": page_number,
                "mode": mode,
            }
        )
        if not self.responses:
            raise AssertionError("scripted provider ran out of responses")
        return self.responses.pop(0)
