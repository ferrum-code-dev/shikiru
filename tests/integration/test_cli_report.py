from __future__ import annotations

import json
import os
import sys
from typing import TYPE_CHECKING

import pytest

from shikiru.cli.report import REPORT_EXIT_INVALID, REPORT_EXIT_NO_CURRENT_TURN
from shikiru.process import SubprocessRunner

if TYPE_CHECKING:
    from pathlib import Path

_ROLE = "leader"


def _make_turn_dir(tmp_path: Path, *, kind: str) -> tuple[Path, Path]:
    run_dir = tmp_path / "runs" / "T-1"
    turn_dir = run_dir / "turns" / "03-verify-i1"
    turn_dir.mkdir(parents=True)
    (turn_dir / "report.kind").write_text(kind)
    role_dir = run_dir / _ROLE
    role_dir.mkdir(parents=True)
    (role_dir / "current_turn").symlink_to(turn_dir, target_is_directory=True)
    return run_dir, turn_dir


def _env(run_dir: Path) -> dict[str, str]:
    return {**os.environ, "SHIKIRU_RUN_DIR": str(run_dir), "SHIKIRU_ROLE": _ROLE}


async def _run_report(env: dict[str, str], file_path: Path) -> tuple[int, str, str]:
    runner = SubprocessRunner()
    result = await runner.run(
        [sys.executable, "-m", "shikiru", "report", "--file", str(file_path)],
        timeout=10,
        env=env,
    )
    return result.returncode, result.stdout, result.stderr


@pytest.mark.asyncio
async def test_a_valid_report_is_written_atomically_and_exits_zero(tmp_path: Path) -> None:
    run_dir, turn_dir = _make_turn_dir(tmp_path, kind="verdict")
    payload = {"decision": "accept", "summary": "Looks good"}
    file_path = tmp_path / "report.json"
    file_path.write_text(json.dumps(payload))

    returncode, stdout, stderr = await _run_report(_env(run_dir), file_path)

    assert returncode == 0
    assert "report accepted" in stdout
    assert stderr == ""
    written = json.loads((turn_dir / "report.json").read_text())
    assert written["decision"] == "accept"
    assert written["summary"] == "Looks good"
    assert written["issues"] == []


@pytest.mark.asyncio
async def test_an_invalid_report_exits_two_and_names_the_offending_field(tmp_path: Path) -> None:
    run_dir, turn_dir = _make_turn_dir(tmp_path, kind="verdict")
    payload: dict[str, object] = {
        "decision": "changes_requested",
        "summary": "Needs work",
        "issues": [],
    }
    file_path = tmp_path / "report.json"
    file_path.write_text(json.dumps(payload))

    returncode, _, stderr = await _run_report(_env(run_dir), file_path)

    assert returncode == REPORT_EXIT_INVALID
    assert "issues" in stderr
    assert not (turn_dir / "report.json").exists()


@pytest.mark.asyncio
async def test_malformed_json_exits_two(tmp_path: Path) -> None:
    run_dir, _ = _make_turn_dir(tmp_path, kind="verdict")
    file_path = tmp_path / "report.json"
    file_path.write_text("{not json")

    returncode, _, stderr = await _run_report(_env(run_dir), file_path)

    assert returncode == REPORT_EXIT_INVALID
    assert "invalid JSON" in stderr


@pytest.mark.asyncio
async def test_missing_run_dir_env_var_exits_three(tmp_path: Path) -> None:
    file_path = tmp_path / "report.json"
    file_path.write_text("{}")
    env = {**os.environ}
    env.pop("SHIKIRU_RUN_DIR", None)
    env.pop("SHIKIRU_ROLE", None)

    returncode, _, stderr = await _run_report(env, file_path)

    assert returncode == REPORT_EXIT_NO_CURRENT_TURN
    assert "SHIKIRU_RUN_DIR" in stderr


@pytest.mark.asyncio
async def test_dangling_current_turn_symlink_exits_three(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "T-1"
    role_dir = run_dir / _ROLE
    role_dir.mkdir(parents=True)
    (role_dir / "current_turn").symlink_to(run_dir / "turns" / "missing")
    file_path = tmp_path / "report.json"
    file_path.write_text("{}")

    returncode, _, stderr = await _run_report(_env(run_dir), file_path)

    assert returncode == REPORT_EXIT_NO_CURRENT_TURN
    assert "current turn" in stderr


@pytest.mark.asyncio
async def test_unknown_report_kind_exits_two(tmp_path: Path) -> None:
    run_dir, _ = _make_turn_dir(tmp_path, kind="not-a-real-kind")
    file_path = tmp_path / "report.json"
    file_path.write_text("{}")

    returncode, _, stderr = await _run_report(_env(run_dir), file_path)

    assert returncode == REPORT_EXIT_INVALID
    assert "not-a-real-kind" in stderr


@pytest.mark.asyncio
async def test_running_report_twice_replaces_the_stale_report_json(tmp_path: Path) -> None:
    run_dir, turn_dir = _make_turn_dir(tmp_path, kind="verdict")
    (turn_dir / "report.json").write_text("stale")
    payload = {"decision": "accept", "summary": "Replaces the stale report"}
    file_path = tmp_path / "report.json"
    file_path.write_text(json.dumps(payload))

    returncode, _, _ = await _run_report(_env(run_dir), file_path)

    assert returncode == 0
    written = json.loads((turn_dir / "report.json").read_text())
    assert written["summary"] == "Replaces the stale report"
