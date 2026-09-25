"""The only place git argv is built: worktrees, branches and facts (03 §5-6)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from shikiru.errors import CommandFailed

if TYPE_CHECKING:
    from shikiru.process import ProcessResult, SubprocessRunner

_GIT_BIN = "git"
_COMMAND_TIMEOUT_SECONDS = 30.0
_WORKTREE_LIST_PREFIX = "worktree "
_STATUS_IGNORED_PREFIX = "!! "
_STATUS_PATH_OFFSET = 3
_RENAME_COPY_CODES = frozenset(("R", "C"))

_MERGE_BASE_IS_ANCESTOR = 0
_MERGE_BASE_IS_NOT_ANCESTOR = 1


class Git:
    """A small, intention-revealing wrapper over the git CLI, always through `SubprocessRunner`."""

    def __init__(self, runner: SubprocessRunner) -> None:
        """Run every git command through `runner`, never a shell."""
        self._runner = runner

    async def worktree_add(
        self, repo_dir: Path, worktree_path: Path, *, new_branch: str, base: str
    ) -> None:
        """Add a worktree at `worktree_path` on new branch `new_branch`, starting from `base`."""
        await self._run(repo_dir, "worktree", "add", "-b", new_branch, str(worktree_path), base)

    async def worktree_add_existing_branch(
        self, repo_dir: Path, worktree_path: Path, branch: str
    ) -> None:
        """Add a worktree at `worktree_path` checking out the already-existing `branch`."""
        await self._run(repo_dir, "worktree", "add", str(worktree_path), branch)

    async def worktree_remove(self, repo_dir: Path, worktree_path: Path) -> None:
        """Remove the worktree at `worktree_path`, discarding any local changes in it."""
        await self._run(repo_dir, "worktree", "remove", "--force", str(worktree_path))

    async def worktree_exists(self, repo_dir: Path, worktree_path: Path) -> bool:
        """Return whether `worktree_path` is a registered worktree of the repo at `repo_dir`."""
        return worktree_path.resolve() in await self._worktree_paths(repo_dir)

    async def branch_exists(self, repo_dir: Path, branch: str) -> bool:
        """Return whether local branch `branch` exists in the repo at `repo_dir`."""
        result = await self._run(
            repo_dir, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", check=False
        )
        return result.returncode == 0

    async def delete_branch(self, repo_dir: Path, branch: str) -> None:
        """Force-delete local branch `branch` in the repo at `repo_dir`."""
        await self._run(repo_dir, "branch", "-D", branch)

    async def current_branch(self, repo_dir: Path) -> str:
        """Return the name of the branch currently checked out in `repo_dir`."""
        result = await self._run(repo_dir, "rev-parse", "--abbrev-ref", "HEAD")
        return result.stdout.strip()

    async def is_repository_root(self, path: Path) -> bool:
        """Return whether `path` is the top-level directory of a git working tree."""
        result = await self._run(path, "rev-parse", "--show-toplevel", check=False)
        return result.returncode == 0 and Path(result.stdout.strip()).resolve() == path.resolve()

    async def rev_parse(self, repo_dir: Path, ref: str) -> str:
        """Resolve `ref` to a commit sha in the repo at `repo_dir`."""
        result = await self._run(repo_dir, "rev-parse", ref)
        return result.stdout.strip()

    async def log_oneline(self, repo_dir: Path, commit_range: str) -> list[str]:
        """Return one line per commit in `commit_range` (oldest first as git orders them)."""
        result = await self._run(repo_dir, "log", "--oneline", commit_range)
        return [line for line in result.stdout.splitlines() if line]

    async def diff_stat(self, repo_dir: Path, commit_range: str) -> str:
        """Return `git diff --stat` for `commit_range`, verbatim.

        This output is display-formatted for humans: long paths are elided to `...` and
        renames are shown as `old => new`. Never parse paths out of it; use `diff_name_only`.
        """
        result = await self._run(repo_dir, "diff", "--stat", commit_range)
        return result.stdout

    async def diff_name_only(self, repo_dir: Path, commit_range: str) -> list[str]:
        """Return the full, unformatted path of every file changed in `commit_range`."""
        result = await self._run(repo_dir, "diff", "--name-only", commit_range)
        return [line for line in result.stdout.splitlines() if line]

    async def status_porcelain(self, repo_dir: Path, *, include_ignored: bool = False) -> list[str]:
        """Return one `git status --porcelain` line per entry, optionally including ignored ones."""
        args = ["status", "--porcelain"]
        if include_ignored:
            args.append("--ignored")
        result = await self._run(repo_dir, *args)
        return [line for line in result.stdout.splitlines() if line]

    async def ignored_paths(self, repo_dir: Path) -> list[Path]:
        """Return every top-level path git ignores in `repo_dir` (from `status --ignored`).

        Not exhaustive: git reports an ignored directory itself but not paths nested inside
        another ignored directory, so a path ignored only because its parent is ignored will
        not appear here. This matches the documented cache globs, which target the ignored
        directory directly (e.g. `node_modules`), not paths beneath it.
        """
        lines = await self.status_porcelain(repo_dir, include_ignored=True)
        paths: list[Path] = []
        for line in lines:
            if not line.startswith(_STATUS_IGNORED_PREFIX):
                continue
            relative = line[len(_STATUS_IGNORED_PREFIX) :].strip().rstrip("/")
            paths.append(repo_dir / relative)
        return paths

    async def merge_base_is_ancestor(self, repo_dir: Path, ancestor: str, descendant: str) -> bool:
        """Return whether `ancestor` is an ancestor of `descendant` (or the same commit)."""
        result = await self._run(
            repo_dir, "merge-base", "--is-ancestor", ancestor, descendant, check=False
        )
        if result.returncode not in (_MERGE_BASE_IS_ANCESTOR, _MERGE_BASE_IS_NOT_ANCESTOR):
            raise CommandFailed(result.argv, result.returncode, result.stderr)
        return result.returncode == _MERGE_BASE_IS_ANCESTOR

    async def rebase(self, repo_dir: Path, base: str) -> None:
        """Rebase the current branch onto `base`, raising when git reports a conflict."""
        await self._run(repo_dir, "rebase", base)

    async def rebase_continue(self, repo_dir: Path) -> None:
        """Continue a rebase after its conflicts have been resolved and staged."""
        await self._run(repo_dir, "-c", "core.editor=true", "rebase", "--continue")

    async def merge_ff_only(self, repo_dir: Path, branch: str) -> None:
        """Fast-forward the checked-out base branch to `branch` only."""
        await self._run(repo_dir, "merge", "--ff-only", branch)

    async def update_ref(
        self, repo_dir: Path, branch: str, new_sha: str, expected_old_sha: str
    ) -> None:
        """Atomically move `branch` from `expected_old_sha` to `new_sha`."""
        await self._run(
            repo_dir,
            "update-ref",
            f"refs/heads/{branch}",
            new_sha,
            expected_old_sha,
        )

    async def dirty_paths(self, repo_dir: Path) -> set[str]:
        """Return paths changed in the working tree, including untracked paths."""
        result = await self._run(repo_dir, "status", "--porcelain=v1", "-z")
        entries = result.stdout.split("\0")
        paths: set[str] = set()
        index = 0
        while index < len(entries):
            entry = entries[index]
            index += 1
            if not entry:
                continue
            status = entry[:2]
            path = entry[_STATUS_PATH_OFFSET:]
            paths.add(path)
            if any(code in _RENAME_COPY_CODES for code in status) and index < len(entries):
                paths.add(entries[index])
                index += 1
        return paths

    async def is_rebase_in_progress(self, repo_dir: Path) -> bool:
        """Return whether a rebase operation is currently in progress."""
        merge_dir = await self._git_path(repo_dir, "rebase-merge")
        apply_dir = await self._git_path(repo_dir, "rebase-apply")
        return merge_dir.exists() or apply_dir.exists()

    async def append_to_exclude(self, repo_dir: Path, line: str) -> None:
        """Append `line` to this repo's shared `info/exclude`, unless it is already there."""
        exclude_path = await self._exclude_path(repo_dir)
        text = exclude_path.read_text() if exclude_path.exists() else ""
        if line in text.splitlines():
            return
        with exclude_path.open("a") as handle:
            if text and not text.endswith("\n"):
                handle.write("\n")
            handle.write(f"{line}\n")

    async def _exclude_path(self, repo_dir: Path) -> Path:
        result = await self._run(
            repo_dir, "rev-parse", "--path-format=absolute", "--git-common-dir"
        )
        git_common_dir = result.stdout.strip()
        return Path(git_common_dir) / "info" / "exclude"

    async def _git_path(self, repo_dir: Path, name: str) -> Path:
        result = await self._run(repo_dir, "rev-parse", "--git-path", name)
        path = Path(result.stdout.strip())
        return path if path.is_absolute() else repo_dir / path

    async def _worktree_paths(self, repo_dir: Path) -> set[Path]:
        result = await self._run(repo_dir, "worktree", "list", "--porcelain")
        paths: set[Path] = set()
        for line in result.stdout.splitlines():
            if line.startswith(_WORKTREE_LIST_PREFIX):
                paths.add(Path(line[len(_WORKTREE_LIST_PREFIX) :]).resolve())
        return paths

    async def _run(self, repo_dir: Path, *args: str, check: bool = True) -> ProcessResult:
        return await self._runner.run(
            [_GIT_BIN, *args], timeout=_COMMAND_TIMEOUT_SECONDS, cwd=repo_dir, check=check
        )
