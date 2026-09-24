"""`TurnRunner`: turn dir, pointer injection, confirmation, nudges and rebuild (03 §4.3)."""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from shikiru.agents.contracts import PromptSubmitted, SessionEnded, TurnEnded, role_dir
from shikiru.config.settings import AgentConfig
from shikiru.domain.recovery import Completed, PaneDied, ProtocolError, Stalled
from shikiru.domain.reports import ConsultAnswer, ReportKind, schema_for
from shikiru.domain.tasks import Hold, Role, Step
from shikiru.engine.agents import AgentPool
from shikiru.engine.monitor import InjectedLines
from shikiru.engine.turns import (
    BEFORE_TURN_WAIT_SECONDS,
    TurnRunner,
    TurnSpec,
    read_turn_report,
)
from shikiru.helper.helper import Helper
from shikiru.process import SubprocessRunner
from shikiru.prompts.renderer import PromptName, PromptRenderer
from shikiru.workspace.roots import run_dir, task_root
from tests.support.clock import FakeClock
from tests.support.engine import FAKE_AGENT_NAME, FAKE_RUNG, build_registry, set_script
from tests.support.factories import TASK_CREATED_AT, make_settings
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Mapping
    from pathlib import Path

    from shikiru.agents.contracts import AgentSignal, Usage
    from shikiru.config.settings import Settings
    from shikiru.engine.monitor import MonitorSignal
    from tests.support.engine import EngineEnv
    from tests.support.tmux import TmuxServer

_SIGNAL_TIMEOUT = 15.0
_RETRY_WAIT_SECONDS = 16


class _NoBeforeTurnSession:
    """An `AgentSession` double with no `before_turn` lines (04 §1)."""

    async def signals(self) -> AsyncGenerator[AgentSignal]:
        while True:
            await asyncio.Event().wait()
            yield SessionEnded()

    async def before_turn(self) -> list[str]:
        return []

    async def usage(self) -> Usage:
        raise NotImplementedError

    def snapshot(self) -> Mapping[str, Any]:
        return {}


class _FixedBeforeTurnSession(_NoBeforeTurnSession):
    """An `AgentSession` double whose `before_turn()` returns a fixed set of lines (04 §1)."""

    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    async def before_turn(self) -> list[str]:
        return self._lines


def _settings_with_asks(asks: list[dict[str, Any]]) -> Settings:
    return make_settings(
        implementer_ladder=[FAKE_RUNG],
        agents={FAKE_AGENT_NAME: AgentConfig(options={"asks": asks})},
    )


def _build_helper(settings: Settings) -> Helper:
    pool = AgentPool(
        build_registry().create, SubprocessRunner(), settings, FakeClock(TASK_CREATED_AT)
    )
    return Helper(pool.get(FAKE_AGENT_NAME))


def _spec(*, cwd: Path, iteration: int = 1, facts: str = "") -> TurnSpec:
    return TurnSpec(
        step=Step.CONSULT,
        iteration=iteration,
        kind=ReportKind.CONSULT,
        template=PromptName.CONSULT,
        context={
            "request": "Add retries to the flaky parser",
            "workspace": str(cwd),
            "trackers": [],
            "reports": [],
            "question": "Which retry library should I use?",
        },
        prompt_prefix="",
        facts=facts,
        cwd=cwd,
        helper_model="fake-helper",
        helper_effort=None,
        helper_timeout=timedelta(seconds=5),
    )


