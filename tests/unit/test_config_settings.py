from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from shikiru.config.loader import load_settings
from shikiru.config.settings import (
    ComplexityLadder,
    ComplexityLadders,
    ImplementerConfig,
    ProjectConfig,
    RungSpec,
    implementer_ladder,
)

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


def test_settings_reject_unknown_keys() -> None:
    with pytest.raises(ValidationError):
        ProjectConfig.model_validate({"name": "demo", "unknown": True})


def test_settings_models_are_frozen(tmp_path: Path) -> None:
    _write_minimal_project(tmp_path)
    settings = load_settings(tmp_path)

    with pytest.raises(ValidationError):
        settings.project.name = "renamed"


def test_implementer_ladder_falls_back_to_base_ladder_without_complexity_override() -> None:
    base_rung = RungSpec(agent="codex", model="sol", effort="high")
    implementer = ImplementerConfig(ladder=[base_rung])

    assert implementer_ladder(implementer, "high") == [base_rung]


def test_implementer_ladder_uses_complexity_override_when_configured() -> None:
    base_rung = RungSpec(agent="codex", model="sol", effort="high")
    high_rung = RungSpec(agent="claude", model="opus", effort="xhigh")
    implementer = ImplementerConfig(
        ladder=[base_rung],
        complexity=ComplexityLadders(high=ComplexityLadder(ladder=[high_rung])),
    )

    assert implementer_ladder(implementer, "high") == [high_rung]
    assert implementer_ladder(implementer, "normal") == [base_rung]


def test_implementer_escalate_after_defaults_to_two() -> None:
    implementer = ImplementerConfig(ladder=[RungSpec(agent="codex", model="sol")])

    assert implementer.escalate_after == 2
