"""A minimal `Task` factory shared by domain tests."""

from __future__ import annotations

from typing import Any

from shikiru.domain.tasks import Status, Task


def make_task(**overrides: Any) -> Task:
    fields: dict[str, Any] = {
        "id": "T-1",
        "seq": 1,
        "origin": "operator",
        "title": "Add retry to parser",
        "request": "Add retry to parser",
        "status": Status.RUNNING,
    }
    fields.update(overrides)
    return Task(**fields)
