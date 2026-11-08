"""The release workflow parses, keeps full history for its notes, and pins every action to a SHA."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, cast

import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "release.yml"
CHECKOUT_ACTION = "actions/checkout@"
PINNED_USE = re.compile(r"uses: \S+@[0-9a-f]{40} # v\d+\.\d+\.\d+$")


def load_workflow() -> dict[str, Any]:
    return cast("dict[str, Any]", yaml.safe_load(WORKFLOW.read_text(encoding="utf-8")))


def test_publish_job_checks_out_full_history_for_the_notes() -> None:
    steps: list[dict[str, Any]] = load_workflow()["jobs"]["publish"]["steps"]
    checkouts = [step for step in steps if str(step.get("uses", "")).startswith(CHECKOUT_ACTION)]
    assert checkouts
    assert all(step["with"]["fetch-depth"] == 0 for step in checkouts)


def test_every_action_is_pinned_to_a_full_sha_with_its_version() -> None:
    uses_lines = [
        line for line in WORKFLOW.read_text(encoding="utf-8").splitlines() if "uses:" in line
    ]
    assert uses_lines
    assert all(PINNED_USE.search(line.strip()) for line in uses_lines)
