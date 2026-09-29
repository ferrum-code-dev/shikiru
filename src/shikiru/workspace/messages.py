"""Queues operator messages per role and delivers them into the next turn (03 §4.3, §9)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.paths import MESSAGES_DIR_NAME, queued_messages_dir, run_dir

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.domain.tasks import Role

MESSAGE_NUMBER_WIDTH = 3
MESSAGE_SUFFIX = ".md"
MESSAGE_KEY_SEPARATOR = "-"
PARTIAL_SUFFIX = ".partial"
COMMAND_KEY_PREFIX = "c"


class OperatorMessages:
    """Files the Operator's messages under `runs/T/messages/<role>/NNN.md`."""

    def __init__(self, project_root: Path) -> None:
        """Queue messages under `project_root`'s run directories."""
        self._project_root = project_root

    def queue(self, task_id: str, role: Role, text: str, key: str) -> Path:
        """Write `text` as the role's next numbered message and return its path.

        The file name carries `key`; a message already filed under `key`, queued or delivered,
        is returned instead of being written again, so a replayed request queues it once.
        """
        existing = self._find(task_id, key)
        if existing is not None:
            return existing
        directory = queued_messages_dir(self._project_root, task_id, role.value)
        directory.mkdir(parents=True, exist_ok=True)
        number = max((_number(path) for path in _messages(directory)), default=0) + 1
        path = _message_path(directory, number, key)
        _write_atomically(path, text)
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

    def requeue(self, task_id: str, role: Role, turn_dir: Path) -> None:
        """Return `turn_dir`'s delivered messages to the role's queue, ahead of newer ones."""
        delivered_dir = turn_dir / MESSAGES_DIR_NAME
        delivered = _messages(delivered_dir) if delivered_dir.is_dir() else []
        if not delivered:
            return
        directory = queued_messages_dir(self._project_root, task_id, role.value)
        directory.mkdir(parents=True, exist_ok=True)
        pending = [*delivered, *_messages(directory)]
        entries = [(_key(path), path.read_text()) for path in pending]
        for path in pending:
            path.unlink()
        delivered_dir.rmdir()
        for number, (key, text) in enumerate(entries, start=1):
            _write_atomically(_message_path(directory, number, key), text)

    def _find(self, task_id: str, key: str) -> Path | None:
        pattern = f"*{MESSAGE_KEY_SEPARATOR}{key}{MESSAGE_SUFFIX}"
        return next(run_dir(self._project_root, task_id).rglob(pattern), None)


def command_message_key(command_id: int) -> str:
    """Return the message key that ties a filed message to the command that queued it."""
    return f"{COMMAND_KEY_PREFIX}{command_id}"


def _number(path: Path) -> int:
    return int(path.stem.partition(MESSAGE_KEY_SEPARATOR)[0])


def _key(path: Path) -> str:
    return path.stem.partition(MESSAGE_KEY_SEPARATOR)[2]


def _write_atomically(path: Path, text: str) -> None:
    partial = path.with_name(path.name + PARTIAL_SUFFIX)
    partial.write_text(text)
    partial.replace(path)


def _messages(directory: Path) -> list[Path]:
    return sorted(directory.glob(f"*{MESSAGE_SUFFIX}"))


def _message_path(directory: Path, number: int, key: str) -> Path:
    name = f"{number:0{MESSAGE_NUMBER_WIDTH}d}{MESSAGE_KEY_SEPARATOR}{key}{MESSAGE_SUFFIX}"
    return directory / name
