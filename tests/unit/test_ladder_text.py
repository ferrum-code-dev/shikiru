from __future__ import annotations

import pytest

from shikiru.agents.contracts import Rung
from shikiru.domain.ladder_text import LadderTextError, parse_ladder_text


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("agent:model", (Rung("agent", "model"),)),
        (
            " agent:model:high , other:next ",
            (Rung("agent", "model", "high"), Rung("other", "next")),
        ),
        ("", ()),
        ("   ", ()),
    ],
)
def test_parses_ordered_ladder_entries(value: str, expected: tuple[Rung, ...]) -> None:
    assert parse_ladder_text(value) == expected


@pytest.mark.parametrize(
    ("value", "entry_index"),
    [
        (",agent:model", 0),
        ("agent:model,", 1),
        ("agent:model,,other:next", 1),
        ("agent", 0),
        ("agent:model:high:extra", 0),
        ("agent::high", 0),
        ("agent:model:", 0),
        (", ", 0),
    ],
)
def test_rejects_malformed_entries_without_dropping_their_position(
    value: str,
    entry_index: int,
) -> None:
    with pytest.raises(LadderTextError) as caught:
        parse_ladder_text(value)

    assert caught.value.entry_index == entry_index
