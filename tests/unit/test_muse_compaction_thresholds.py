from __future__ import annotations

import pytest

from shikiru.plugins.agents.muse.options import DEFAULT_COMPACT_AT_PCT, MAX_COMPACT_AT_PCT
from shikiru.plugins.agents.muse.turn import compaction_thresholds


@pytest.mark.parametrize(
    ("compact_at_pct", "expected_soft", "expected_hard"),
    [
        (0, 0.0, 0.005),
        (DEFAULT_COMPACT_AT_PCT, 0.8, 0.805),
        (MAX_COMPACT_AT_PCT, 0.99, 0.995),
    ],
)
def test_compact_percentage_maps_to_muse_threshold_fractions(
    compact_at_pct: float, expected_soft: float, expected_hard: float
) -> None:
    assert compaction_thresholds(compact_at_pct) == pytest.approx((expected_soft, expected_hard))
