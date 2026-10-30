from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from shikiru.agents.contracts import Rung
from shikiru.config.loader import load_settings
from shikiru.operator.detectors import DetectedProject, render_config
from shikiru.paths import PROJECT_CONFIG_RELATIVE_PATH

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.config.settings import Settings

_PROJECT = DetectedProject(
    name="demo",
    base="main",
    worktree_clean=True,
    caches=(),
    trackers=(),
    agents=("claude", "codex"),
)
_LEADER_LADDER = [
    Rung(agent="codex", model="gpt-6.1-sol", effort="high"),
    Rung(agent="claude", model="opus"),
]
_IMPLEMENTER_LADDER = [Rung(agent="claude", model="llama3:8b", effort="high")]
_HIGH_COMPLEXITY_LADDER = [Rung(agent="codex", model="sol")]
_HELPER_LADDER = [Rung(agent="claude", model="haiku", effort="low")]


def _load_rendered(tmp_path: Path, config_text: str) -> Settings:
    config_path = tmp_path / PROJECT_CONFIG_RELATIVE_PATH
    config_path.parent.mkdir()
    config_path.write_text(config_text)
    return load_settings(tmp_path)


def test_written_ladders_load_back_as_the_same_rungs(tmp_path: Path) -> None:
    config_text = render_config(
        _PROJECT,
        ladders={
            "leader": _LEADER_LADDER,
            "implementer": _IMPLEMENTER_LADDER,
            "helper": _HELPER_LADDER,
        },
        complexity={"high": _HIGH_COMPLEXITY_LADDER},
    )

    settings = _load_rendered(tmp_path, config_text)

    assert 'ladder = ["codex:gpt-6.1-sol:high", "claude:opus"]' in config_text
    assert settings.roles.leader.ladder == _LEADER_LADDER
    assert settings.roles.implementer.ladder == _IMPLEMENTER_LADDER
    assert settings.roles.implementer.complexity["high"].ladder == _HIGH_COMPLEXITY_LADDER
    assert settings.roles.helper.ladder == _HELPER_LADDER


@pytest.mark.parametrize(
    "leader_ladder",
    [
        pytest.param([Rung(agent="claude", model="opus", effort="")], id="empty-effort"),
        pytest.param(
            [Rung(agent="claude", model="opus", effort="custom:effort")],
            id="colon-effort",
        ),
        pytest.param([Rung(agent="claude", model="llama3:8b")], id="colon-model-without-effort"),
    ],
)
def test_rungs_the_short_form_cannot_express_still_load_back_unchanged(
    tmp_path: Path, leader_ladder: list[Rung]
) -> None:
    config_text = render_config(
        _PROJECT,
        ladders={
            "leader": leader_ladder,
            "implementer": _IMPLEMENTER_LADDER,
            "helper": _HELPER_LADDER,
        },
    )

    settings = _load_rendered(tmp_path, config_text)

    assert settings.roles.leader.ladder == leader_ladder
