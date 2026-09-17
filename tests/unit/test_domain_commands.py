from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from shikiru.agents.contracts import Rung
from shikiru.domain.commands import (
    AcceptResolution,
    AnswerResolution,
    CancelResolution,
    Command,
    Pause,
    Resolve,
    RetryResolution,
    Say,
    SetRung,
    Submit,
)
from shikiru.domain.tasks import Mode, Role

_COMMAND_ADAPTER: TypeAdapter[Command] = TypeAdapter(Command)


def test_submit_carries_the_allocated_task_id_and_defaults() -> None:
    submit = Submit(task_id="T-1", title="Add retries", request="Add retries to the parser")

    assert submit.kind == "submit"
    assert submit.complexity == "normal"
    assert submit.mode is None
    assert submit.depends_on == []


def test_submit_accepts_every_flag() -> None:
    submit = Submit(
        task_id="T-1",
        title="Add retries",
        request="Add retries",
        mode=Mode.IMPLEMENT,
        complexity="high",
        priority=5,
        depends_on=["T-0"],
        leader=Rung(agent="claude", model="opus", effort="high"),
        implementer=Rung(agent="codex", model="sol", effort="high"),
    )

    assert submit.priority == 5
    assert submit.depends_on == ["T-0"]
    assert submit.leader is not None


def test_pause_defaults_now_to_false() -> None:
    assert Pause(task_id="T-1").now is False


def test_pause_now_can_be_set() -> None:
    assert Pause(task_id="T-1", now=True).now is True


def test_commands_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        Say.model_validate({"task_id": "T-1", "role": "leader", "text": "hi", "bogus": True})


def test_commands_are_frozen() -> None:
    command = SetRung(
        task_id="T-1", role=Role.LEADER, rung=Rung(agent="claude", model="opus", effort=None)
    )

    with pytest.raises(ValidationError):
        command.task_id = "T-2"


@pytest.mark.parametrize(
    ("payload", "expected_type"),
    [
        ({"kind": "answer", "text": "use snake_case"}, AnswerResolution),
        ({"kind": "retry"}, RetryResolution),
        ({"kind": "accept"}, AcceptResolution),
        ({"kind": "cancel"}, CancelResolution),
    ],
    ids=["answer", "retry", "accept", "cancel"],
)
def test_resolve_discriminates_the_resolution_kind(
    payload: dict[str, object], expected_type: type[object]
) -> None:
    resolve = Resolve.model_validate(
        {"kind": "resolve", "escalation_id": "E-1", "resolution": payload}
    )

    assert isinstance(resolve.resolution, expected_type)


def test_resolve_retry_accepts_an_optional_rung() -> None:
    resolve = Resolve.model_validate(
        {
            "kind": "resolve",
            "escalation_id": "E-1",
            "resolution": {"kind": "retry", "rung": {"agent": "claude", "model": "opus"}},
        }
    )

    assert isinstance(resolve.resolution, RetryResolution)
    assert resolve.resolution.rung == Rung(agent="claude", model="opus", effort=None)


def test_command_union_discriminates_by_kind() -> None:
    submit = _COMMAND_ADAPTER.validate_python(
        {"kind": "submit", "task_id": "T-1", "title": "x", "request": "x"}
    )

    assert isinstance(submit, Submit)


def test_command_union_rejects_an_unknown_kind() -> None:
    with pytest.raises(ValidationError):
        _COMMAND_ADAPTER.validate_python({"kind": "bogus"})
