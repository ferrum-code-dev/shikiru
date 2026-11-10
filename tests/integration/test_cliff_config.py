"""git-cliff reads cliff.toml, and only final version tags count as release tags."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import cast

import pytest

CLIFF_CONFIG = Path(__file__).resolve().parents[2] / "cliff.toml"


def tag_pattern() -> str:
    config = tomllib.loads(CLIFF_CONFIG.read_text(encoding="utf-8"))
    return cast("str", config["git"]["tag_pattern"])


@pytest.mark.parametrize(
    ("tag", "is_release"),
    [
        pytest.param("v1.2.3", True, id="final-release"),
        pytest.param("v0.10.0", True, id="multi-digit-components"),
        pytest.param("v1.2.3-rc.1", False, id="prerelease-is-not-a-release"),
        pytest.param("v1.2.3.4", False, id="four-components"),
        pytest.param("1.2.3", False, id="missing-v-prefix"),
        pytest.param("v1.2", False, id="missing-patch"),
    ],
)
def test_only_final_version_tags_are_release_tags(tag: str, is_release: bool) -> None:
    assert bool(re.search(tag_pattern(), tag)) is is_release
