from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.agents.contracts import Rung
from shikiru.config.loader import load_settings
from shikiru.config.settings import ComplexityLadder, ImplementerConfig
from tests.support.config import write_minimal_project

if TYPE_CHECKING:
    from pathlib import Path


def test_packaged_defaults_validate_together_with_a_minimal_project_file(tmp_path: Path) -> None:
    write_minimal_project(tmp_path)

    settings = load_settings(tmp_path)

    assert settings.project.name == "demo"


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
