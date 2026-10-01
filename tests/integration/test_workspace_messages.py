"""Operator message filing under a task's run directory (03 §4.3, §9)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.domain.tasks import Role
from shikiru.paths import run_dir, turns_dir
from shikiru.workspace.messages import OperatorMessages

if TYPE_CHECKING:
    from pathlib import Path

_TASK_ID = "T-1"
_KEY = "c7"


def test_only_a_file_in_a_message_directory_counts_as_the_message_filed_under_a_key(
    tmp_path: Path,
) -> None:
    unrelated = turns_dir(tmp_path, _TASK_ID) / "1" / "work" / f"009-{_KEY}.md"
    unrelated.parent.mkdir(parents=True)
    unrelated.write_text("an agent's own file\n")
    messages = OperatorMessages(tmp_path)

    filed = messages.queue(_TASK_ID, Role.IMPLEMENTER, "do this", _KEY)

    assert filed != unrelated
    assert filed.read_text() == "do this"
    assert filed.is_relative_to(run_dir(tmp_path, _TASK_ID))
    assert messages.queue(_TASK_ID, Role.IMPLEMENTER, "do this", _KEY) == filed
