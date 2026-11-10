"""The CI disk report runs around the quality gate and the package smoke and never fails the job."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, cast

import yaml

from shikiru.process import ProcessResult, SubprocessRunner

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
SCRIPT = ROOT / ".github" / "scripts" / "disk-usage.sh"
CHECK_JOB = "check"
DISK_REPORT_MARKER = "disk-usage.sh"
QUALITY_GATE_MARKER = "poe check"
PACKAGE_SMOKE_MARKER = "release-readiness-smoke.sh"
ALWAYS = "always()"
NEVER_FAILS_SUFFIX = "|| true"
MAXIMUM_LINES = 40
ENTRIES_PER_FOLDER = 12
BYTES_PER_ENTRY = 4096
SCRIPT_TIMEOUT_SECONDS = 60.0


def check_job() -> dict[str, Any]:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return cast("dict[str, Any]", workflow["jobs"][CHECK_JOB])


def check_steps() -> list[dict[str, Any]]:
    return cast("list[dict[str, Any]]", check_job()["steps"])


def disk_report_positions(steps: list[dict[str, Any]]) -> list[int]:
    return [
        position
        for position, step in enumerate(steps)
        if DISK_REPORT_MARKER in str(step.get("run", ""))
    ]


def position_of(steps: list[dict[str, Any]], marker: str) -> int:
    (position,) = [
        position for position, step in enumerate(steps) if marker in str(step.get("run", ""))
    ]
    return position


def make_folder(path: Path) -> Path:
    path.mkdir()
    for index in range(ENTRIES_PER_FOLDER):
        (path / f"entry-{index}").write_bytes(b"x" * BYTES_PER_ENTRY * (index + 1))
    return path


async def run_report(environment: dict[str, str]) -> ProcessResult:
    return await SubprocessRunner().run(
        ("bash", str(SCRIPT)),
        timeout=SCRIPT_TIMEOUT_SECONDS,
        env={"PATH": os.environ["PATH"], **environment},
    )


def test_the_disk_report_runs_before_the_gate_after_it_and_after_the_smoke() -> None:
    steps = check_steps()
    before_gate, after_gate, after_smoke = disk_report_positions(steps)
    assert before_gate < position_of(steps, QUALITY_GATE_MARKER) < after_gate
    assert after_gate < position_of(steps, PACKAGE_SMOKE_MARKER) < after_smoke


def test_the_disk_reports_run_on_ubuntu_and_macos() -> None:
    operating_systems = check_job()["strategy"]["matrix"]["os"]
    assert {"ubuntu-24.04", "macos-15"} <= set(operating_systems)


def test_the_reports_after_the_gate_and_the_smoke_run_even_when_those_failed() -> None:
    steps = check_steps()
    _, after_gate, after_smoke = disk_report_positions(steps)
    assert steps[after_gate]["if"] == ALWAYS
    assert steps[after_smoke]["if"] == ALWAYS


def test_no_disk_report_is_skipped_on_either_operating_system() -> None:
    steps = check_steps()
    conditions = {steps[position].get("if") for position in disk_report_positions(steps)}
    assert conditions <= {None, ALWAYS}


def test_every_disk_report_step_is_guarded_so_it_cannot_fail_the_job() -> None:
    steps = check_steps()
    commands = [str(steps[position]["run"]) for position in disk_report_positions(steps)]
    assert all(command.endswith(NEVER_FAILS_SUFFIX) for command in commands)


async def test_the_report_exits_successfully_when_its_folders_are_missing(tmp_path: Path) -> None:
    result = await run_report(
        {"HOME": str(tmp_path / "missing-home"), "TMPDIR": str(tmp_path / "missing-temp")}
    )
    assert result.returncode == 0


async def test_the_report_lists_the_largest_entries_of_a_folder_first(tmp_path: Path) -> None:
    home = make_folder(tmp_path / "home")
    result = await run_report(
        {
            "HOME": str(home),
            "TMPDIR": str(make_folder(tmp_path / "temp")),
            "RUNNER_TEMP": str(make_folder(tmp_path / "runner")),
        }
    )
    home_lines = [line for line in result.stdout.splitlines() if str(home) in line]
    assert home_lines[0].endswith(str(home / f"entry-{ENTRIES_PER_FOLDER - 1}"))
    assert str(home / "entry-0") not in result.stdout


async def test_the_report_prints_at_most_forty_lines_when_every_folder_is_full(
    tmp_path: Path,
) -> None:
    result = await run_report(
        {
            "HOME": str(make_folder(tmp_path / "home")),
            "TMPDIR": str(make_folder(tmp_path / "temp")),
            "RUNNER_TEMP": str(make_folder(tmp_path / "runner")),
        }
    )
    assert result.returncode == 0
    assert len(result.stdout.splitlines()) <= MAXIMUM_LINES
