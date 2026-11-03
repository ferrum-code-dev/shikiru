"""Integration tests for the Leader/Implementer feedback turns on the fake agent."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from shikiru.domain.events import EventKind
from shikiru.domain.feedback import (
    FeedbackInput,
    FeedbackMessage,
    FeedbackSnapshot,
    FeedbackSource,
    FeedbackSourceKind,
)
from shikiru.domain.reports import (
    REPORT_FILENAME,
    REPORT_KIND_FILENAME,
    FeedbackAnalysis,
    FeedbackRework,
    FeedbackVerdict,
    ReportKind,
)
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import Mode, Role, Status, Step
from shikiru.engine.history import ReportHistory
from shikiru.engine.turns import TurnRequest
from shikiru.prompts.renderer import PromptName
from shikiru.workspace.roots import run_dir
from tests.support.engine import set_role_script
from tests.support.flow import build_flow, build_flow_task, flow_settings, init_project_repo

if TYPE_CHECKING:
    from shikiru.domain.tasks import Task
    from tests.support.engine import EngineEnv
    from tests.support.flow import FlowEnv
    from tests.support.tmux import TmuxServer

_TIMEOUT_SECONDS = 60.0
_REVIEW = "review-body-1"
_THREAD = "thread-1"
_ECHO = "thread-2"
_OTHER = "thread-3"
_WORK_ID = "w1"
_WORK_TEXT = "rename the retry helper"
_TEMPLATES = {
    ReportKind.FEEDBACK_ANALYSIS: PromptName.FEEDBACK_ANALYSIS,
    ReportKind.FEEDBACK_REWORK: PromptName.FEEDBACK_REWORK,
    ReportKind.FEEDBACK_VERDICT: PromptName.FEEDBACK_VERIFY,
}


def _source_url(source_id: str) -> str:
    return f"https://github.test/{source_id}"


def _source(source_id: str, kind: FeedbackSourceKind) -> FeedbackSource:
    return FeedbackSource(
        id=source_id,
        kind=kind,
        url=_source_url(source_id),
        location="src/parser.py:7",
        outdated=True,
        messages=[FeedbackMessage(author="reviewer", body="Please change", url="u")],
    )


def _feedback(*source_ids: str, analysis: FeedbackAnalysis | None = None) -> FeedbackInput:
    return FeedbackInput(
        snapshot=FeedbackSnapshot(
            pull_request_url="https://github.test/pr/1",
            head_sha="abc123",
            sources=[
                _source(
                    source_id,
                    FeedbackSourceKind.REVIEW_BODY
                    if source_id == _REVIEW
                    else FeedbackSourceKind.DISCUSSION,
                )
                for source_id in source_ids
            ],
        ),
        analysis=analysis,
    )


def _decision(source_id: str, action: str, **overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "source_id": source_id,
        "action": action,
        "points": ["the point"],
        "detail": "the detail",
        "reply": f"reply to {source_id}",
    }
    if action == "address":
        fields["work_id"] = _WORK_ID
    fields.update(overrides)
    return fields


def _analysis_step(*decisions: dict[str, Any], **extra: Any) -> dict[str, Any]:
    has_work = any(decision["action"] == "address" for decision in decisions)
    return {
        "kind": "report",
        "report": {
            "summary": "analysed",
            "work": [{"id": _WORK_ID, "instruction": _WORK_TEXT}] if has_work else [],
            "decisions": list(decisions),
            **extra,
        },
    }


def _verdict_step(decision: str, *reply_ids: str) -> dict[str, Any]:
    return {
        "kind": "report",
        "report": {
            "decision": decision,
            "summary": "verified",
            **({"commit_subject": "feat: verify feedback"} if decision == "accept" else {}),
            "replies": [{"source_id": source_id, "reply": "final"} for source_id in reply_ids],
        },
    }


async def _integrating(env: EngineEnv, task: Task) -> Task:
    env.tasks.apply_transition(
        task.id, Transition.stay(task, step=Step.INTEGRATE, status=Status.INTEGRATING)
    )
    return env.tasks.require(task.id)


def _prompts(env: EngineEnv, task: Task, kind: ReportKind) -> list[str]:
    turn_dirs = sorted((run_dir(env.project_root, task.id) / "turns").iterdir())
    return [
        (turn_dir / "prompt.md").read_text()
        for turn_dir in turn_dirs
        if (turn_dir / REPORT_KIND_FILENAME).read_text().strip() == kind.value
    ]


async def _run(
    flow_env: FlowEnv,
    task: Task,
    role: Role,
    kind: ReportKind,
    feedback: FeedbackInput,
) -> Any:
    _, report = await asyncio.wait_for(
        flow_env.steps.run_report_turn(
            task, role=role, template=_TEMPLATES[kind], kind=kind, feedback=feedback
        ),
        timeout=_TIMEOUT_SECONDS,
    )
    return report


async def test_mixed_review_flows_from_analysis_through_rework_to_verified_replies(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    await init_project_repo(engine_env.project_root)
    task = await _integrating(engine_env, await build_flow_task(engine_env, mode=Mode.IMPLEMENT))
    flow_env = build_flow(engine_env, flow_settings())
    decisions = [
        _decision(_REVIEW, "disagree"),
        _decision(_THREAD, "address"),
        _decision(_ECHO, "address"),
        _decision(_OTHER, "already_addressed"),
    ]
    all_ids = (_REVIEW, _THREAD, _ECHO, _OTHER)
    await set_role_script(
        tmux_server,
        Role.LEADER,
        [_analysis_step(*decisions), _verdict_step("accept", *all_ids)],
    )
    await set_role_script(tmux_server, Role.IMPLEMENTER, [{"kind": "accept"}])

    analysis = await _run(
        flow_env,
        task,
        Role.LEADER,
        ReportKind.FEEDBACK_ANALYSIS,
        _feedback(*all_ids),
    )
    assert isinstance(analysis, FeedbackAnalysis)
    assert len(analysis.work) == 1
    rework = await _run(
        flow_env,
        task,
        Role.IMPLEMENTER,
        ReportKind.FEEDBACK_REWORK,
        _feedback(*all_ids, analysis=analysis),
    )
    assert isinstance(rework, FeedbackRework)
    assert rework.commits
    verdict = await _run(
        flow_env,
        task,
        Role.LEADER,
        ReportKind.FEEDBACK_VERDICT,
        _feedback(*all_ids, analysis=analysis),
    )
    assert isinstance(verdict, FeedbackVerdict)
    assert {reply.source_id for reply in verdict.replies} == set(all_ids)

    (rework_prompt,) = _prompts(engine_env, task, ReportKind.FEEDBACK_REWORK)
    assert _WORK_TEXT in rework_prompt
    assert _source_url(_THREAD) in rework_prompt
    assert _source_url(_ECHO) in rework_prompt
    assert _source_url(_REVIEW) not in rework_prompt
    assert _source_url(_OTHER) not in rework_prompt
    (analysis_prompt,) = _prompts(engine_env, task, ReportKind.FEEDBACK_ANALYSIS)
    (verify_prompt,) = _prompts(engine_env, task, ReportKind.FEEDBACK_VERDICT)
    assert all(_source_url(source_id) in analysis_prompt for source_id in all_ids)
    assert _WORK_TEXT in verify_prompt


async def test_a_round_without_accepted_work_needs_no_implementer_turn(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    await init_project_repo(engine_env.project_root)
    task = await _integrating(engine_env, await build_flow_task(engine_env, mode=Mode.IMPLEMENT))
    flow_env = build_flow(engine_env, flow_settings())
    ids = (_REVIEW, _THREAD)
    await set_role_script(
        tmux_server,
        Role.LEADER,
        [
            _analysis_step(_decision(_REVIEW, "disagree"), _decision(_THREAD, "already_addressed")),
            _verdict_step("accept", *ids),
        ],
    )

    analysis = await _run(
        flow_env,
        task,
        Role.LEADER,
        ReportKind.FEEDBACK_ANALYSIS,
        _feedback(*ids),
    )
    verdict = await _run(
        flow_env,
        task,
        Role.LEADER,
        ReportKind.FEEDBACK_VERDICT,
        _feedback(*ids, analysis=analysis),
    )

    assert analysis.work == []
    assert isinstance(verdict, FeedbackVerdict)
    started = flow_env.events.since(0, task_id=task.id, kinds=[EventKind.TURN_STARTED])
    assert {event.data["role"] for event in started} == {Role.LEADER.value}


async def test_unverified_drafts_are_sent_back_instead_of_accepted(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    await init_project_repo(engine_env.project_root)
    task = await _integrating(engine_env, await build_flow_task(engine_env, mode=Mode.IMPLEMENT))
    flow_env = build_flow(engine_env, flow_settings())
    analysis = FeedbackAnalysis.model_validate(
        {
            "summary": "s",
            "work": [],
            "decisions": [_decision(_REVIEW, "disagree")],
        }
    )
    await set_role_script(
        tmux_server,
        Role.LEADER,
        [
            _verdict_step("accept"),
            _verdict_step("accept", _OTHER),
            _verdict_step("accept", _REVIEW),
        ],
    )

    verdict = await _run(
        flow_env,
        task,
        Role.LEADER,
        ReportKind.FEEDBACK_VERDICT,
        _feedback(_REVIEW, analysis=analysis),
    )

    assert isinstance(verdict, FeedbackVerdict)
    assert [reply.source_id for reply in verdict.replies] == [_REVIEW]
    nudges = flow_env.events.since(0, task_id=task.id, kinds=[EventKind.NUDGE])
    assert len(nudges) == 2


async def test_an_analysis_that_misses_a_source_is_rejected_before_it_is_used(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    await init_project_repo(engine_env.project_root)
    task = await _integrating(engine_env, await build_flow_task(engine_env, mode=Mode.IMPLEMENT))
    flow_env = build_flow(engine_env, flow_settings())
    await set_role_script(
        tmux_server,
        Role.LEADER,
        [
            _analysis_step(_decision(_REVIEW, "disagree")),
            _analysis_step(_decision(_REVIEW, "disagree"), _decision("unknown", "disagree")),
            _analysis_step(_decision(_REVIEW, "disagree"), _decision(_THREAD, "disagree")),
        ],
    )

    analysis = await _run(
        flow_env,
        task,
        Role.LEADER,
        ReportKind.FEEDBACK_ANALYSIS,
        _feedback(_REVIEW, _THREAD),
    )

    assert {decision.source_id for decision in analysis.decisions} == {_REVIEW, _THREAD}
    nudges = flow_env.events.since(0, task_id=task.id, kinds=[EventKind.NUDGE])
    assert len(nudges) == 2


async def test_planning_feedback_keeps_the_task_and_emits_no_follow_up_tasks(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    await init_project_repo(engine_env.project_root)
    task = await _integrating(engine_env, await build_flow_task(engine_env, mode=Mode.PLAN))
    flow_env = build_flow(engine_env, flow_settings())
    decision = _decision(_REVIEW, "needs_operator", reply="", detail="Implement the product?")
    await set_role_script(
        tmux_server,
        Role.LEADER,
        [
            _analysis_step(
                decision, new_tasks=[{"title": "Build it", "request": "Build the product"}]
            ),
            _analysis_step(decision),
        ],
    )

    analysis = await _run(
        flow_env,
        task,
        Role.LEADER,
        ReportKind.FEEDBACK_ANALYSIS,
        _feedback(_REVIEW),
    )

    assert analysis.decisions[0].action.value == "needs_operator"
    assert [row.id for row in engine_env.tasks.list_all()] == [task.id]
    assert Mode.PLAN.value in _prompts(engine_env, task, ReportKind.FEEDBACK_ANALYSIS)[0]


async def test_feedback_reports_stay_readable_and_apart_from_integration_evidence(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    await init_project_repo(engine_env.project_root)
    task = await _integrating(engine_env, await build_flow_task(engine_env, mode=Mode.IMPLEMENT))
    flow_env = build_flow(engine_env, flow_settings())
    ids = (_THREAD,)
    await set_role_script(
        tmux_server,
        Role.LEADER,
        [_analysis_step(_decision(_THREAD, "address")), _verdict_step("accept", _THREAD)],
    )
    await set_role_script(tmux_server, Role.IMPLEMENTER, [{"kind": "accept"}])
    analysis = await _run(
        flow_env,
        task,
        Role.LEADER,
        ReportKind.FEEDBACK_ANALYSIS,
        _feedback(*ids),
    )
    await _run(
        flow_env,
        task,
        Role.IMPLEMENTER,
        ReportKind.FEEDBACK_REWORK,
        _feedback(*ids, analysis=analysis),
    )
    await _run(
        flow_env,
        task,
        Role.LEADER,
        ReportKind.FEEDBACK_VERDICT,
        _feedback(*ids, analysis=analysis),
    )

    history = ReportHistory(engine_env.project_root)
    kinds = [type(report) for _, report in history.load(task.id)]
    assert kinds == [FeedbackAnalysis, FeedbackRework, FeedbackVerdict]
    assert history.latest_report(task.id) is not None
    completed = flow_env.events.since(0, task_id=task.id, kinds=[EventKind.TURN_COMPLETED])
    assert [event.data["report_kind"] for event in completed] == [
        ReportKind.FEEDBACK_ANALYSIS.value,
        ReportKind.FEEDBACK_REWORK.value,
        ReportKind.FEEDBACK_VERDICT.value,
    ]
    assert history.integration_metadata(task).summary != "verified"


async def _recorded_analysis_turn(
    engine_env: EngineEnv, tmux_server: TmuxServer, feedback: FeedbackInput
) -> tuple[Task, FeedbackAnalysis]:
    task = await _integrating(engine_env, await build_flow_task(engine_env, mode=Mode.IMPLEMENT))
    flow_env = build_flow(engine_env, flow_settings())
    await set_role_script(
        tmux_server,
        Role.LEADER,
        [_analysis_step(*(_decision(s.id, "disagree") for s in feedback.snapshot.sources))],
    )
    analysis = await _run(flow_env, task, Role.LEADER, ReportKind.FEEDBACK_ANALYSIS, feedback)
    return task, analysis


def _request(task: Task, kind: ReportKind, feedback: FeedbackInput) -> TurnRequest:
    return TurnRequest(
        step=Step.INTEGRATE,
        iteration=task.iteration,
        kind=kind,
        template=_TEMPLATES[kind],
        context={"task_id": task.id},
        facts="",
        feedback=feedback,
    )


async def test_a_recovered_feedback_report_is_reused_only_for_the_same_request(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    await init_project_repo(engine_env.project_root)
    feedback = _feedback(_REVIEW, _THREAD)
    task, analysis = await _recorded_analysis_turn(engine_env, tmux_server, feedback)
    turns = engine_env.turn_system.turns

    same = turns.completed_turn(
        task.id, Role.LEADER, _request(task, ReportKind.FEEDBACK_ANALYSIS, feedback)
    )
    other_kind = turns.completed_turn(
        task.id,
        Role.LEADER,
        _request(task, ReportKind.FEEDBACK_VERDICT, _feedback(_REVIEW, _THREAD, analysis=analysis)),
    )
    other_snapshot = turns.completed_turn(
        task.id, Role.LEADER, _request(task, ReportKind.FEEDBACK_ANALYSIS, _feedback(_ECHO))
    )

    assert same is not None
    assert isinstance(same[0].report, FeedbackAnalysis)
    assert other_kind is None
    assert other_snapshot is None


async def test_a_recovered_feedback_report_that_breaks_its_contract_is_not_reused(
    engine_env: EngineEnv, tmux_server: TmuxServer
) -> None:
    await init_project_repo(engine_env.project_root)
    feedback = _feedback(_REVIEW, _THREAD)
    task, _ = await _recorded_analysis_turn(engine_env, tmux_server, feedback)
    turn_dir = max((run_dir(engine_env.project_root, task.id) / "turns").iterdir())
    incomplete = FeedbackAnalysis.model_validate(
        {"summary": "s", "work": [], "decisions": [_decision(_REVIEW, "disagree")]}
    )
    (turn_dir / REPORT_FILENAME).write_text(incomplete.model_dump_json())

    found = engine_env.turn_system.turns.completed_turn(
        task.id, Role.LEADER, _request(task, ReportKind.FEEDBACK_ANALYSIS, feedback)
    )

    assert found is None
