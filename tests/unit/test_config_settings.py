from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from shikiru.agents.contracts import Rung
from shikiru.config.loader import load_settings
from shikiru.config.settings import ComplexityLadder, ImplementerConfig
from tests.support.config import write_minimal_project
from tests.support.factories import DEFAULT_AGENTS, make_settings

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


_GHOST_RUNG = Rung(agent="ghost", model="m", effort=None)


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({"leader_ladder": [_GHOST_RUNG]}, id="leader"),
        pytest.param({"implementer_ladder": [_GHOST_RUNG]}, id="implementer"),
        pytest.param(
            {
                "implementer_complexity": {
                    "high": ComplexityLadder(ladder=[_GHOST_RUNG]),
                },
            },
            id="implementer-complexity-override",
        ),
        pytest.param({"helper_ladder": [_GHOST_RUNG]}, id="helper"),
    ],
)
def test_settings_rejects_a_ladder_agent_with_no_agents_table(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="ghost"):
        make_settings(**kwargs)
