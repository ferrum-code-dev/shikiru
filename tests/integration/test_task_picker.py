from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from textual.app import App
from textual.widgets import Input, ListView

from shikiru.dashboard.task_picker import (
    PICKER_LIST,
    PICKER_SEARCH,
    PickerRow,
    TaskPickerScreen,
)
from shikiru.ui.theme import install_theme
from tests.support.clock import FakeClock
from tests.support.dashboard import enter_text
from tests.support.escalations import operator_services
from tests.support.store import seed_task
from tests.support.waiting import UI_WAIT_SECONDS, wait_until

if TYPE_CHECKING:
    from pathlib import Path

    from textual.pilot import Pilot

    from shikiru.operator.queries import OperatorQueries
    from shikiru.store.reader import StateReader
    from tests.support.store import Connection

_START = datetime(2026, 1, 1, tzinfo=UTC)
_APP_SIZE = (100, 30)
_FIRST_PAGE_COUNT = 20
_SECOND_PAGE_COUNT = 40
_TASK_COUNT = 47
_CURRENT = 3
_SELECTED = 5
_SEARCH_INTERVAL = 2
_SEARCH_MATCHES = tuple(range(1, _TASK_COUNT, _SEARCH_INTERVAL))
_MATCH_TITLE = "Zebra crossing"
_PENDING = "pending"


class _Host(App[None]):
    def __init__(self, queries: OperatorQueries, current_task_id: str) -> None:
        super().__init__()
        install_theme(self)
        self.picker = TaskPickerScreen(queries, current_task_id)
        self.result: str | None = _PENDING

    def on_mount(self) -> None:
        self.push_screen(self.picker, self._store)

    def _store(self, task_id: str | None) -> None:
        self.result = task_id


def _list(host: _Host) -> ListView:
    return host.picker.query_one(f"#{PICKER_LIST}", ListView)


def _ids(host: _Host) -> list[str]:
    return [cast("PickerRow", row).task_id for row in _list(host).children]


def _selected(host: _Host) -> str:
    return cast("PickerRow", _list(host).highlighted_child).task_id


async def _type_search(pilot: Pilot[None], host: _Host, text: str) -> None:
    await pilot.press("/")
    await wait_until(lambda: bool(host.picker.query(f"#{PICKER_SEARCH}")), UI_WAIT_SECONDS)
    await enter_text(pilot, text)


async def test_task_picker_searches_pages_and_chooses_a_task(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    clock = FakeClock(_START)
    tasks = [
        seed_task(
            connection,
            clock,
            title=_MATCH_TITLE if index in _SEARCH_MATCHES else f"Job {index}",
        )
        for index in range(_TASK_COUNT)
    ]
    ids = [task.id for task in tasks]
    _, queries, _ = operator_services(connection, reader, clock, tmp_path)
    host = _Host(queries, ids[_CURRENT])

    async with host.run_test(size=_APP_SIZE) as pilot:
        await wait_until(lambda: len(_list(host).children) == _FIRST_PAGE_COUNT, UI_WAIT_SECONDS)
        assert (_ids(host), _selected(host)) == (ids[:_FIRST_PAGE_COUNT], ids[_CURRENT])

        await pilot.press("down", "down")
        await wait_until(lambda: _selected(host) == ids[_SELECTED], UI_WAIT_SECONDS)
        await pilot.press("m")
        await wait_until(lambda: len(_list(host).children) == _SECOND_PAGE_COUNT, UI_WAIT_SECONDS)
        assert _ids(host) == ids[:_SECOND_PAGE_COUNT]
        assert _selected(host) == ids[_SELECTED]

        await pilot.press("m")
        await wait_until(lambda: _ids(host) == ids, UI_WAIT_SECONDS)
        assert _selected(host) == ids[_SELECTED]
        await pilot.press("m")
        assert _ids(host) == ids

        await pilot.press("end")
        await wait_until(lambda: _selected(host) == ids[-1], UI_WAIT_SECONDS)
        await pilot.press("home", "down")
        await wait_until(lambda: _selected(host) == ids[1], UI_WAIT_SECONDS)

        await _type_search(pilot, host, "zebra")
        expected_matches = [ids[index] for index in _SEARCH_MATCHES]
        await wait_until(
            lambda: _ids(host) == expected_matches[:_FIRST_PAGE_COUNT], UI_WAIT_SECONDS
        )
        assert _selected(host) == ids[_CURRENT]

        await pilot.press("enter")
        await wait_until(lambda: not host.picker.query(f"#{PICKER_SEARCH}"), UI_WAIT_SECONDS)
        await pilot.press("m")
        await wait_until(lambda: _ids(host) == expected_matches, UI_WAIT_SECONDS)
        assert _selected(host) == ids[_CURRENT]

        await pilot.press("/")
        await wait_until(lambda: bool(host.picker.query(f"#{PICKER_SEARCH}")), UI_WAIT_SECONDS)
        assert host.picker.query_one(f"#{PICKER_SEARCH}", Input).value == "zebra"
        assert (_ids(host), _selected(host)) == (expected_matches, ids[_CURRENT])
        await pilot.press("escape")
        await wait_until(lambda: _ids(host) == ids[:_FIRST_PAGE_COUNT], UI_WAIT_SECONDS)
        assert not host.picker.query(f"#{PICKER_SEARCH}")
        assert _selected(host) == ids[_CURRENT]
        assert host.result == _PENDING

        await pilot.press("escape")
        await wait_until(lambda: host.result is None, UI_WAIT_SECONDS)

    chooser = _Host(queries, ids[0])
    async with chooser.run_test(size=_APP_SIZE) as pilot:
        await wait_until(lambda: len(_list(chooser).children) == _FIRST_PAGE_COUNT, UI_WAIT_SECONDS)
        await _type_search(pilot, chooser, "zebra")
        await wait_until(lambda: len(_list(chooser).children) == _FIRST_PAGE_COUNT, UI_WAIT_SECONDS)
        await pilot.press("enter", "down", "enter")
        await wait_until(lambda: chooser.result == ids[_SEARCH_MATCHES[1]], UI_WAIT_SECONDS)
