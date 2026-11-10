"""The release workflow starts from a button, runs its jobs in order and pins its actions."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "release.yml"
YAML_BOOLEAN_KEY_FOR_ON = True
CHECKOUT_ACTION = "actions/checkout@"
PINNED_USE = re.compile(r"uses: \S+@[0-9a-f]{40} # v\d+\.\d+\.\d+$")
NO_NEEDS: list[str] = []


def load_workflow() -> dict[Any, Any]:
    return cast("dict[Any, Any]", yaml.safe_load(WORKFLOW.read_text(encoding="utf-8")))


def test_the_workflow_starts_only_from_the_button() -> None:
    assert set(load_workflow()[YAML_BOOLEAN_KEY_FOR_ON]) == {"workflow_dispatch"}


def test_the_button_takes_an_optional_version() -> None:
    version = load_workflow()[YAML_BOOLEAN_KEY_FOR_ON]["workflow_dispatch"]["inputs"]["version"]
    assert version["type"] == "string"
    assert version["required"] is False


def test_releases_are_serialized_and_never_cancelled() -> None:
    assert load_workflow()["concurrency"] == {"group": "release", "cancel-in-progress": False}


def test_the_workflow_grants_no_token_scopes_by_default() -> None:
    assert load_workflow()["permissions"] == {}


def test_every_checkout_keeps_full_history_for_the_tag_checks_and_notes() -> None:
    steps = [step for job in load_workflow()["jobs"].values() for step in job["steps"]]
    checkouts = [step for step in steps if str(step.get("uses", "")).startswith(CHECKOUT_ACTION)]
    assert checkouts
    assert all(step["with"]["fetch-depth"] == 0 for step in checkouts)


@pytest.mark.parametrize(
    ("job", "needs"),
    [
        ("prepare", NO_NEEDS),
        ("build", ["prepare"]),
        ("pypi", ["build"]),
        ("github", ["prepare", "pypi"]),
    ],
)
def test_each_job_waits_for_the_one_before_it(job: str, needs: list[str]) -> None:
    declared = load_workflow()["jobs"][job].get("needs", NO_NEEDS)
    assert ([declared] if isinstance(declared, str) else declared) == needs


@pytest.mark.parametrize(
    ("job", "permissions"),
    [
        ("prepare", {"contents": "read", "actions": "read"}),
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
