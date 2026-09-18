from __future__ import annotations

import pytest

from shikiru.agents.contracts import Rung, parse_rung
from shikiru.errors import ConfigError


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("claude:opus", Rung(agent="claude", model="opus", effort=None)),
        ("codex:gpt-5.6-sol:high", Rung(agent="codex", model="gpt-5.6-sol", effort="high")),
        ("claude:haiku:low", Rung(agent="claude", model="haiku", effort="low")),
    ],
)
def test_parse_rung_accepts_valid_input(text: str, expected: Rung) -> None:
    assert parse_rung(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "claude",
        "claude:opus:high:extra",
        "claude::high",
        "",
    ],
)
def test_parse_rung_rejects_malformed_input(text: str) -> None:
    with pytest.raises(ConfigError):
        parse_rung(text)
