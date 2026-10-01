"""`poe soak`: a seeded chaos run of the real engine; exits non-zero on any violation."""

from __future__ import annotations

import argparse
import asyncio
import secrets
import sys

from tests.soak.driver import ARENA_MODES, DEFAULT_BUDGET_SECONDS, Timing, run_soak
from tests.soak.model import IntegrationMode

SEED_BYTES = 4


def main() -> int:
    parser = argparse.ArgumentParser(prog="poe soak", description=__doc__)
    parser.add_argument(
        "--seed", default=None, help="replays a run; printed at the start of every run"
    )
    parser.add_argument("--budget-seconds", type=float, default=DEFAULT_BUDGET_SECONDS)
    parser.add_argument(
        "--mode",
        action="append",
        choices=[mode.value for mode in IntegrationMode],
        help="an arena per mode (default: one of each)",
    )
    arguments = parser.parse_args()
    seed = arguments.seed or secrets.token_hex(SEED_BYTES)
    modes = tuple(
        IntegrationMode(mode) for mode in arguments.mode or [mode.value for mode in ARENA_MODES]
    )
    result = asyncio.run(
        run_soak(seed, timing=Timing.of_budget(arguments.budget_seconds), modes=modes)
    )
    return 0 if result.clean else 1


if __name__ == "__main__":
    sys.exit(main())
