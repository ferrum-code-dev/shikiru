"""The next-version script prints the proposal from a real repository and exits by outcome."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from shikiru.process import SubprocessRunner
from tests.support.release_history import Tag, build_history

if TYPE_CHECKING:
    from collections.abc import Sequence

    from tests.support.release_history import Step

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPOSITORY_ROOT / "scripts" / "next_version.py"
HEAD = "HEAD"
COMMIT_OPTION = "--commit"
SCRIPT_TIMEOUT_SECONDS = 30.0
EXIT_SUCCESS = 0
EXIT_FAILURE = 1
EXIT_NOTHING_TO_RELEASE = 2


@dataclass(frozen=True)
class Case:
    steps: Sequence[Step]
    commit_subject: str | None
    arguments: tuple[str, ...]
    status: int
    stdout: str
    stderr: str


CLI_ROWS = [
    pytest.param(
        Case(
            [Tag("v0.1.0"), "fix: a fix"],
            None,
            (),
            EXIT_SUCCESS,
            "0.1.1\n",
            "",
        ),
        id="patch-for-a-fix",
    ),
    pytest.param(
        Case(
            ["feat: start"],
            None,
            (),
            EXIT_SUCCESS,
            "0.0.1\n",
            "",
        ),
        id="no-tag-starts-from-zero",
    ),
    pytest.param(
        Case(
            [Tag("v0.1.0"), "docs: explain"],
            None,
            (),
            EXIT_NOTHING_TO_RELEASE,
            "",
            "nothing to release",
        ),
        id="nothing-releasable",
    ),
    pytest.param(
        Case(
            [Tag("v0.1.0"), "fix: a fix"],
            None,
            ("--version", "0.3.0"),
            EXIT_SUCCESS,
            "0.3.0\n",
            "",
        ),
        id="version-replaces-the-proposal",
    ),
    pytest.param(
        Case(
            [Tag("v0.1.0"), "fix: a fix"],
            None,
            ("--version", "0.1.0"),
            EXIT_FAILURE,
            "",
            "not higher than the last release 0.1.0",
        ),
        id="version-not-higher-than-last-release",
    ),
    pytest.param(
        Case(
            [Tag("v0.1.0"), "fix: a fix", "feat: after", Tag("v0.2.0")],
            "fix: a fix",
            ("--version", "0.2.0"),
            EXIT_FAILURE,
            "",
            "tag v0.2.0 already exists",
        ),
        id="version-tag-already-exists",
    ),
    pytest.param(
        Case(
            [Tag("v0.1.0"), "fix: before", "feat: after", Tag("v0.2.0")],
            "fix: before",
            (),
            EXIT_SUCCESS,
            "0.1.1\n",
            "",
        ),
        id="only-tags-reachable-from-the-commit-count",
    ),
    pytest.param(
        Case(
            [Tag("v0.1.0"), "fix: a fix"],
            None,
            ("--version", "0.2"),
            EXIT_FAILURE,
            "",
            "is not <major>.<minor>.<patch>",
        ),
        id="malformed-version",
    ),
    pytest.param(
        Case(
            [Tag("v0.1.0"), "fix: a fix"],
            None,
            ("--version", "01.2.3"),
            EXIT_FAILURE,
            "",
            "leading zero in a component",
        ),
        id="leading-zero-version-is-rejected-not-normalized",
    ),
    pytest.param(
        Case(
            [Tag("v0.1.0"), "fix: a fix"],
            None,
            ("--version", "0.10.0"),
            EXIT_SUCCESS,
            "0.10.0\n",
            "",
        ),
        id="zero-and-multi-digit-components-are-allowed",
    ),
]


@pytest.mark.parametrize("case", CLI_ROWS)
async def test_cli_prints_the_version_or_why_it_cannot_be_released(
    tmp_path: Path,
    case: Case,
) -> None:
    shas = await build_history(tmp_path, case.steps)
    commit = HEAD if case.commit_subject is None else shas[case.commit_subject]

    result = await SubprocessRunner().run(
        (sys.executable, str(SCRIPT), COMMIT_OPTION, commit, *case.arguments),
        timeout=SCRIPT_TIMEOUT_SECONDS,
        cwd=tmp_path,
    )

    assert result.returncode == case.status
    assert result.stdout == case.stdout
    assert case.stderr in result.stderr
