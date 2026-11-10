from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

import pytest
from rich.cells import cell_len
from textual.widgets import Static

from shikiru.dashboard.contract import DiagnosticSampleState, RepoStatus
from shikiru.domain.events import EventKind
from shikiru.domain.scheduling import WaitReason
from shikiru.domain.steps import Step, Transition, first_step
from shikiru.domain.tasks import EscalationReason, Hold, Mode, Status
from shikiru.plugins.widgets.header import HeaderPanel, HeaderWidget
from shikiru.plugins.widgets.queue import QueuePanel, QueueWidget
from shikiru.plugins.widgets.strips import StripsPanel, StripsWidget
from shikiru.store.escalations import EscalationStore
from shikiru.store.events import EventLog
from shikiru.store.pushes import PushTargets
from shikiru.store.tasks import TaskStore
from shikiru.ui.tokens import (
    ALERT_COLOR,
    PRIMARY_TEXT_COLOR,
    SAGE,
    SECONDARY_TEXT_COLOR,
)
from tests.support.cells import foregrounds, rendered_lines, styles
from tests.support.clock import FakeClock
from tests.support.config import write_minimal_project
from tests.support.dashboard import StubServices, WidgetHostApp, state_views, widget_context
from tests.support.render import plain_text
from tests.support.store import seed_task
from tests.support.waiting import SHORT_WAIT_SECONDS, UI_WAIT_SECONDS, wait_until

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.store.reader import StateReader
    from tests.support.store import Connection


START = datetime(2026, 1, 1, tzinfo=UTC)
BRAND = "SHIKIRU"
PUSH_INTERVAL_SECONDS = 3600


def _plain(content: object) -> str:
    return plain_text(content)


def _publishing_checkout(
    project_dir: Path, *, unpushed: int | None, unpulled: int | None
) -> RepoStatus:
    return RepoStatus(
        branch="main",
        ahead=0,
        behind=0,
        dirty=0,
        root=str(project_dir.resolve()),
        added_lines=0,
        deleted_lines=0,
        binary_entries=0,
        untracked_paths=0,
        base="main",
        unpushed=unpushed,
        unpulled=unpulled,
    )


def _submit_task(
    connection: Connection, clock: FakeClock, *, mode: Mode | None = Mode.IMPLEMENT
) -> str:
    task = seed_task(connection, clock, title="Add retry", mode=mode)
    store = TaskStore(connection, clock)
    store.set_slot(task.id, 0)
    store.apply_transition(task.id, first_step(task))
    return task.id


@pytest.mark.usefixtures("connection")
async def test_header_labels_the_squash_integration_mode(
    reader: StateReader, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path, '\n[orchestration]\nintegration = "squash"\n')
    context = widget_context(
        reader, project_dir, FakeClock(START), services=StubServices(engine_running=True)
    )
    widget = cast("HeaderPanel", HeaderWidget().create(context))
    async with WidgetHostApp(widget).run_test() as pilot:
        await pilot.pause()
        assert "Local squash and merge" in _plain(widget.content)


