"""The release workflow publishes after one CI check, keeps full history and pins its actions."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "release.yml"
CHECKOUT_ACTION = "actions/checkout@"
PINNED_USE = re.compile(r"uses: \S+@[0-9a-f]{40} # v\d+\.\d+\.\d+$")
NO_NEEDS: list[str] = []


def load_workflow() -> dict[str, Any]:
    return cast("dict[str, Any]", yaml.safe_load(WORKFLOW.read_text(encoding="utf-8")))


def test_every_checkout_keeps_full_history_for_the_tag_checks_and_notes() -> None:
    steps = [step for job in load_workflow()["jobs"].values() for step in job["steps"]]
    checkouts = [step for step in steps if str(step.get("uses", "")).startswith(CHECKOUT_ACTION)]
    assert checkouts
    assert all(step["with"]["fetch-depth"] == 0 for step in checkouts)


@pytest.mark.parametrize(
    ("job", "needs"),
    [
        ("check", NO_NEEDS),
        ("build", ["check"]),
        ("pypi", ["build"]),
        ("github", ["pypi"]),
    ],
)
def test_each_job_waits_for_the_one_before_it(job: str, needs: list[str]) -> None:
    declared = load_workflow()["jobs"][job].get("needs", NO_NEEDS)
    assert ([declared] if isinstance(declared, str) else declared) == needs


@pytest.mark.parametrize(
    ("job", "permissions"),
    [
        ("check", {"contents": "read", "actions": "read"}),
        ("build", {}),
        ("pypi", {"id-token": "write"}),
        ("github", {"contents": "write"}),
    ],
)
def test_each_job_gets_only_the_token_scopes_it_needs(
    job: str, permissions: dict[str, str]
) -> None:
    assert load_workflow()["jobs"][job].get("permissions", {}) == permissions


def test_every_action_is_pinned_to_a_full_sha_with_its_version() -> None:
    uses_lines = [
        line for line in WORKFLOW.read_text(encoding="utf-8").splitlines() if "uses:" in line
    ]
    assert uses_lines
    assert all(PINNED_USE.search(line.strip()) for line in uses_lines)