async def test_a_turn_that_is_accepted_immediately_completes_with_its_report(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    settings = _settings_with_asks([])
    manager = engine_env.build_manager(settings)
    task = await engine_env.build_task()
    await set_script(tmux_server, [{"kind": "accept"}])
    session = await manager.ensure(task, Role.IMPLEMENTER)
    queue = manager.subscribe(task.id, Role.IMPLEMENTER)
    pane = engine_env.pane(task.panes[Role.IMPLEMENTER])
    runner = TurnRunner(PromptRenderer(engine_env.project_root), FakeClock(TASK_CREATED_AT))
    helper = _build_helper(settings)

    result = await asyncio.wait_for(
        runner.run(
            session,
            pane,
            queue,
            run_dir=run_dir(engine_env.project_root, task.id),
            role=Role.IMPLEMENTER,
            spec=_spec(cwd=task_root(engine_env.project_root, task.id)),
            helper=helper,
            injected=manager.injected_lines(task.id, Role.IMPLEMENTER),
        ),
        timeout=_SIGNAL_TIMEOUT,
    )

    assert isinstance(result, Completed)
    assert result.rebuilt is False
    assert isinstance(result.report, ConsultAnswer)
    assert result.report.decision == "answer"

    task_run_dir = run_dir(engine_env.project_root, task.id)
    turn_dir = task_run_dir / "turns" / "01-consult-i1"
    assert "Add retries to the flaky parser" in (turn_dir / "prompt.md").read_text()
    assert json.loads((turn_dir / "report.schema.json").read_text()) == schema_for(
        ReportKind.CONSULT
    )
    assert (turn_dir / "report.kind").read_text() == ReportKind.CONSULT.value
    current_turn = role_dir(task_run_dir, Role.IMPLEMENTER.value) / "current_turn"
    assert current_turn.resolve() == turn_dir.resolve()


async def test_a_turn_with_facts_writes_facts_md_next_to_the_prompt(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    settings = _settings_with_asks([])
    manager = engine_env.build_manager(settings)
    task = await engine_env.build_task()
    await set_script(tmux_server, [{"kind": "accept"}])
    session = await manager.ensure(task, Role.IMPLEMENTER)
    queue = manager.subscribe(task.id, Role.IMPLEMENTER)
    pane = engine_env.pane(task.panes[Role.IMPLEMENTER])
    runner = TurnRunner(PromptRenderer(engine_env.project_root), FakeClock(TASK_CREATED_AT))
    helper = _build_helper(settings)
    task_run_dir = run_dir(engine_env.project_root, task.id)

    await asyncio.wait_for(
        runner.run(
            session,
            pane,
            queue,
            run_dir=task_run_dir,
            role=Role.IMPLEMENTER,
            spec=_spec(cwd=task_root(engine_env.project_root, task.id), facts="git log: none"),
            helper=helper,
            injected=manager.injected_lines(task.id, Role.IMPLEMENTER),
        ),
        timeout=_SIGNAL_TIMEOUT,
    )

    facts_path = task_run_dir / "turns" / "01-consult-i1" / "facts.md"
    assert facts_path.read_text() == "git log: none"


async def test_a_turn_without_facts_writes_no_facts_md(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    settings = _settings_with_asks([])
    manager = engine_env.build_manager(settings)
    task = await engine_env.build_task()
    await set_script(tmux_server, [{"kind": "accept"}])
    session = await manager.ensure(task, Role.IMPLEMENTER)
    queue = manager.subscribe(task.id, Role.IMPLEMENTER)
    pane = engine_env.pane(task.panes[Role.IMPLEMENTER])
    runner = TurnRunner(PromptRenderer(engine_env.project_root), FakeClock(TASK_CREATED_AT))
    helper = _build_helper(settings)
    task_run_dir = run_dir(engine_env.project_root, task.id)

    await asyncio.wait_for(
        runner.run(
            session,
            pane,
            queue,
            run_dir=task_run_dir,
            role=Role.IMPLEMENTER,
            spec=_spec(cwd=task_root(engine_env.project_root, task.id)),
            helper=helper,
            injected=manager.injected_lines(task.id, Role.IMPLEMENTER),
        ),
        timeout=_SIGNAL_TIMEOUT,
    )

    facts_path = task_run_dir / "turns" / "01-consult-i1" / "facts.md"
    assert not facts_path.exists()


async def test_a_turn_ended_without_a_report_is_nudged_then_completes(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    settings = _settings_with_asks([])
    manager = engine_env.build_manager(settings)
    task = await engine_env.build_task()
    await set_script(tmux_server, [{"kind": "no_report"}])
    session = await manager.ensure(task, Role.IMPLEMENTER)
    queue = manager.subscribe(task.id, Role.IMPLEMENTER)
    pane = engine_env.pane(task.panes[Role.IMPLEMENTER])
    runner = TurnRunner(PromptRenderer(engine_env.project_root), FakeClock(TASK_CREATED_AT))
    helper = _build_helper(settings)

    result = await asyncio.wait_for(
        runner.run(
            session,
            pane,
            queue,
            run_dir=run_dir(engine_env.project_root, task.id),
            role=Role.IMPLEMENTER,
            spec=_spec(cwd=task_root(engine_env.project_root, task.id)),
            helper=helper,
            injected=manager.injected_lines(task.id, Role.IMPLEMENTER),
        ),
        timeout=_SIGNAL_TIMEOUT,
    )

    assert isinstance(result, Completed)
    assert result.rebuilt is False


async def test_three_reportless_turns_are_rebuilt_by_the_helper(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    rebuilt_answer = {"decision": "answer", "answer": "Use tenacity"}
    settings = _settings_with_asks([rebuilt_answer])
    manager = engine_env.build_manager(settings)
    task = await engine_env.build_task()
    await set_script(
        tmux_server, [{"kind": "no_report"}, {"kind": "no_report"}, {"kind": "no_report"}]
    )
    session = await manager.ensure(task, Role.IMPLEMENTER)
    queue = manager.subscribe(task.id, Role.IMPLEMENTER)
    pane = engine_env.pane(task.panes[Role.IMPLEMENTER])
    runner = TurnRunner(PromptRenderer(engine_env.project_root), FakeClock(TASK_CREATED_AT))
    helper = _build_helper(settings)
    task_run_dir = run_dir(engine_env.project_root, task.id)

    result = await asyncio.wait_for(
        runner.run(
            session,
            pane,
            queue,
            run_dir=task_run_dir,
            role=Role.IMPLEMENTER,
            spec=_spec(cwd=task_root(engine_env.project_root, task.id)),
            helper=helper,
            injected=manager.injected_lines(task.id, Role.IMPLEMENTER),
        ),
        timeout=_SIGNAL_TIMEOUT,
    )

    assert isinstance(result, Completed)
    assert result.rebuilt is True
    assert isinstance(result.report, ConsultAnswer)
    assert result.report.answer == "Use tenacity"
    helper_json = task_run_dir / "turns" / "01-consult-i1" / "helper.json"
    assert json.loads(helper_json.read_text())["answer"] == "Use tenacity"


def test_invalid_agent_report_falls_back_to_a_valid_helper_report(tmp_path: Path) -> None:
    turn_dir = tmp_path / "turn"
    turn_dir.mkdir()
    (turn_dir / "report.kind").write_text(ReportKind.CONSULT.value)
    (turn_dir / "report.json").write_text("not json")
    (turn_dir / "helper.json").write_text(
        ConsultAnswer(decision="answer", answer="Use tenacity").model_dump_json()
    )

    report = read_turn_report(turn_dir)

    assert report == ConsultAnswer(decision="answer", answer="Use tenacity")


async def test_helper_failure_becomes_a_protocol_error(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    settings = _settings_with_asks([])
    manager = engine_env.build_manager(settings)
    task = await engine_env.build_task()
    await set_script(
        tmux_server,
        [{"kind": "no_report"}, {"kind": "no_report"}, {"kind": "no_report"}],
    )
    session = await manager.ensure(task, Role.IMPLEMENTER)
    queue = manager.subscribe(task.id, Role.IMPLEMENTER)
    pane = engine_env.pane(task.panes[Role.IMPLEMENTER])
    runner = TurnRunner(PromptRenderer(engine_env.project_root), FakeClock(TASK_CREATED_AT))
    helper = _build_helper(settings)

    result = await asyncio.wait_for(
        runner.run(
            session,
            pane,
            queue,
            run_dir=run_dir(engine_env.project_root, task.id),
            role=Role.IMPLEMENTER,
            spec=_spec(cwd=task_root(engine_env.project_root, task.id)),
            helper=helper,
            injected=manager.injected_lines(task.id, Role.IMPLEMENTER),
        ),
        timeout=_SIGNAL_TIMEOUT,
    )

    assert isinstance(result, ProtocolError)


async def test_missing_pane_during_pointer_injection_returns_pane_died(
    engine_env: EngineEnv,
) -> None:
    settings = _settings_with_asks([])
    manager = engine_env.build_manager(settings)
    task = await engine_env.build_task()
    session = await manager.ensure(task, Role.IMPLEMENTER)
    queue = manager.subscribe(task.id, Role.IMPLEMENTER)
    pane = engine_env.pane(task.panes[Role.IMPLEMENTER])
    await pane.kill()
    runner = TurnRunner(PromptRenderer(engine_env.project_root), FakeClock(TASK_CREATED_AT))
    helper = _build_helper(settings)
    injected = manager.injected_lines(task.id, Role.IMPLEMENTER)

    result = await asyncio.wait_for(
        runner.run(
            session,
            pane,
            queue,
            run_dir=run_dir(engine_env.project_root, task.id),
            role=Role.IMPLEMENTER,
            spec=_spec(cwd=task_root(engine_env.project_root, task.id)),
            helper=helper,
            injected=injected,
        ),
        timeout=_SIGNAL_TIMEOUT,
    )

    assert isinstance(result, PaneDied)
    assert len(injected) == 0


async def test_an_unconfirmed_pointer_retries_once_then_stalls(engine_env: EngineEnv) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    task = await engine_env.build_task()
    pane_id = await engine_env.new_pane()
    pane = engine_env.pane(pane_id)
    signals: asyncio.Queue[MonitorSignal] = asyncio.Queue()
    runner = TurnRunner(PromptRenderer(engine_env.project_root), clock)
    helper = _build_helper(_settings_with_asks([]))

    run_task = asyncio.ensure_future(
        runner.run(
            _NoBeforeTurnSession(),
            pane,
            signals,
            run_dir=run_dir(engine_env.project_root, task.id),
            role=Role.IMPLEMENTER,
            spec=_spec(cwd=task_root(engine_env.project_root, task.id)),
            helper=helper,
            injected=InjectedLines(),
        )
    )
    try:
        await wait_until(clock.has_pending_sleep, timeout=_SIGNAL_TIMEOUT)
        clock.advance(_RETRY_WAIT_SECONDS)
        await wait_until(clock.has_pending_sleep, timeout=_SIGNAL_TIMEOUT)
        clock.advance(_RETRY_WAIT_SECONDS)
        result = await asyncio.wait_for(run_task, timeout=_SIGNAL_TIMEOUT)
    finally:
        if not run_task.done():
            run_task.cancel()
            with suppress(asyncio.CancelledError):
                await run_task

    assert isinstance(result, Stalled)


async def test_delayed_pointer_confirmation_after_retry_consumes_one_marker(
    engine_env: EngineEnv,
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    task = await engine_env.build_task()
    pane = engine_env.pane(await engine_env.new_pane())
    signals: asyncio.Queue[MonitorSignal] = asyncio.Queue()
    injected = InjectedLines()
    runner = TurnRunner(PromptRenderer(engine_env.project_root), clock)
    helper = _build_helper(_settings_with_asks([]))
    task_run_dir = run_dir(engine_env.project_root, task.id)
    prompt_path = task_run_dir / "turns" / "01-consult-i1" / "prompt.md"
    injected_line = f"Read {prompt_path} and follow it exactly."

    run_task = asyncio.create_task(
        runner.run(
            _NoBeforeTurnSession(),
            pane,
            signals,
            run_dir=task_run_dir,
            role=Role.IMPLEMENTER,
            spec=_spec(cwd=task_root(engine_env.project_root, task.id)),
            helper=helper,
            injected=injected,
        )
    )
    try:
        await wait_until(clock.has_pending_sleep, timeout=_SIGNAL_TIMEOUT)
        clock.advance(_RETRY_WAIT_SECONDS)
        await wait_until(clock.has_pending_sleep, timeout=_SIGNAL_TIMEOUT)
        (prompt_path.parent / "report.json").write_text(
            ConsultAnswer(decision="answer", answer="Use tenacity").model_dump_json()
        )
        signals.put_nowait(PromptSubmitted(text=injected_line))
        assert injected.consume(injected_line)
        signals.put_nowait(TurnEnded(last_message="done"))
        result = await asyncio.wait_for(run_task, timeout=_SIGNAL_TIMEOUT)
    finally:
        if not run_task.done():
            run_task.cancel()
            with suppress(asyncio.CancelledError):
                await run_task

    assert isinstance(result, Completed)
    assert len(injected) == 0


async def test_a_before_turn_line_is_not_flagged_as_an_operator_prompt(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    settings = _settings_with_asks([])
    manager = engine_env.build_manager(settings)
    task = await engine_env.build_task()
    await set_script(tmux_server, [{"kind": "accept"}, {"kind": "accept"}])
    await manager.ensure(task, Role.IMPLEMENTER)
    queue = manager.subscribe(task.id, Role.IMPLEMENTER)
    pane = engine_env.pane(task.panes[Role.IMPLEMENTER])
    runner = TurnRunner(PromptRenderer(engine_env.project_root), FakeClock(TASK_CREATED_AT))
    helper = _build_helper(settings)

    result = await asyncio.wait_for(
        runner.run(
            _FixedBeforeTurnSession(["/compact"]),
            pane,
            queue,
            run_dir=run_dir(engine_env.project_root, task.id),
            role=Role.IMPLEMENTER,
            spec=_spec(cwd=task_root(engine_env.project_root, task.id)),
            helper=helper,
            injected=manager.injected_lines(task.id, Role.IMPLEMENTER),
        ),
        timeout=_SIGNAL_TIMEOUT,
    )

    assert isinstance(result, Completed)
    current = engine_env.tasks.get(task.id)
    assert current is not None
    assert Hold.INTERVENTION not in current.holds


async def test_before_turn_timeout_returns_stalled(engine_env: EngineEnv) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    task = await engine_env.build_task()
    pane = engine_env.pane(await engine_env.new_pane())
    signals: asyncio.Queue[MonitorSignal] = asyncio.Queue()
    runner = TurnRunner(PromptRenderer(engine_env.project_root), clock)
    helper = _build_helper(_settings_with_asks([]))
    run_task = asyncio.create_task(
        runner.run(
            _FixedBeforeTurnSession(["/compact"]),
            pane,
            signals,
            run_dir=run_dir(engine_env.project_root, task.id),
            role=Role.IMPLEMENTER,
            spec=_spec(cwd=task_root(engine_env.project_root, task.id)),
            helper=helper,
            injected=InjectedLines(),
        )
    )
    try:
        await wait_until(clock.has_pending_sleep, timeout=_SIGNAL_TIMEOUT)
        clock.advance(BEFORE_TURN_WAIT_SECONDS + 1)
        result = await asyncio.wait_for(run_task, timeout=_SIGNAL_TIMEOUT)
    finally:
        if not run_task.done():
            run_task.cancel()
            with suppress(asyncio.CancelledError):
                await run_task

    assert isinstance(result, Stalled)


async def test_before_turn_pane_error_returns_pane_died(engine_env: EngineEnv) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    task = await engine_env.build_task()
    pane = engine_env.pane(await engine_env.new_pane())
    await pane.kill()
    signals: asyncio.Queue[MonitorSignal] = asyncio.Queue()
    runner = TurnRunner(PromptRenderer(engine_env.project_root), clock)
    helper = _build_helper(_settings_with_asks([]))

    result = await asyncio.wait_for(
        runner.run(
            _FixedBeforeTurnSession(["/compact"]),
            pane,
            signals,
            run_dir=run_dir(engine_env.project_root, task.id),
            role=Role.IMPLEMENTER,
            spec=_spec(cwd=task_root(engine_env.project_root, task.id)),
            helper=helper,
            injected=InjectedLines(),
        ),
        timeout=_SIGNAL_TIMEOUT,
    )

    assert isinstance(result, PaneDied)


async def test_injected_lines_are_isolated_per_session(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    settings = _settings_with_asks([])
    manager = engine_env.build_manager(settings)
    task_a = await engine_env.build_task(title="a")
    task_b = await engine_env.build_task(title="b")
    await set_script(tmux_server, [{"kind": "no_report"}])
    await manager.ensure(task_a, Role.IMPLEMENTER)
    await manager.ensure(task_b, Role.IMPLEMENTER)
    injected_a = manager.injected_lines(task_a.id, Role.IMPLEMENTER)
    shared_text = "hello from a's pointer, injected only on a"
    injected_a.add(shared_text)
    pane_b = engine_env.pane(task_b.panes[Role.IMPLEMENTER])

    await pane_b.paste_line(shared_text)

    def _has_intervention(task_id: str) -> bool:
        current = engine_env.tasks.get(task_id)
        return current is not None and Hold.INTERVENTION in current.holds

    await wait_until(lambda: _has_intervention(task_b.id), timeout=_SIGNAL_TIMEOUT)
    assert not _has_intervention(task_a.id)
