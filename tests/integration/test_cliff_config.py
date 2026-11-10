"""git-cliff reads cliff.toml, and only final version tags count as release tags."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable

CLIFF_CONFIG = Path(__file__).resolve().parents[2] / "cliff.toml"
BREAKING_GROUP = "Breaking changes"
CATCH_ALL_MESSAGE = ".*"


def load_config() -> dict[str, Any]:
    return tomllib.loads(CLIFF_CONFIG.read_text(encoding="utf-8"))


def tag_pattern() -> str:
    return cast("str", load_config()["git"]["tag_pattern"])


def commit_parser_indexes(predicate: Callable[[dict[str, Any]], bool]) -> list[int]:
    parsers = cast("list[dict[str, Any]]", load_config()["git"]["commit_parsers"])
    return [index for index, parser in enumerate(parsers) if predicate(parser)]


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


def test_docs_only_commits_are_skipped_after_every_breaking_change_parser() -> None:
    breaking = commit_parser_indexes(lambda parser: parser.get("group") == BREAKING_GROUP)
    (skipped,) = commit_parser_indexes(lambda parser: parser.get("skip") is True)
    (catch_all,) = commit_parser_indexes(lambda parser: parser.get("message") == CATCH_ALL_MESSAGE)
    assert max(breaking) < skipped < catch_all
