"""Free Codex sandbox probe: a real `codex sandbox` run in a scratch linked worktree."""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from shikiru.agents.contracts import CheckResult
from shikiru.errors import ShikiruError
from shikiru.plugins.agents.codex.options import (
    CODEX_COMMAND,
    END_OF_OPTIONS_FLAG,
    GIT_COMMAND,
    GIT_DIRS_ARGV,
    SANDBOX_PROBE_PARENT,
    SANDBOX_PROBE_PREFIX,
    SANDBOX_PROBE_TIMEOUT_SECONDS,
)
from shikiru.plugins.agents.codex.sandbox import sandbox_config_args

if TYPE_CHECKING:
    from shikiru.process import SubprocessRunner

SANDBOX_CHECK = "sandbox"
_SANDBOX_SUBCOMMAND = "sandbox"
_WORKTREE_DIR = "worktree"
_RUN_DIR = "run"
_MAIN_BRANCH = "main"
_PROBE_BRANCH = "probe"
_PROBE_FILE = ".shikiru-probe"
_GIT_IDENTITY = ("-c", "user.name=shikiru", "-c", "user.email=shikiru@localhost")
_TOUCH_SCRIPT = (
    'for directory; do touch "$directory/'
    + _PROBE_FILE
    + '" 2>/dev/null || echo "$directory"; done'
)
_SHELL_ARGV = ("sh", "-c", _TOUCH_SCRIPT, "sh")


class CodexSandboxProbe:
    """Check that a linked worktree's git directory and the run directory are writable."""

    def __init__(self, runner: SubprocessRunner) -> None:
        self._runner = runner

    async def check(self) -> CheckResult:
        """Run the probe outside `$TMPDIR` and report every root the sandbox kept read-only."""
        parent = Path.home().joinpath(*SANDBOX_PROBE_PARENT)
        await asyncio.to_thread(parent.mkdir, parents=True, exist_ok=True)
        base = Path(
            await asyncio.to_thread(tempfile.mkdtemp, prefix=SANDBOX_PROBE_PREFIX, dir=parent)
        )
        try:
            return await self._probe(base)
        except (OSError, ShikiruError) as error:
            return CheckResult(SANDBOX_CHECK, False, str(error))
        finally:
            await asyncio.to_thread(shutil.rmtree, base, ignore_errors=True)

    async def _probe(self, base: Path) -> CheckResult:
        repository, worktree, run_dir = base / "repo", base / _WORKTREE_DIR, base / _RUN_DIR
        run_dir.mkdir()
        await self._git(base, "init", "-q", "-b", _MAIN_BRANCH, str(repository))
        await self._git(repository, *_GIT_IDENTITY, "commit", "-q", "--allow-empty", "-m", "probe")
        await self._git(repository, "worktree", "add", "-q", "-b", _PROBE_BRANCH, str(worktree))
        git_dirs = (await self._git(worktree, *GIT_DIRS_ARGV[1:])).split()
        targets = (*git_dirs, str(run_dir))
        result = await self._runner.run(
            (
                CODEX_COMMAND,
                _SANDBOX_SUBCOMMAND,
                *sandbox_config_args((run_dir, *map(Path, git_dirs))),
                END_OF_OPTIONS_FLAG,
                *_SHELL_ARGV,
                *targets,
            ),
            timeout=SANDBOX_PROBE_TIMEOUT_SECONDS,
            cwd=worktree,
            check=False,
        )
        if result.returncode != 0:
            return CheckResult(SANDBOX_CHECK, False, result.stderr.strip())
        denied = result.stdout.split()
        if denied:
            return CheckResult(
                SANDBOX_CHECK, False, f"not writable under the sandbox: {', '.join(denied)}"
            )
        return CheckResult(SANDBOX_CHECK, True, "worktree git directory and run directory writable")

    async def _git(self, cwd: Path, *args: str) -> str:
        result = await self._runner.run(
            (GIT_COMMAND, *args), timeout=SANDBOX_PROBE_TIMEOUT_SECONDS, cwd=cwd, check=True
        )
        return result.stdout
