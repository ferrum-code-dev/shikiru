from __future__ import annotations

import asyncio
import importlib.util
import json
import platform
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

from shikiru.process import SubprocessRunner
from tests.support.clock import FakeClock
from tests.support.e2e_provenance import source_state
from tests.support.git_repo import commit_all, init_repo

if TYPE_CHECKING:
    from types import ModuleType

    import pytest

_START = datetime(2030, 1, 2, 3, 4, 5, tzinfo=UTC)
_RUN_DURATION = timedelta(minutes=25)
_SCENARIO = "tests/e2e/test_real_agents.py::test_e1"


def _e2e_plugin() -> ModuleType:
    path = Path(__file__).parents[1] / "e2e" / "conftest.py"
    spec = importlib.util.spec_from_file_location("shikiru_e2e_conftest", path)
    if spec is None or spec.loader is None:
        message = f"could not load E2E pytest hooks from {path}"
        raise AssertionError(message)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _RealE2EItem:
    nodeid = _SCENARIO

    def get_closest_marker(self, name: str) -> object:
        return object() if name == "real_e2e" else None


def _git_head(root: Path) -> str:
    result = asyncio.run(
        SubprocessRunner().run(["git", "rev-parse", "HEAD"], timeout=30, cwd=root, check=True)
    )
    return result.stdout.strip()


def test_session_without_real_e2e_run_preserves_existing_results(tmp_path: Path) -> None:
    plugin = _e2e_plugin()
    plugin_state = cast("Any", plugin)
    plugin_state.RESULTS_DIRECTORY = tmp_path
    plugin_state.CLOCK = FakeClock(_START)
    result_path = tmp_path / f"2030-01-02{plugin.RESULTS_FILENAME_SUFFIX}"
    original = '{"scenarios":[{"scenario":"live run"}]}\n'
    result_path.write_text(original)

    session = SimpleNamespace(config=SimpleNamespace())
    plugin.pytest_sessionfinish(cast("Any", session), 0)

    assert result_path.read_text() == original


def test_results_record_the_source_the_tools_and_the_invocation_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SHIKIRU_E2E", "1")
    plugin = _e2e_plugin()
    clock = FakeClock(_START)
    plugin_state = cast("Any", plugin)
    plugin_state.RESULTS_DIRECTORY = tmp_path
    plugin_state.CLOCK = clock
    plugin.pytest_collection_modifyitems(SimpleNamespace(), [_RealE2EItem()])
    report = SimpleNamespace(
        nodeid=_SCENARIO, when="call", passed=True, failed=False, skipped=False
    )
    plugin.pytest_runtest_logreport(report)
    clock.advance(_RUN_DURATION.total_seconds())

    plugin.pytest_sessionfinish(cast("Any", SimpleNamespace(config=SimpleNamespace())), 0)

    written = json.loads((tmp_path / f"2030-01-02{plugin.RESULTS_FILENAME_SUFFIX}").read_text())
    assert written["started_at"] == _START.isoformat()
    assert written["finished_at"] == (_START + _RUN_DURATION).isoformat()
    assert written["source_commit"] == _git_head(Path(__file__).parents[2])
    assert isinstance(written["source_dirty"], bool)
    versions = written["tool_versions"]
    assert versions["python"] == platform.python_version()
    assert versions["git"].startswith("git version")
    assert set(versions) == {"python", "claude", "codex", "tmux", "git"}


def test_source_state_reports_uncommitted_changes(tmp_path: Path) -> None:
    async def scenario() -> tuple[dict[str, object], dict[str, object]]:
        runner = SubprocessRunner()
        await init_repo(runner, tmp_path)
        (tmp_path / "tracked.txt").write_text("one\n")
        await commit_all(runner, tmp_path, "initial")
        clean = await source_state(tmp_path)
        (tmp_path / "tracked.txt").write_text("two\n")
        return clean, await source_state(tmp_path)

    clean, dirty = asyncio.run(scenario())

    assert clean["source_dirty"] is False
    assert dirty["source_dirty"] is True
    assert clean["source_commit"] == dirty["source_commit"]
