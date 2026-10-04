"""Bounded projections of recorded run lineages and their turn identities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeGuard, cast

from shikiru.dashboard.contract import (
    TaskModelHistoryView,
    TaskSessionView,
    TaskTurnUsageDeltaView,
)
from shikiru.domain.events import Event, EventKind
from shikiru.domain.runs import Run, RunStatus, has_positive_token_evidence

if TYPE_CHECKING:
    from collections.abc import Sequence

TASK_MODEL_HISTORY_TRUNCATED = "earlier turn history may be omitted by the event limit"
TASK_MODEL_HISTORY_UNLINKED = "turn evidence could not be linked to a session lineage"
TASK_MODEL_HISTORY_MISSING = "no retained turn history is available"
TASK_MODEL_HISTORY_INCOMPLETE = "turn has no recorded end event"
TASK_MODEL_HISTORY_START_MISSING = "turn start is not in retained history"
TASK_MODEL_HISTORY_EFFORT_MISSING = "recorded turn effort is unavailable"
TASK_MODEL_HISTORY_DELTA_MISSING = "per-turn usage delta is unavailable"
TASK_MODEL_HISTORY_IDENTITY_CHANGED = (
    "turn identity is incomplete or inconsistent; per-turn usage delta was not attributed"
)
TASK_MODEL_HISTORY_READ_FAILED = "turn history could not be read"
EFFORT_FIELD = "effort"
TURN_IDENTITY_EFFORT_RECORDED_INDEX = 3
MODEL_HISTORY_STATUS_COMPLETED = "completed"
MODEL_HISTORY_STATUS_FAILED = "failed"
MODEL_HISTORY_STATUS_INTERRUPTED = "interrupted"
MODEL_HISTORY_STATUS_IDENTITY_UNCERTAIN = "identity uncertain"
_HISTORY_EVENTS = {
    EventKind.TURN_COMPLETED: MODEL_HISTORY_STATUS_COMPLETED,
    EventKind.TURN_FAILED: MODEL_HISTORY_STATUS_FAILED,
    EventKind.TURN_STOPPED: MODEL_HISTORY_STATUS_INTERRUPTED,
}


@dataclass
class _TurnHistory:
    run_id: int
    agent: str
    model: str
    effort: str | None
    turn: str | None
    status: str
    started: bool
    usage_delta: TaskTurnUsageDeltaView | None
    identity_complete: bool


class _TaskSessionHistory:
    def __init__(
        self,
        runs: Sequence[Run],
        *,
        history_truncated: bool,
        history_error: bool,
    ) -> None:
        self._runs = runs
        self._runs_by_id = {run.id: run for run in runs}
        self._history_truncated = history_truncated
        self._history_error = history_error
        self._history: dict[int, list[_TurnHistory]] = {run.id: [] for run in runs}
        self._gaps: dict[int, list[str]] = {run.id: [] for run in runs}
        self._unlinked_roles: set[str] = set()
        self._turns: dict[tuple[str, str], _TurnHistory] = {}

    def project(self, events: Sequence[Event]) -> tuple[TaskSessionView, ...]:
        """Return run rows decorated with bounded model observations and explicit gaps."""
        for event in reversed(events):
            self._append(event)
        self._finish_gaps()
        return tuple(
            self._session_view(run, run.status in (RunStatus.LIVE, RunStatus.TAKEN_OVER))
            for run in self._runs
        )

    def _append(self, event: Event) -> None:
        role = _event_string(event, "role")
        turn = _event_string(event, "turn")
        identity = _turn_identity(event, role)
        paired = self._turns.get((role, turn)) if role is not None and turn is not None else None
        if event.kind is EventKind.TURN_STARTED:
            self._start(event, role, turn, identity)
        elif event.kind in _HISTORY_EVENTS:
            self._end(event, role, turn, identity, paired)

    def _start(
        self,
        event: Event,
        role: str | None,
        turn: str | None,
        identity: tuple[str, str, str | None, bool] | None,
    ) -> None:
        run = self._run_for(event, role, identity)
        if run is None or identity is None:
            self._mark_unlinked(role)
            return
        agent, model, effort, effort_recorded = identity
        history = _TurnHistory(
            run_id=run.id,
            agent=agent,
            model=model,
            effort=effort,
            turn=turn,
            status=MODEL_HISTORY_STATUS_INTERRUPTED,
            started=True,
            usage_delta=None,
            identity_complete=effort_recorded,
        )
        self._history[run.id].append(history)
        if turn is not None:
            self._turns[(role or "", turn)] = history
        if not effort_recorded:
            self._gaps[run.id].append(TASK_MODEL_HISTORY_EFFORT_MISSING)

    def _end(
        self,
        event: Event,
        role: str | None,
        turn: str | None,
        identity: tuple[str, str, str | None, bool] | None,
        paired: _TurnHistory | None,
    ) -> None:
        if paired is not None and _event_run_matches(event, paired.run_id):
            self._finish_pair(event, paired, identity)
            return
        self._finish_unpaired(event, role, turn, identity)

    def _finish_pair(
        self,
        event: Event,
        paired: _TurnHistory,
        identity: tuple[str, str, str | None, bool] | None,
    ) -> None:
        end_state = _HISTORY_EVENTS[event.kind]
        if identity is not None and not _identity_matches(paired, identity):
            paired.status = MODEL_HISTORY_STATUS_IDENTITY_UNCERTAIN
            self._gaps[paired.run_id].append(TASK_MODEL_HISTORY_IDENTITY_CHANGED)
            self._record_end_identity(event, identity, end_state)
            return
        paired.status = end_state
        if event.kind is EventKind.TURN_COMPLETED:
            if (
                not paired.identity_complete
                or identity is None
                or not identity[TURN_IDENTITY_EFFORT_RECORDED_INDEX]
            ):
                self._gaps[paired.run_id].append(TASK_MODEL_HISTORY_IDENTITY_CHANGED)
            else:
                paired.usage_delta = _turn_usage_delta(event)
            if paired.usage_delta is None:
                self._gaps[paired.run_id].append(TASK_MODEL_HISTORY_DELTA_MISSING)

    def _record_end_identity(
        self,
        event: Event,
        identity: tuple[str, str, str | None, bool],
        status: str,
    ) -> None:
        run = self._run_for(event, _event_string(event, "role"), identity)
        if run is None:
            return
        agent, model, effort, effort_recorded = identity
        self._history[run.id].append(
            _TurnHistory(
                run_id=run.id,
                agent=agent,
                model=model,
                effort=effort,
                turn=_event_string(event, "turn"),
                status=status,
                started=False,
                usage_delta=None,
                identity_complete=effort_recorded,
            )
        )
        self._gaps[run.id].append(TASK_MODEL_HISTORY_IDENTITY_CHANGED)

    def _finish_unpaired(
        self,
        event: Event,
        role: str | None,
        turn: str | None,
        identity: tuple[str, str, str | None, bool] | None,
    ) -> None:
        run = self._run_for(event, role, identity)
        if run is None or identity is None:
            self._mark_unlinked(role)
            return
        agent, model, effort, effort_recorded = identity
        delta = _turn_usage_delta(event) if event.kind is EventKind.TURN_COMPLETED else None
        self._history[run.id].append(
            _TurnHistory(
                run_id=run.id,
                agent=agent,
                model=model,
                effort=effort,
                turn=turn,
                status=_HISTORY_EVENTS[event.kind],
                started=False,
                usage_delta=delta,
                identity_complete=effort_recorded,
            )
        )
        self._gaps[run.id].append(TASK_MODEL_HISTORY_START_MISSING)
        if not effort_recorded:
            self._gaps[run.id].append(TASK_MODEL_HISTORY_EFFORT_MISSING)
        if event.kind is EventKind.TURN_COMPLETED and delta is None:
            self._gaps[run.id].append(TASK_MODEL_HISTORY_DELTA_MISSING)

    def _run_for(
        self,
        event: Event,
        role: str | None,
        identity: tuple[str, str, str | None, bool] | None,
    ) -> Run | None:
        run_id = event.data.get("run_id")
        if isinstance(run_id, bool):
            return None
        if isinstance(run_id, int):
            run = self._runs_by_id.get(run_id)
            return run if run is not None and _run_matches(run, role, identity) else None
        if run_id is not None or role is None or identity is None:
            return None
        candidates = [
            run
            for run in self._runs
            if run.role.value == role
            and run.agent == identity[0]
            and run.started_at <= event.ts
            and (run.ended_at is None or event.ts <= run.ended_at)
        ]
        return candidates[0] if len(candidates) == 1 else None

    def _mark_unlinked(self, role: str | None) -> None:
        if role is not None:
            self._unlinked_roles.add(role)

    def _finish_gaps(self) -> None:
        for run in self._runs:
            gaps = self._gaps[run.id]
            if self._history_truncated:
                gaps.append(TASK_MODEL_HISTORY_TRUNCATED)
            if self._history_error:
                gaps.append(TASK_MODEL_HISTORY_READ_FAILED)
            if run.role.value in self._unlinked_roles:
                gaps.append(TASK_MODEL_HISTORY_UNLINKED)
            for history in self._history[run.id]:
                if history.started and history.status == MODEL_HISTORY_STATUS_INTERRUPTED:
                    gaps.append(TASK_MODEL_HISTORY_INCOMPLETE)
            if not self._history[run.id] and _run_has_turn_evidence(run):
                gaps.append(TASK_MODEL_HISTORY_MISSING)

    def _session_view(self, run: Run, current_lineage: bool) -> TaskSessionView:
        return TaskSessionView(
            role=run.role.value,
            agent=run.agent,
            model=run.model,
            effort=run.effort,
            session_id=run.session_id,
            status=run.status.value,
            input_tokens=run.usage.input_tokens,
            output_tokens=run.usage.output_tokens,
            cache_read_tokens=run.usage.cache_read_tokens,
            cache_write_tokens=run.usage.cache_write_tokens,
            cost_usd=run.usage.cost_usd,
            cost_basis=run.usage.cost_basis,
            context_used_pct=run.usage.context_used_pct,
            billing_mode=run.billing_mode,
            billing_source=run.billing_source,
            usage_reported=run.usage_reported,
            legacy_token_totals=(run.usage_reported is None and has_positive_token_evidence(run)),
            legacy_cost_recorded=(run.usage_reported is None and run.usage.cost_usd is not None),
            run_id=run.id,
            current_lineage=current_lineage,
            model_history=tuple(_history_view(item) for item in self._history[run.id]),
            history_gaps=tuple(dict.fromkeys(self._gaps[run.id])),
        )


def project_task_sessions(
    runs: Sequence[Run],
    events: Sequence[Event],
    *,
    history_truncated: bool,
    history_error: bool,
) -> tuple[TaskSessionView, ...]:
    """Project persisted session lineages and link bounded model history when evidence permits."""
    return _TaskSessionHistory(
        runs,
        history_truncated=history_truncated,
        history_error=history_error,
    ).project(events)


def _run_matches(
    run: Run,
    role: str | None,
    identity: tuple[str, str, str | None, bool] | None,
) -> bool:
    return (role is None or run.role.value == role) and (
        identity is None or run.agent == identity[0]
    )


def _event_run_matches(event: Event, run_id: int) -> bool:
    event_run_id = event.data.get("run_id")
    return event_run_id is None or (not isinstance(event_run_id, bool) and event_run_id == run_id)


def _identity_matches(
    history: _TurnHistory,
    identity: tuple[str, str, str | None, bool],
) -> bool:
    agent, model, effort, effort_recorded = identity
    if history.agent != agent or history.model != model:
        return False
    return not history.identity_complete or not effort_recorded or history.effort == effort


def _turn_identity(
    event: Event,
    role: str | None,
) -> tuple[str, str, str | None, bool] | None:
    agent = _event_string(event, "agent")
    model = _event_string(event, "model")
    if role is None or agent is None or model is None:
        return None
    effort = event.data.get(EFFORT_FIELD)
    if not isinstance(effort, str | None):
        return None
    return agent, model, effort, EFFORT_FIELD in event.data


def _event_string(event: Event, key: str) -> str | None:
    value = event.data.get(key)
    return value if isinstance(value, str) else None


def _turn_usage_delta(event: Event) -> TaskTurnUsageDeltaView | None:
    raw = event.data.get("usage_delta")
    if not isinstance(raw, dict):
        return None
    usage_data = cast("dict[str, object]", raw)
    input_tokens = usage_data.get("input_tokens")
    output_tokens = usage_data.get("output_tokens")
    cache_read_tokens = usage_data.get("cache_read_tokens")
    cache_write_tokens = usage_data.get("cache_write_tokens")
    if not (
        _is_token_count(input_tokens)
        and _is_token_count(output_tokens)
        and _is_token_count(cache_read_tokens)
        and _is_token_count(cache_write_tokens)
    ):
        return None
    cost = usage_data.get("cost_usd")
    if cost is not None and (isinstance(cost, bool) or not isinstance(cost, int | float)):
        return None
    return TaskTurnUsageDeltaView(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        cost_usd=float(cost) if cost is not None else None,
    )


def _is_token_count(value: object) -> TypeGuard[int]:
    return not isinstance(value, bool) and isinstance(value, int)


def _history_view(history: _TurnHistory) -> TaskModelHistoryView:
    return TaskModelHistoryView(
        agent=history.agent,
        model=history.model,
        effort=history.effort,
        turn=history.turn,
        status=history.status,
        usage_delta=history.usage_delta,
        identity_complete=history.identity_complete,
    )


def _run_has_turn_evidence(run: Run) -> bool:
    return (
        run.session_id is not None or run.usage_reported is True or has_positive_token_evidence(run)
    )
