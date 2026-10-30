from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.agents.contracts import Rung
from shikiru.config.loader import load_settings
from shikiru.operator.detectors import DetectedProject, render_config
from shikiru.paths import PROJECT_CONFIG_RELATIVE_PATH

if TYPE_CHECKING:
    from pathlib import Path

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
    config_path = tmp_path / PROJECT_CONFIG_RELATIVE_PATH
    config_path.parent.mkdir()
    config_path.write_text(config_text)

    settings = load_settings(tmp_path)

    assert 'ladder = ["codex:gpt-6.1-sol:high", "claude:opus"]' in config_text
    assert settings.roles.leader.ladder == _LEADER_LADDER
    assert settings.roles.implementer.ladder == _IMPLEMENTER_LADDER
    assert settings.roles.implementer.complexity["high"].ladder == _HIGH_COMPLEXITY_LADDER
    assert settings.roles.helper.ladder == _HELPER_LADDER
