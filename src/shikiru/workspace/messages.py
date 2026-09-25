"""Queues operator messages per role and delivers them into the next turn (03 §4.3, §9)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.paths import MESSAGES_DIR_NAME, queued_messages_dir

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.domain.tasks import Role

MESSAGE_NUMBER_WIDTH = 3
MESSAGE_SUFFIX = ".md"


class OperatorMessages:
    """Files the Operator's messages under `runs/T/messages/<role>/NNN.md`."""

    def __init__(self, project_root: Path) -> None:
        """Queue messages under `project_root`'s run directories."""
        self._project_root = project_root

    def queue(self, task_id: str, role: Role, text: str) -> Path:
        """Write `text` as the role's next numbered message and return its path."""
        directory = queued_messages_dir(self._project_root, task_id, role.value)
        directory.mkdir(parents=True, exist_ok=True)
        number = max((int(path.stem) for path in _messages(directory)), default=0) + 1
        path = directory / f"{number:0{MESSAGE_NUMBER_WIDTH}d}{MESSAGE_SUFFIX}"
        path.write_text(text)
        return path

    def deliver(self, task_id: str, role: Role, turn_dir: Path) -> list[Path]:
        """Move the role's queued messages into `turn_dir/messages/` and return them in order."""
        directory = queued_messages_dir(self._project_root, task_id, role.value)
        queued = _messages(directory) if directory.is_dir() else []
        if not queued:
            return []
        target = turn_dir / MESSAGES_DIR_NAME
        target.mkdir(parents=True, exist_ok=True)
        return [path.rename(target / path.name) for path in queued]


def _messages(directory: Path) -> list[Path]:
    return sorted(directory.glob(f"*{MESSAGE_SUFFIX}"))
