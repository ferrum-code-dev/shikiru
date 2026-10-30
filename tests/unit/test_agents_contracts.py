from __future__ import annotations

import pytest

from shikiru.agents.contracts import Rung, parse_rung, short_rung_text
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
        "claude:",
        ":opus",
        "claude:opus:",
        "",
    ],
)
def test_parse_rung_rejects_malformed_input(text: str) -> None:
    with pytest.raises(ConfigError):
        parse_rung(text)


@pytest.mark.parametrize(
    ("rung", "text"),
    [
        pytest.param(Rung(agent="claude", model="opus"), "claude:opus", id="without-effort"),
        pytest.param(
            Rung(agent="codex", model="gpt-5.6-sol", effort="high"),
            "codex:gpt-5.6-sol:high",
            id="with-effort",
        ),
        pytest.param(Rung(agent="ollama", model="llama3:8b"), None, id="colon-model"),
        pytest.param(
            Rung(agent="ollama", model="llama3:8b", effort="high"),
            None,
            id="colon-model-with-effort",
        ),
    ],
)
def test_short_rung_text_is_none_only_when_the_model_contains_the_separator(
    rung: Rung, text: str | None
) -> None:
    assert short_rung_text(rung) == text
