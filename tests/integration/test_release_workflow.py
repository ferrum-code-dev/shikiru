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
DAILY_AT_MIDNIGHT_UTC = "0 0 * * *"
RELEASE_CONDITION = "needs.prepare.outputs.release == 'true'"
PINNED_USE = re.compile(r"uses: \S+@[0-9a-f]{40} # v\d+\.\d+\.\d+$")
NO_NEEDS: list[str] = []
PYPI_PUBLISH_ACTION = "pypa/gh-action-pypi-publish@"
DOWNLOAD_ACTION = "actions/download-artifact@"
UPLOAD_ACTION = "actions/upload-artifact@"
CLEAN_INSTALL_COMMAND = "uv run --isolated --no-project"
ANCESTOR_CHECK = "git merge-base --is-ancestor"


def load_workflow() -> dict[Any, Any]:
    return cast("dict[Any, Any]", yaml.safe_load(WORKFLOW.read_text(encoding="utf-8")))


def job_steps(job: str) -> list[dict[str, Any]]:
    return cast("list[dict[str, Any]]", load_workflow()["jobs"][job]["steps"])


def step_index(steps: list[dict[str, Any]], marker: str) -> int:
    (index,) = [
        position
        for position, step in enumerate(steps)
        if marker in str(step.get("uses", "")) or marker in str(step.get("run", ""))
    ]
    return index


def test_the_workflow_runs_daily_and_on_the_button() -> None:
    triggers = load_workflow()[YAML_BOOLEAN_KEY_FOR_ON]
    assert set(triggers) == {"schedule", "workflow_dispatch"}
    assert triggers["schedule"] == [{"cron": DAILY_AT_MIDNIGHT_UTC}]


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
        ("pypi", ["prepare", "build"]),
        ("github", ["prepare", "pypi"]),
    ],
)
def test_each_job_waits_for_the_jobs_before_it(job: str, needs: list[str]) -> None:
    declared = load_workflow()["jobs"][job].get("needs", NO_NEEDS)
    assert ([declared] if isinstance(declared, str) else declared) == needs


@pytest.mark.parametrize("job", ["build", "pypi", "github"])
def test_the_publishing_jobs_run_only_when_prepare_proposes_a_release(job: str) -> None:
    assert load_workflow()["jobs"][job]["if"] == RELEASE_CONDITION


def test_the_github_release_is_marked_latest_and_never_a_prerelease() -> None:
    commands = [
        str(step["run"])
        for job in load_workflow()["jobs"].values()
        for step in job["steps"]
        if "gh release create" in str(step.get("run", ""))
    ]
    assert len(commands) == 1
    assert "--latest" in commands[0]
    assert "--prerelease" not in commands[0]


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


def test_prepare_releases_only_commits_after_the_last_release() -> None:
    assert any(ANCESTOR_CHECK in str(step.get("run", "")) for step in job_steps("prepare"))


def test_the_built_wheel_is_installed_in_a_clean_environment_before_upload() -> None:
    steps = job_steps("build")
    assert step_index(steps, CLEAN_INSTALL_COMMAND) < step_index(steps, UPLOAD_ACTION)


def test_the_pypi_upload_skips_files_pypi_already_has() -> None:
    publish = next(
        step for step in job_steps("pypi") if PYPI_PUBLISH_ACTION in str(step.get("uses", ""))
    )
    assert publish["with"]["skip-existing"] is True


def test_the_github_release_downloads_and_attaches_no_files() -> None:
    steps = job_steps("github")
    assert not any(DOWNLOAD_ACTION in str(step.get("uses", "")) for step in steps)
    (release,) = [step for step in steps if "gh release create" in str(step.get("run", ""))]
    assert "dist/" not in str(release["run"])
