from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from shikiru.agents.contracts import Rung
from shikiru.config.loader import load_settings
from shikiru.config.settings import ComplexityLadder, ImplementerConfig

if TYPE_CHECKING:
    from pathlib import Path

_MINIMAL_PROJECT_CONFIG = """
[project]
name = "demo"

[roles.leader]
ladder = [ { agent = "claude", model = "opus", effort = "high" } ]

[roles.implementer]
ladder = [ { agent = "codex", model = "sol", effort = "high" } ]

[roles.helper]
ladder = [ { agent = "claude", model = "haiku", effort = "low" } ]
"""


def _write_minimal_project(tmp_path: Path, extra: str = "") -> None:
    shikiru_dir = tmp_path / ".shikiru"
    shikiru_dir.mkdir()
    (shikiru_dir / "config.toml").write_text(_MINIMAL_PROJECT_CONFIG + extra)


def test_packaged_defaults_validate_together_with_a_minimal_project_file(tmp_path: Path) -> None:
    _write_minimal_project(tmp_path)

    settings = load_settings(tmp_path)

    assert settings.project.name == "demo"
    assert settings.orchestration.max_parallel == 2
    assert settings.orchestration.resources.max_cpu_pct == 85
    assert settings.dashboard.widgets[0] == "header"
    assert settings.notify.command == ""
    assert settings.roles.implementer.escalate_after == 2


def test_settings_reject_unknown_keys_in_a_ladder_entry() -> None:
    with pytest.raises(ValidationError):
        ImplementerConfig.model_validate(
            {
                "escalate_after": 2,
                "ladder": [{"agent": "codex", "model": "sol", "bogus": True}],
            }
        )


def test_implementer_ladder_for_falls_back_to_base_ladder_without_complexity_override() -> None:
    base_rung = Rung(agent="codex", model="sol", effort="high")
    implementer = ImplementerConfig(ladder=[base_rung], escalate_after=2)

    assert implementer.ladder_for("high") == [base_rung]


def test_implementer_ladder_for_uses_complexity_override_when_configured() -> None:
    base_rung = Rung(agent="codex", model="sol", effort="high")
    high_rung = Rung(agent="claude", model="opus", effort="xhigh")
    implementer = ImplementerConfig(
        ladder=[base_rung],
        escalate_after=2,
        complexity={"high": ComplexityLadder(ladder=[high_rung])},
    )

    assert implementer.ladder_for("high") == [high_rung]
    assert implementer.ladder_for("normal") == [base_rung]
