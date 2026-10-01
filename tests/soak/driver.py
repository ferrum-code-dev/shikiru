"""The soak driver: arenas side by side under one seed and one wall-clock budget."""

from __future__ import annotations

import asyncio
import shutil
import tempfile
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from tests.soak import processes
from tests.soak.arena import Arena
from tests.soak.model import IntegrationMode, Tally, Violation
from tests.soak.reporter import ArenaSummary, Reporter
from tests.soak.run import ArenaRun
from tests.soak.scenarios import Schedule, build_schedule

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine


DEFAULT_BUDGET_SECONDS = 480.0
INTAKE_SHARE = 0.6
FINISH_ALLOWANCE_SECONDS = 40.0
DIRECTORY_PREFIX = "shikiru-soak-"
ARENA_MODES = (IntegrationMode.MERGE, IntegrationMode.PR)


def seeded_schedule(seed: str, mode: IntegrationMode, intake_seconds: float) -> Schedule:
    return build_schedule(seed, mode, intake_seconds=intake_seconds)


@dataclass(frozen=True, slots=True)
class Timing:
    """How long tasks come in, and how long the run then waits for them to end."""

    intake_seconds: float
    drain_seconds: float

    @classmethod
    def of_budget(cls, budget_seconds: float) -> Timing:
        intake = budget_seconds * INTAKE_SHARE
        return cls(intake, max(budget_seconds - intake - FINISH_ALLOWANCE_SECONDS, 0.0))


@dataclass(frozen=True, slots=True)
class SoakResult:
    clean: bool
    reporter: Reporter


async def run_soak(
    seed: str,
    *,
    timing: Timing | None = None,
    modes: tuple[IntegrationMode, ...] = ARENA_MODES,
    schedule_for: Callable[[str, IntegrationMode, float], Schedule] = seeded_schedule,
    reporter: Reporter | None = None,
) -> SoakResult:
    reporter = reporter or Reporter(seed)
    timing = timing or Timing.of_budget(DEFAULT_BUDGET_SECONDS)
    reporter.say(f"seed {seed}, {timing}, arenas {[mode.value for mode in modes]}")
    base = Path(tempfile.mkdtemp(prefix=DIRECTORY_PREFIX))
    arenas: list[Arena] = []
    try:
        for index, mode in enumerate(modes):
            name = f"{mode.value}{index}"
            arenas.append(await Arena.create(base / name, name, mode))
        runs: list[Coroutine[None, None, ArenaSummary]] = []
        for arena in arenas:
            schedule = schedule_for(
                f"{seed}:{arena.name}", arena.integration, timing.intake_seconds
            )
            reporter.announce(arena.name, arena.integration.value, schedule)
            runs.append(
                _guarded(
                    ArenaRun(arena, schedule, seed, reporter, drain_seconds=timing.drain_seconds),
                    arena,
                    reporter,
                )
            )
        reporter.summaries.extend(await asyncio.gather(*runs))
    finally:
        await _clean_up(arenas, base, reporter)
    return SoakResult(reporter.finish(), reporter)


async def _guarded(run: ArenaRun, arena: Arena, reporter: Reporter) -> ArenaSummary:
    try:
        return await run.run()
    except Exception as error:  # noqa: BLE001 -- the soak reports its own failure as a finding
        message = f"the soak itself failed: {_describe(error)}"
        reporter.violation(arena.name, Violation(0, None, message))
        return ArenaSummary(arena.name, Tally())


async def _clean_up(arenas: list[Arena], base: Path, reporter: Reporter) -> None:
    for arena in arenas:
        killed = await arena.kill_everything()
        arena.release_database()
        if killed:
            reporter.say(f"{arena.name}: had to kill leftover processes {killed}")
    leftovers = processes.owned_by(base)
    if leftovers:
        reporter.violation(
            "soak",
            Violation(5, None, f"processes survived the cleanup: {[p.pid for p in leftovers]}"),
        )
        processes.kill_all(leftovers)
    if reporter.violations:
        reporter.say(f"state kept for diagnosis at {base}")
    else:
        shutil.rmtree(base, ignore_errors=True)


def _describe(error: BaseException) -> str:
    return "".join(traceback.format_exception(error))
