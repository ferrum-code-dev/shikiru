"""The Project usage popup carries one title and the approved Totals, By agent and Limits layout."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from textual.widgets import Static

from shikiru.plugins.widgets.usage import UsagePanel
from tests.support.clock import FakeClock
from tests.support.config import write_minimal_project
from tests.support.dashboard import choose_focus_view, dashboard_app
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.store.reader import StateReader
    from tests.support.store import Connection

_START = datetime(2026, 1, 1, tzinfo=UTC)
_WIDE = (100, 40)
_WAIT_SECONDS = 5
_HOME_USAGE_ENTRY = "Usage details"
_PROJECT_USAGE_TITLE = "Project usage"


async def test_usage_popup_title_is_the_single_project_usage_label(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    del connection
    project_dir = write_minimal_project(tmp_path)
    app = dashboard_app(reader, project_dir, FakeClock(_START), widget_names=["usage"])
    async with app.run_test(size=_WIDE) as pilot:
        await choose_focus_view(pilot, _HOME_USAGE_ENTRY)
        await wait_until(lambda: bool(app.screen.query(UsagePanel)), timeout=_WAIT_SECONDS)
        title = app.screen.query_one("#context-widget-title", Static)
        assert str(title.render()) == _PROJECT_USAGE_TITLE
