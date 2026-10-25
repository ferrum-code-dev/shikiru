"""Queues operator messages per role and delivers them into the next turn."""

from __future__ import annotations

from itertools import chain
from typing import TYPE_CHECKING

from shikiru.paths import MESSAGES_DIR_NAME, queued_messages_dir, run_dir, turns_dir
from shikiru.private import make_private_dir, write_private_text

if TYPE_CHECKING:
    from collections.abc import Iterator
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
        make_private_dir(directory)
        number = self._last_number(task_id) + 1
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
        make_private_dir(target)
        return [path.rename(target / path.name) for path in queued]

    def requeue(self, task_id: str, role: Role, turn_dir: Path) -> None:
        """Return `turn_dir`'s delivered messages to the role's queue, ahead of newer ones.

        Each message moves back under its own name, and numbers only grow, so the queue order is
        the filing order; one that has not moved yet when the process dies is returned by the
        next call, never lost.
        """
        delivered_dir = turn_dir / MESSAGES_DIR_NAME
        delivered = _messages(delivered_dir) if delivered_dir.is_dir() else []
        if not delivered:
            return
        directory = queued_messages_dir(self._project_root, task_id, role.value)
        make_private_dir(directory)
        for path in delivered:
            path.rename(directory / path.name)
        delivered_dir.rmdir()

    def _filed(self, task_id: str, pattern: str = f"*{MESSAGE_SUFFIX}") -> Iterator[Path]:
        queued = run_dir(self._project_root, task_id).glob(f"{MESSAGES_DIR_NAME}/*/{pattern}")
        delivered = turns_dir(self._project_root, task_id).glob(f"*/{MESSAGES_DIR_NAME}/{pattern}")
        return chain(queued, delivered)

    def _last_number(self, task_id: str) -> int:
        return max((_number(path) for path in self._filed(task_id)), default=0)

    def _find(self, task_id: str, key: str) -> Path | None:
        return next(self._filed(task_id, f"*{MESSAGE_KEY_SEPARATOR}{key}{MESSAGE_SUFFIX}"), None)


def command_message_key(command_id: int) -> str:
    """Return the message key that ties a filed message to the command that queued it."""
    return f"{COMMAND_KEY_PREFIX}{command_id}"


def _number(path: Path) -> int:
    return int(path.stem.partition(MESSAGE_KEY_SEPARATOR)[0])


def _write_atomically(path: Path, text: str) -> None:
    partial = path.with_name(path.name + PARTIAL_SUFFIX)
    write_private_text(partial, text)
    partial.replace(path)


def _messages(directory: Path) -> list[Path]:
    return sorted(directory.glob(f"*{MESSAGE_SUFFIX}"))


def _message_path(directory: Path, number: int, key: str) -> Path:
    name = f"{number:0{MESSAGE_NUMBER_WIDTH}d}{MESSAGE_KEY_SEPARATOR}{key}{MESSAGE_SUFFIX}"
    return directory / name
