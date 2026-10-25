"""`HeadlessAgent` for the fake agent: served from `agents.fake.options.asks`."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import timedelta
    from pathlib import Path


class FakeHeadlessAgent:
    """A `HeadlessAgent` that returns one canned answer per call, in order."""

    def __init__(self, answers: Sequence[Mapping[str, Any]]) -> None:
        """Serve `answers` in order, one per `ask()` call."""
        self._answers = list(answers)
        self._calls = 0

    async def ask(
        self,
        prompt: str,
        schema: Mapping[str, Any],
        model: str,
        effort: str | None,
        cwd: Path,
        timeout: timedelta,
    ) -> Mapping[str, Any]:
        """Return the next scripted answer, raising once the script is exhausted."""
        if self._calls >= len(self._answers):
            message = "fake agent's headless ask script is exhausted"
            raise LookupError(message)
        answer = self._answers[self._calls]
        self._calls += 1
        return answer
