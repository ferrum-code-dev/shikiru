from __future__ import annotations

import pytest
from pydantic import ValidationError

from shikiru.plugins.agents.muse.options import MAX_COMPACT_AT_PCT, MuseOptions


@pytest.mark.parametrize(
    ("compact_at_pct", "accepted"),
    [
        (0, True),
        (MAX_COMPACT_AT_PCT, True),
        (MAX_COMPACT_AT_PCT + 0.5, False),
        (100, False),
        (-0.1, False),
    ],
)
def test_compact_at_pct_keeps_the_hard_threshold_below_one(
    compact_at_pct: float, accepted: bool
) -> None:
    if accepted:
        assert MuseOptions(compact_at_pct=compact_at_pct).compact_at_pct == compact_at_pct
    else:
        with pytest.raises(ValidationError):
            MuseOptions(compact_at_pct=compact_at_pct)
