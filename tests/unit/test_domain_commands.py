from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from shikiru.domain.commands import (
    AcceptResolution,
    AnswerResolution,
    CancelResolution,
    Command,
    Resolve,
    RetryResolution,
)

_COMMAND_ADAPTER: TypeAdapter[Command] = TypeAdapter(Command)


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


def test_command_union_discriminates_by_kind() -> None:
    submit = _COMMAND_ADAPTER.validate_python(
        {"kind": "submit", "task_id": "T-1", "title": "x", "request": "x"}
    )

    assert submit.kind == "submit"


def test_command_union_rejects_an_unknown_kind() -> None:
    with pytest.raises(ValidationError):
        _COMMAND_ADAPTER.validate_python({"kind": "bogus"})