async def test_header_shows_project_name_engine_state_and_slots(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    _submit_task(connection, clock)

    context = widget_context(
        reader, project_dir, FakeClock(START), services=StubServices(engine_running=True)
    )
    widget = cast("HeaderPanel", HeaderWidget().create(context))
    async with WidgetHostApp(widget).run_test() as pilot:
        await pilot.pause()
        text = _plain(widget.content)
        assert "demo" in text
        assert "Engine running · Uptime unavailable" in text
        assert "Running 1 · Waiting 0" in text
        assert "Slots 1/2" in text
        assert "Local merge" in text
        assert "line counts unavailable" in text
        assert "binary count unavailable" in text
        assert "untracked count unavailable" in text
        assert "+1/-0" not in text
        assert "2 dirty" not in text


async def test_header_preserves_stale_and_unavailable_checkout_facts(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    del connection
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    cases = (
        (
            RepoStatus(
                branch="main",
                ahead=0,
                behind=0,
                dirty=0,
                sampled_ago="0:00:05",
                root=str(project_dir.resolve()),
                added_lines=0,
                deleted_lines=0,
                state=DiagnosticSampleState.STALE,
                error="git temporarily unavailable",
            ),
            (
                "main (previously clean)",
                "Checkout stale · sampled 0:00:05",
                "Checkout read failed · git temporarily unavailable",
            ),
        ),
        (
            RepoStatus(
                branch=None,
                ahead=None,
                behind=None,
                dirty=None,
                root=str(project_dir.resolve()),
                state=DiagnosticSampleState.FAILED,
                error="not a repository",
            ),
            (
                "branch unavailable",
                "Checkout unavailable · not a repository",
            ),
        ),
    )

    for repo_status, expected_facts in cases:
        context = widget_context(
            reader,
            project_dir,
            clock,
            services=StubServices(repo_status_value=repo_status),
        )
        widget = cast("HeaderPanel", HeaderWidget().create(context))
        async with WidgetHostApp(widget).run_test() as pilot:
            await pilot.pause()
            rendered = _plain(widget.content)
            assert re.search(r"SHIKIRU v\d+\.\d+\.\d+.* · demo", rendered)
            assert str(project_dir.resolve()) in rendered
            assert all(fact in rendered for fact in expected_facts)


@pytest.mark.usefixtures("colour_terminal")
async def test_header_colours_the_dirty_line_counts_green_and_red(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    del connection
    project_dir = write_minimal_project(tmp_path)
    repo = RepoStatus(
        branch="main",
        ahead=0,
        behind=0,
        dirty=2,
        root=str(project_dir.resolve()),
        added_lines=100,
        deleted_lines=20,
        binary_entries=0,
        untracked_paths=0,
    )
    context = widget_context(
        reader, project_dir, FakeClock(START), services=StubServices(repo_status_value=repo)
    )
    widget = cast("HeaderPanel", HeaderWidget().create(context))
    async with WidgetHostApp(widget).run_test():
        await wait_until(
            lambda: "main (+100 -20)" in _plain(widget.content), timeout=UI_WAIT_SECONDS
        )
        assert set(foregrounds(widget, "+100")) == {SAGE}
        assert set(foregrounds(widget, "+100 -20")[-3:]) == {ALERT_COLOR}


@pytest.mark.usefixtures("connection", "colour_terminal")
@pytest.mark.parametrize("no_color", [False, True], ids=["colour", "no_color"])
async def test_header_brand_is_bold_and_stays_bold_under_no_color(
    reader: StateReader,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_color: bool,
) -> None:
    if no_color:
        monkeypatch.setenv("NO_COLOR", "1")
    project_dir = write_minimal_project(tmp_path)
    context = widget_context(reader, project_dir, FakeClock(START))
    widget = cast("HeaderPanel", HeaderWidget().create(context))
    async with WidgetHostApp(widget).run_test():
        await wait_until(
            lambda: bool(re.search(rf"{BRAND} v\d+\.\d+\.\d+.* · demo", _plain(widget.content))),
            timeout=SHORT_WAIT_SECONDS,
        )
        line = rendered_lines(widget)[0]
        brand = line[: len(BRAND)]
        text = "".join(char for char, _ in line)
        version_start = text.index("v")
        version_end = text.index(" · ", version_start)
        project_start = text.index("demo")
    assert "".join(char for char, _ in brand) == BRAND
    assert all(bold for _, (bold, _) in brand)
    version_styles = line[version_start:version_end]
    project_styles = line[project_start : project_start + len("demo")]
    assert all(not bold for _, (bold, _) in version_styles)
    if not no_color:
        assert all(color == SECONDARY_TEXT_COLOR for _, (_, color) in version_styles)
    assert all(bold for _, (bold, _) in project_styles)


async def test_header_does_not_count_an_escalated_slot_as_occupied(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task = seed_task(connection, clock, title="escalated")
    store = TaskStore(connection, clock)
    store.set_slot(task.id, 0)
    store.apply_transition(task.id, Transition.stay(task, step=task.step, status=Status.ESCALATED))

    context = widget_context(reader, project_dir, FakeClock(START))
    widget = cast("HeaderPanel", HeaderWidget().create(context))
    async with WidgetHostApp(widget).run_test() as pilot:
        await pilot.pause()

        assert "Slots 0/2" in _plain(widget.content)


async def test_home_summary_separates_paused_and_external_waits_from_slot_occupancy(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    tasks = TaskStore(connection, clock)
    paused_id = _submit_task(connection, clock)
    tasks.set_holds(paused_id, frozenset({Hold.PAUSED}))
    running_id = _submit_task(connection, clock)
    tasks.set_slot(running_id, 1)
    external_wait = seed_task(connection, clock, title="Pull request wait")
    tasks.apply_transition(
        external_wait.id,
        Transition.stay(external_wait, step=Step.INTEGRATE, status=Status.INTEGRATING),
    )
    tasks.set_wait_reason(external_wait.id, WaitReason.PR_OPEN)

    views = state_views(reader, project_dir, clock, StubServices())
    summary = views.workspace_summary()
    header = await views.header()

    assert (summary.running, summary.waiting) == (1, 2)
    assert (header.occupied_slots, header.maximum_slots) == (2, 2)


async def test_header_shows_an_open_fault_streak_and_the_age_of_the_oldest_escalation(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task = seed_task(connection, clock, title="stuck")
    EscalationStore(connection, clock).open(
        task.id,
        step=None,
        reason=EscalationReason.DECISION_REQUIRED,
        headline="question",
        packet_path="packet.md",
        options=(),
    )
    events = EventLog(connection, clock)
    fault = {"scope": "engine tick", "error": "CommandFailed", "recoverable": True, "repeat": 3}
    events.append(EventKind.ENGINE_FAULT, message="failed", data=fault)

    context = widget_context(reader, project_dir, FakeClock(START))
    widget = cast("HeaderPanel", HeaderWidget().create(context))
    async with WidgetHostApp(widget).run_test() as pilot:
        await pilot.pause()
        text = _plain(widget.content)
        assert "engine tick: CommandFailed x3" in text
        assert "oldest escalation 0:00:00" in text

        events.append(EventKind.ENGINE_RECOVERED, message="ok", data={"scope": "engine tick"})

        await wait_until(
            lambda: "engine tick" not in _plain(widget.content), timeout=SHORT_WAIT_SECONDS
        )


@pytest.mark.usefixtures("connection")
@pytest.mark.parametrize(
    ("unpushed", "unpulled", "expected", "in_sync"),
    [
        pytest.param(0, 0, "✓ in sync with origin", True, id="in-sync"),
        pytest.param(5, 0, "5 behind origin", False, id="awaiting-push"),
        pytest.param(2, 1, "not in sync with origin", False, id="diverged-from-remote"),
        pytest.param(None, None, "origin unavailable", False, id="remote-ref-unknown"),
    ],
)
async def test_header_states_whether_the_base_is_published(
    reader: StateReader,
    tmp_path: Path,
    *,
    unpushed: int | None,
    unpulled: int | None,
    expected: str,
    in_sync: bool,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    checkout = _publishing_checkout(project_dir, unpushed=unpushed, unpulled=unpulled)
    context = widget_context(
        reader,
        project_dir,
        FakeClock(START),
        services=StubServices(repo_status_value=checkout),
    )
    widget = cast("HeaderPanel", HeaderWidget().create(context))
    async with WidgetHostApp(widget).run_test() as pilot:
        await pilot.pause()
        rendered = _plain(widget.content)
        assert expected in rendered
        assert ("✓ in sync with origin" in rendered) is in_sync


@pytest.mark.usefixtures("connection")
async def test_header_marks_previously_sampled_publication_counts(
    reader: StateReader, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    checkout = replace(
        _publishing_checkout(project_dir, unpushed=3, unpulled=0),
        state=DiagnosticSampleState.STALE,
        sampled_ago="0:00:05",
        error="git temporarily unavailable",
    )
    context = widget_context(
        reader,
        project_dir,
        FakeClock(START),
        services=StubServices(repo_status_value=checkout),
    )
    widget = cast("HeaderPanel", HeaderWidget().create(context))
    async with WidgetHostApp(widget).run_test() as pilot:
        await pilot.pause()
        assert "3 behind origin (previously)" in _plain(widget.content)


@pytest.mark.usefixtures("colour_terminal")
async def test_header_colours_the_publication_count_bold_and_the_push_time_grey(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    PushTargets(connection).register("main", now=START, interval=PUSH_INTERVAL_SECONDS)
    checkout = _publishing_checkout(project_dir, unpushed=5, unpulled=0)
    context = widget_context(
        reader,
        project_dir,
        FakeClock(START),
        services=StubServices(repo_status_value=checkout),
    )
    widget = cast("HeaderPanel", HeaderWidget().create(context))
    async with WidgetHostApp(widget).run_test():
        await wait_until(
            lambda: "5 behind origin" in _plain(widget.content), timeout=SHORT_WAIT_SECONDS
        )
        assert styles(widget, "5 behind")[0] == (True, PRIMARY_TEXT_COLOR)
        assert set(foregrounds(widget, " behind origin")) == {PRIMARY_TEXT_COLOR}
        assert set(foregrounds(widget, " · push at")) == {SECONDARY_TEXT_COLOR}


@pytest.mark.usefixtures("connection", "colour_terminal")
async def test_header_colours_the_in_sync_mark_green(reader: StateReader, tmp_path: Path) -> None:
    project_dir = write_minimal_project(tmp_path)
    checkout = _publishing_checkout(project_dir, unpushed=0, unpulled=0)
    context = widget_context(
        reader,
        project_dir,
        FakeClock(START),
        services=StubServices(repo_status_value=checkout),
    )
    widget = cast("HeaderPanel", HeaderWidget().create(context))
    async with WidgetHostApp(widget).run_test():
        await wait_until(lambda: "✓ in sync" in _plain(widget.content), timeout=SHORT_WAIT_SECONDS)
        assert set(foregrounds(widget, "✓")) == {SAGE}


async def test_header_shows_the_local_push_time_until_a_push_acknowledges_the_integrations(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    targets = PushTargets(connection)
    targets.register("main", now=START, interval=PUSH_INTERVAL_SECONDS)
    due = (START + timedelta(seconds=PUSH_INTERVAL_SECONDS)).astimezone().strftime("%H:%M")
    checkout = _publishing_checkout(project_dir, unpushed=1, unpulled=0)
    context = widget_context(
        reader,
        project_dir,
        FakeClock(START),
        services=StubServices(repo_status_value=checkout),
    )
    widget = cast("HeaderPanel", HeaderWidget().create(context))
    async with WidgetHostApp(widget).run_test():
        await wait_until(
            lambda: f"1 behind origin · push at {due}" in _plain(widget.content),
            timeout=UI_WAIT_SECONDS,
        )

        targets.acknowledge("main", registered=1, now=START, interval=PUSH_INTERVAL_SECONDS)

        await wait_until(lambda: "push at" not in _plain(widget.content), timeout=UI_WAIT_SECONDS)


@pytest.mark.usefixtures("connection")
@pytest.mark.parametrize(
    ("remote_configured", "push_time_shown"),
    [
        pytest.param(True, True, id="remote-configured"),
        pytest.param(False, False, id="remote-absent"),
    ],
)
async def test_header_shows_the_push_time_only_while_the_remote_is_configured(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
    *,
    remote_configured: bool,
    push_time_shown: bool,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    PushTargets(connection).register("main", now=START, interval=PUSH_INTERVAL_SECONDS)
    checkout = replace(
        _publishing_checkout(project_dir, unpushed=None, unpulled=None),
        remote_configured=remote_configured,
    )
    context = widget_context(
        reader,
        project_dir,
        FakeClock(START),
        services=StubServices(repo_status_value=checkout),
    )
    widget = cast("HeaderPanel", HeaderWidget().create(context))
    async with WidgetHostApp(widget).run_test() as pilot:
        await pilot.pause()
        rendered = _plain(widget.content)
        assert "origin unavailable" in rendered
        assert ("push at" in rendered) is push_time_shown


async def test_header_refreshes_when_the_writer_commits(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)

    context = widget_context(reader, project_dir, FakeClock(START))
    widget = cast("HeaderPanel", HeaderWidget().create(context))
    async with WidgetHostApp(widget).run_test() as pilot:
        await pilot.pause()
        assert "Slots 0/2" in _plain(widget.content)

        _submit_task(connection, clock)

        await wait_until(lambda: "Slots 1/2" in _plain(widget.content), timeout=SHORT_WAIT_SECONDS)


async def test_strips_shows_the_active_task_at_its_current_step(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task_id = _submit_task(connection, clock)

    context = widget_context(reader, project_dir, FakeClock(START))
    widget = cast("StripsPanel", StripsWidget().create(context))
    async with WidgetHostApp(widget).run_test() as pilot:
        await pilot.pause()
        text = _plain(widget.content)
        assert task_id in text
        assert "Implement" in text


async def test_strips_shows_only_a_triage_panel_before_the_mode_is_decided(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task_id = _submit_task(connection, clock, mode=None)

    context = widget_context(reader, project_dir, FakeClock(START))
    widget = cast("StripsPanel", StripsWidget().create(context))
    async with WidgetHostApp(widget).run_test() as pilot:
        await pilot.pause()
        text = _plain(widget.content)
        assert task_id in text
        assert "Triage" in text
        assert "Implement" not in text
        assert "Merge" not in text


async def test_strips_renders_the_narrow_form_in_a_narrow_pane(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    _submit_task(connection, clock)

    context = widget_context(reader, project_dir, FakeClock(START))
    widget = cast("StripsPanel", StripsWidget().create(context))
    async with WidgetHostApp(widget).run_test(size=(30, 24)) as pilot:
        await pilot.pause()
        text = _plain(widget.content)
        assert "|" not in text
        for line in text.splitlines():
            assert cell_len(line) <= 30


async def test_strips_shows_a_paused_stamp_for_a_held_task(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task_id = _submit_task(connection, clock)
    TaskStore(connection, clock).set_holds(task_id, frozenset({Hold.PAUSED}))

    context = widget_context(reader, project_dir, FakeClock(START))
    widget = cast("StripsPanel", StripsWidget().create(context))
    async with WidgetHostApp(widget).run_test() as pilot:
        await pilot.pause()
        assert "PAUSED" in _plain(widget.content)


async def test_queue_lists_a_queued_task_with_its_wait_reason(
    connection: Connection, reader: StateReader, tmp_path: Path
) -> None:
    project_dir = write_minimal_project(tmp_path)
    clock = FakeClock(START)
    task = seed_task(connection, clock, title="Add retry", priority=3)
    assert task.status is Status.QUEUED
    TaskStore(connection, clock).set_wait_reason(task.id, WaitReason.SLOT)

    context = widget_context(reader, project_dir, FakeClock(START))
    widget = cast("QueuePanel", QueueWidget().create(context))
    async with WidgetHostApp(widget).run_test():
        await wait_until(
            lambda: bool(widget.query(f"#task-row-{task.id}")), timeout=UI_WAIT_SECONDS
        )
        text = _plain(widget.query_one(f"#task-row-{task.id} > Static", Static).render())
        assert task.id in text
        assert "Add retry" in text
        assert "waiting on slot" in text
