from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from types import ModuleType


def _e2e_plugin() -> ModuleType:
    path = Path(__file__).parents[1] / "e2e" / "conftest.py"
    spec = importlib.util.spec_from_file_location("shikiru_e2e_conftest", path)
    if spec is None or spec.loader is None:
        message = f"could not load E2E pytest hooks from {path}"
        raise AssertionError(message)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_session_without_real_e2e_run_preserves_existing_results(tmp_path: Path) -> None:
    plugin = _e2e_plugin()
    plugin_state = cast("Any", plugin)
    plugin_state.RESULTS_DIRECTORY = tmp_path
    today = datetime.now(UTC).date().strftime(plugin.RESULTS_DATE_FORMAT)
    result_path = tmp_path / f"{today}{plugin.RESULTS_FILENAME_SUFFIX}"
    original = '{"scenarios":[{"scenario":"live run"}]}\n'
    result_path.write_text(original)

    session = SimpleNamespace(config=SimpleNamespace())
    plugin.pytest_sessionfinish(cast("Any", session), 0)

    assert result_path.read_text() == original
