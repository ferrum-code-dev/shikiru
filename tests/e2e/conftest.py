from __future__ import annotations

import asyncio
import json
import os
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from shikiru.clock import SystemClock
from shikiru.plugins.agents.codex.home import codex_home
from shikiru.plugins.agents.codex.options import WORKSPACE_WRITE_SANDBOX
from tests.support.e2e_provenance import source_state, tool_versions
from tests.support.project_dir import E2E_PROJECTS_PARENT

if TYPE_CHECKING:
    from datetime import datetime

    from _pytest.config import Config
    from _pytest.main import Session
    from _pytest.nodes import Item
    from _pytest.reports import TestReport


SOURCE_ROOT = Path(__file__).parents[2]
RESULTS_DIRECTORY = SOURCE_ROOT / "build" / "e2e"
CLOCK = SystemClock()
RESULTS_DATE_FORMAT = "%Y-%m-%d"
RESULTS_FILENAME_SUFFIX = ".json"
RESULTS_TEMP_SUFFIX = ".tmp"
RESULTS_SCHEMA_VERSION = 3
RESULTS_STATUS_PASSED = "passed"
RESULTS_STATUS_FAILED = "failed"
RESULTS_STATUS_SKIPPED = "skipped"
REPORT_PHASE_CALL = "call"
REPORT_PHASE_SETUP = "setup"
CODEX_CONFIG_FILENAME = "config.toml"
CODEX_ACCESS_SETTINGS = (
    "approvals_reviewer",
    "approval_policy",
    "default_permissions",
    "sandbox_mode",
)
_COSTS: dict[str, float | None] = {}
_REPORTS: dict[str, TestReport] = {}
_REAL_E2E_NODEIDS: set[str] = set()
_STARTED: list[datetime] = []


@pytest.fixture
def record_e2e_cost(request: pytest.FixtureRequest) -> object:
    """Return a callback that records one scenario's agent-reported USD cost; None stays unknown."""

    def record(cost_usd: float | None) -> None:
        node = cast("object", request.node)
        nodeid = cast("str", node.__getattribute__("nodeid"))
        _COSTS[nodeid] = _add_cost(_COSTS.get(nodeid), cost_usd)

    return record


def pytest_runtest_logreport(report: TestReport) -> None:
    """Collect completed scenario reports for the dated results artifact."""
    if report.nodeid in _REAL_E2E_NODEIDS and (
        report.when == REPORT_PHASE_CALL or (report.when == REPORT_PHASE_SETUP and report.failed)
    ):
        _REPORTS[report.nodeid] = report


def pytest_collection_modifyitems(config: Config, items: list[Item]) -> None:
    """Skip real-agent scenarios unless the explicit E2E opt-in is present."""
    del config
    _COSTS.clear()
    _REPORTS.clear()
    _REAL_E2E_NODEIDS.clear()
    _STARTED[:] = [CLOCK.now()]
    _REAL_E2E_NODEIDS.update(
        item.nodeid for item in items if item.get_closest_marker("real_e2e") is not None
    )
    if os.environ.get("SHIKIRU_E2E") == "1":
        return
    skip = pytest.mark.skip(reason="set SHIKIRU_E2E=1 to run real-agent E2E")
    for item in items:
        if item.get_closest_marker("real_e2e") is not None:
            item.add_marker(skip)


def pytest_sessionfinish(session: Session, exitstatus: int) -> None:
    """Write one redaction-safe dated results file after the E2E session."""
    if _is_xdist_worker(session.config) or not _REPORTS:
        return
    scenarios = [_scenario_result(nodeid, report) for nodeid, report in sorted(_REPORTS.items())]
    finished = CLOCK.now()
    payload = {
        "schema_version": RESULTS_SCHEMA_VERSION,
        "date": finished.date().strftime(RESULTS_DATE_FORMAT),
        "started_at": _STARTED[0].isoformat(),
        "finished_at": finished.isoformat(),
        **asyncio.run(source_state(SOURCE_ROOT)),
        "tool_versions": asyncio.run(tool_versions()),
        "sandbox": _sandbox_settings(),
        "exit_status": exitstatus,
        "scenarios": scenarios,
    }
    RESULTS_DIRECTORY.mkdir(parents=True, exist_ok=True)
    filename = f"{payload['date']}{RESULTS_FILENAME_SUFFIX}"
    result_path = RESULTS_DIRECTORY / filename
    temporary_path = result_path.with_suffix(RESULTS_TEMP_SUFFIX)
    temporary_path.write_text(json.dumps(payload, indent=2) + "\n")
    temporary_path.replace(result_path)


def _sandbox_settings() -> dict[str, object]:
    path = codex_home() / CODEX_CONFIG_FILENAME
    config = tomllib.loads(path.read_text()) if path.is_file() else {}
    return {
        "codex_sandbox_mode": WORKSPACE_WRITE_SANDBOX,
        "project_parent": str(E2E_PROJECTS_PARENT),
        "tmpdir": os.environ.get("TMPDIR"),
        "codex_config": {key: config[key] for key in CODEX_ACCESS_SETTINGS if key in config},
    }


def _add_cost(total: float | None, cost_usd: float | None) -> float | None:
    if total is None:
        return cost_usd
    if cost_usd is None:
        return total
    return total + cost_usd


def _scenario_result(nodeid: str, report: TestReport) -> dict[str, object]:
    status = _status_for(report)
    return {
        "scenario": nodeid,
        "status": status,
        "cost": _COSTS.get(nodeid),
    }


def _status_for(report: TestReport) -> str:
    if report.passed:
        return RESULTS_STATUS_PASSED
    if report.skipped:
        return RESULTS_STATUS_SKIPPED
    return RESULTS_STATUS_FAILED


def _is_xdist_worker(config: Config) -> bool:
    return hasattr(config, "workerinput")
