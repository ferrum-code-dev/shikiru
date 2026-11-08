"""Slows and counts the test process's subprocess spawns, to reproduce a slow machine."""

from __future__ import annotations

import asyncio
import time
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from shikiru.process import SubprocessRunner

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Generator, Mapping, Sequence
    from typing import Concatenate

SPAWN_DELAY_ENV = "SHIKIRU_TEST_SPAWN_DELAY"
SPAWN_COUNT_FILE_ENV = "SHIKIRU_TEST_SPAWN_COUNT_FILE"
_NO_DELAY_SECONDS = 0.0
_TEXT_ENCODING = "utf-8"


@contextmanager
def spawn_hook(environ: Mapping[str, str]) -> Generator[None]:
    """Delay and count `SubprocessRunner`'s spawns as `environ` asks, undone on exit.

    With neither variable set nothing is wrapped. A delay is applied on the real clock before
    each spawn, including the synchronous `spawn_detached`, which blocks for it.
    """
    delay_text = environ.get(SPAWN_DELAY_ENV)
    count_text = environ.get(SPAWN_COUNT_FILE_ENV)
    if delay_text is None and count_text is None:
        yield
        return
    delay = _parse_delay(delay_text)
    count_file = None if count_text is None else Path(count_text)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(SubprocessRunner, "run", _slowed(SubprocessRunner.run, delay, count_file))
        patch.setattr(SubprocessRunner, "spawn", _slowed(SubprocessRunner.spawn, delay, count_file))
        patch.setattr(
            SubprocessRunner,
            "spawn_terminal",
            _slowed(SubprocessRunner.spawn_terminal, delay, count_file),
        )
        patch.setattr(
            SubprocessRunner,
            "spawn_detached",
            _slowed_sync(SubprocessRunner.spawn_detached, delay, count_file),
        )
        yield


def _parse_delay(text: str | None) -> float:
    if text is None:
        return _NO_DELAY_SECONDS
    delay = float(text)
    if delay < _NO_DELAY_SECONDS:
        message = f"{SPAWN_DELAY_ENV} must not be negative, got {text!r}"
        raise ValueError(message)
    return delay


def _record_spawn(argv: Sequence[str], count_file: Path | None) -> None:
    if count_file is not None:
        with count_file.open("a", encoding=_TEXT_ENCODING) as handle:
            handle.write(f"{Path(argv[0]).name}\n")


def _slowed[**P, R](
    original: Callable[Concatenate[SubprocessRunner, Sequence[str], P], Awaitable[R]],
    delay: float,
    count_file: Path | None,
) -> Callable[Concatenate[SubprocessRunner, Sequence[str], P], Awaitable[R]]:
    @wraps(original)
    async def slowed(
        self: SubprocessRunner, argv: Sequence[str], *args: P.args, **kwargs: P.kwargs
    ) -> R:
        _record_spawn(argv, count_file)
        if delay:
            await asyncio.sleep(delay)
        return await original(self, argv, *args, **kwargs)

    return slowed


def _slowed_sync[**P, R](
    original: Callable[Concatenate[SubprocessRunner, Sequence[str], P], R],
    delay: float,
    count_file: Path | None,
) -> Callable[Concatenate[SubprocessRunner, Sequence[str], P], R]:
    @wraps(original)
    def slowed(self: SubprocessRunner, argv: Sequence[str], *args: P.args, **kwargs: P.kwargs) -> R:
        _record_spawn(argv, count_file)
        time.sleep(delay)
        return original(self, argv, *args, **kwargs)

    return slowed
