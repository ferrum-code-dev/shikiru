from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from shikiru.agents.contracts import Rung
from shikiru.config.loader import load_settings
from shikiru.config.settings import ComplexityLadder, Settings
from tests.support.config import write_minimal_project
from tests.support.factories import HELPER_RUNG, make_settings

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


def test_packaged_defaults_validate_together_with_a_minimal_project_file(tmp_path: Path) -> None:
    write_minimal_project(tmp_path)

    settings = load_settings(tmp_path)

    assert settings.project.name == "demo"


_GHOST_RUNG = Rung(agent="ghost", model="m", effort=None)


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(lambda: make_settings(leader_ladder=[_GHOST_RUNG]), id="leader"),
        pytest.param(lambda: make_settings(implementer_ladder=[_GHOST_RUNG]), id="implementer"),
        pytest.param(
            lambda: make_settings(
                implementer_complexity={"high": ComplexityLadder(ladder=[_GHOST_RUNG])}
            ),
            id="implementer-complexity-override",
        ),
        pytest.param(lambda: make_settings(helper_ladder=[_GHOST_RUNG]), id="helper"),
    ],
)
def test_settings_rejects_a_ladder_agent_with_no_agents_table(
    build: Callable[[], Settings],
) -> None:
    with pytest.raises(ValidationError):
        build()


def test_settings_rejects_multiple_helper_rungs() -> None:
    with pytest.raises(ValidationError):
        make_settings(helper_ladder=[HELPER_RUNG, Rung(agent="codex", model="sol", effort="high")])
