from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

if TYPE_CHECKING:
    from _pytest.config import Config
    from _pytest.main import Session
    from _pytest.nodes import Item
    from _pytest.reports import TestReport


RESULTS_DIRECTORY = Path(__file__).parents[2] / "build" / "e2e"
RESULTS_DATE_FORMAT = "%Y-%m-%d"
RESULTS_FILENAME_SUFFIX = ".json"
RESULTS_TEMP_SUFFIX = ".tmp"
RESULTS_SCHEMA_VERSION = 1
RESULTS_STATUS_PASSED = "passed"
RESULTS_STATUS_FAILED = "failed"
RESULTS_STATUS_SKIPPED = "skipped"
REPORT_PHASE_CALL = "call"
REPORT_PHASE_SETUP = "setup"
DEFAULT_COST_USD = 0.0
_COSTS: dict[str, float] = {}
_REPORTS: dict[str, TestReport] = {}


@pytest.fixture
def record_e2e_cost(request: pytest.FixtureRequest) -> object:
    """Return a callback that records one scenario's observed USD cost."""

    def record(cost_usd: float | None) -> None:
        node = cast("object", request.node)
        nodeid = cast("str", node.__getattribute__("nodeid"))
        _COSTS[nodeid] = _COSTS.get(nodeid, DEFAULT_COST_USD) + (
            DEFAULT_COST_USD if cost_usd is None else cost_usd
        )

    return record


def pytest_runtest_logreport(report: TestReport) -> None:
    """Collect completed scenario reports for the dated results artifact."""
    if report.when == REPORT_PHASE_CALL or (
        report.when == REPORT_PHASE_SETUP and (report.skipped or report.failed)
    ):
        _REPORTS[report.nodeid] = report


def pytest_collection_modifyitems(config: Config, items: list[Item]) -> None:
    """Skip real-agent scenarios unless the explicit E2E opt-in is present."""
    del config
    if os.environ.get("SHIKIRU_E2E") == "1":
        return
    skip = pytest.mark.skip(reason="set SHIKIRU_E2E=1 to run real-agent E2E")
    for item in items:
        if item.get_closest_marker("real_e2e") is not None:
            item.add_marker(skip)


def pytest_sessionfinish(session: Session, exitstatus: int) -> None:
    """Write one redaction-safe dated results file after the E2E session."""
    if _is_xdist_worker(session.config):
        return
    scenarios = [_scenario_result(nodeid, report) for nodeid, report in sorted(_REPORTS.items())]
    payload = {
        "schema_version": RESULTS_SCHEMA_VERSION,
        "date": datetime.now(UTC).date().strftime(RESULTS_DATE_FORMAT),
        "exit_status": exitstatus,
        "scenarios": scenarios,
    }
    RESULTS_DIRECTORY.mkdir(parents=True, exist_ok=True)
    filename = f"{payload['date']}{RESULTS_FILENAME_SUFFIX}"
    result_path = RESULTS_DIRECTORY / filename
    temporary_path = result_path.with_suffix(RESULTS_TEMP_SUFFIX)
    temporary_path.write_text(json.dumps(payload, indent=2) + "\n")
    temporary_path.replace(result_path)


def _scenario_result(nodeid: str, report: TestReport) -> dict[str, object]:
    status = _status_for(report)
    return {
        "scenario": nodeid,
        "status": status,
        "cost": _COSTS.get(nodeid, DEFAULT_COST_USD),
    }


def _status_for(report: TestReport) -> str:
    if report.passed:
        return RESULTS_STATUS_PASSED
    if report.skipped:
        return RESULTS_STATUS_SKIPPED
    return RESULTS_STATUS_FAILED


def _is_xdist_worker(config: Config) -> bool:
    return hasattr(config, "workerinput")
