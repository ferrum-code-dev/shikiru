from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from shikiru.cli.report import (
    REPORT_EXIT_INVALID,
    REPORT_EXIT_NO_CURRENT_TURN,
    ReportError,
    resolve_current_turn_dir,
    validate_report,
    write_report,
)
from tests.support.factories import make_verdict

if TYPE_CHECKING:
    from pathlib import Path

_ROLE = "leader"


def _turn_dir(tmp_path: Path, *, kind: str = "verdict") -> Path:
    run_dir = tmp_path / "runs" / "T-1"
    turn_dir = run_dir / "turns" / "03-verify-i1"
    turn_dir.mkdir(parents=True)
    (turn_dir / "report.kind").write_text(kind)
    role_dir = run_dir / _ROLE
    role_dir.mkdir(parents=True)
    (role_dir / "current_turn").symlink_to(turn_dir, target_is_directory=True)
    return turn_dir


def test_resolve_current_turn_dir_follows_the_symlink(tmp_path: Path) -> None:
    turn_dir = _turn_dir(tmp_path)
    run_dir = tmp_path / "runs" / "T-1"

    resolved = resolve_current_turn_dir({"SHIKIRU_RUN_DIR": str(run_dir), "SHIKIRU_ROLE": _ROLE})

    assert resolved == turn_dir.resolve()


def test_resolve_current_turn_dir_requires_both_env_vars(tmp_path: Path) -> None:
    with pytest.raises(ReportError) as excinfo:
        resolve_current_turn_dir({"SHIKIRU_RUN_DIR": str(tmp_path)})

    assert excinfo.value.exit_code == REPORT_EXIT_NO_CURRENT_TURN


def test_resolve_current_turn_dir_rejects_a_dangling_symlink(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "T-1"
    role_dir = run_dir / _ROLE
    role_dir.mkdir(parents=True)
    (role_dir / "current_turn").symlink_to(run_dir / "turns" / "missing")

    with pytest.raises(ReportError) as excinfo:
        resolve_current_turn_dir({"SHIKIRU_RUN_DIR": str(run_dir), "SHIKIRU_ROLE": _ROLE})

    assert excinfo.value.exit_code == REPORT_EXIT_NO_CURRENT_TURN


def test_validate_report_rejects_a_missing_report_kind_file(tmp_path: Path) -> None:
    turn_dir = tmp_path / "turn"
    turn_dir.mkdir()
    file_path = tmp_path / "report.json"
    file_path.write_text("{}")

    with pytest.raises(ReportError) as excinfo:
        validate_report(turn_dir, file_path)

    assert excinfo.value.exit_code == REPORT_EXIT_INVALID
    assert "report.kind" in excinfo.value.message


def test_validate_report_rejects_an_unreadable_file(tmp_path: Path) -> None:
    turn_dir = _turn_dir(tmp_path)

    with pytest.raises(ReportError) as excinfo:
        validate_report(turn_dir, tmp_path / "does-not-exist.json")

    assert excinfo.value.exit_code == REPORT_EXIT_INVALID
    assert "cannot read" in excinfo.value.message


def test_write_report_replaces_an_existing_report_json(tmp_path: Path) -> None:
    turn_dir = _turn_dir(tmp_path)
    (turn_dir / "report.json").write_text("stale")
    report = make_verdict()

    report_path = write_report(turn_dir, report)

    assert report_path == turn_dir / "report.json"
    assert report.model_dump_json(indent=2) == report_path.read_text()


def test_write_report_cleans_up_its_temp_file_when_the_replace_fails(tmp_path: Path) -> None:
    turn_dir = _turn_dir(tmp_path)
    (turn_dir / "report.json").mkdir()

    with pytest.raises(OSError, match=r"report\.json"):
        write_report(turn_dir, make_verdict())

    assert list(turn_dir.glob(".report.json.*")) == []
