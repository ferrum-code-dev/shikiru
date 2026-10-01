"""Concurrent creators of the same private directories never fail (01 §6)."""

from __future__ import annotations

import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from shikiru.private import PRIVATE_DIR_MODE, make_private_dir

if TYPE_CHECKING:
    from pathlib import Path

_CREATORS = 8
_ROUNDS = 40


def test_concurrent_creators_of_one_private_directory_all_succeed(tmp_path: Path) -> None:
    for round_number in range(_ROUNDS):
        target = tmp_path / f"round-{round_number}" / "a" / "b" / "c"
        barrier = threading.Barrier(_CREATORS)

        def create(target: Path = target, barrier: threading.Barrier = barrier) -> None:
            barrier.wait()
            make_private_dir(target)

        with ThreadPoolExecutor(_CREATORS) as pool:
            for future in [pool.submit(create) for _ in range(_CREATORS)]:
                future.result()

        assert stat.S_IMODE(target.stat().st_mode) == PRIVATE_DIR_MODE
