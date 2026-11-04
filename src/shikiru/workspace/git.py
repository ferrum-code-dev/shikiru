"""The only place git argv is built: worktrees, branches and facts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from shikiru.errors import CommandFailed, InvariantError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from shikiru.process import ProcessResult, SubprocessRunner

_GIT_BIN = "git"
_HEAD_REF = "HEAD"
_HEADS_REF_PREFIX = "refs/heads/"
_REMOTE_TRACKING_REF_PREFIX = "refs/remotes/"
_REFSPEC_SEPARATOR = ":"
_REMOTE_HEADS_OPTION = "--heads"
_REMOTE_REF_FIELDS = 2
_FORCE_REFSPEC_PREFIX = "+"
_PUSH_TIMEOUT_SECONDS = 300.0
GIT_STOP_GRACE = 10.0
_WORKTREE_LIST_PREFIX = "worktree "
_WORKTREE_BRANCH_PREFIX = "branch "
_WORKTREE_LOCKED_PREFIX = "locked"
_REMOVAL_LOCK_REASON = "shikiru: removal in progress"
_STATUS_IGNORED_PREFIX = "!! "
_STATUS_CODE_LENGTH = 2
_STATUS_PATH_OFFSET = _STATUS_CODE_LENGTH + 1
_PORCELAIN_FLAG = "--porcelain"
_RENAME_COPY_CODES = frozenset(("R", "C"))

_ENGINE_NAME = "Shikiru"
_ENGINE_EMAIL = "shikiru@localhost"

_MERGE_BASE_IS_ANCESTOR = 0
_MERGE_BASE_IS_NOT_ANCESTOR = 1
_NUMSTAT_BINARY_MARKER = "-"
_NUMSTAT_RENAME_PATH_RECORDS = 2
_NUMSTAT_SEPARATOR = "\t"
_NUMSTAT_RECORD_SEPARATOR = "\0"
_NUMSTAT_MALFORMED_RECORD = "git numstat returned a malformed record"


@dataclass(frozen=True, slots=True)
class LineChangeStats:
    """Distinct changed paths, their line additions and deletions, binary and untracked paths."""

    changed_paths: int
    added_lines: int
    deleted_lines: int
    binary_entries: int
    untracked_paths: int


class Git:
    """A small, intention-revealing wrapper over the git CLI, always through `SubprocessRunner`."""

    def __init__(self, runner: SubprocessRunner, *, command_timeout: float) -> None:
        """Run every git command through `runner`, never a shell, each within `command_timeout`."""
        self._runner = runner
        self._command_timeout = command_timeout

    async def worktree_add(
        self, repo_dir: Path, worktree_path: Path, *, new_branch: str, base: str
    ) -> None:
        """Add a worktree at `worktree_path` on new branch `new_branch`, starting from `base`."""
        await self._run(
            repo_dir,
            "worktree",
            "add",
            "-b",
            new_branch,
            str(worktree_path),
            base,
            mutating=True,
        )

    async def worktree_add_existing_branch(
        self, repo_dir: Path, worktree_path: Path, branch: str
    ) -> None:
        """Add a worktree at `worktree_path` checking out the already-existing `branch`."""
        await self._run(repo_dir, "worktree", "add", str(worktree_path), branch, mutating=True)

    async def worktree_remove(self, repo_dir: Path, worktree_path: Path) -> None:
        """Remove the worktree at `worktree_path`, discarding any local changes in it.

        The worktree is first locked as being removed, so a removal that fails or times out
        midway leaves a state `worktree_removal_pending` recognises, and a later call finishes
        it.
        """
        if not await self.worktree_removal_pending(repo_dir, worktree_path):
            await self._run(
                repo_dir,
                "worktree",
                "lock",
                "--reason",
                _REMOVAL_LOCK_REASON,
                str(worktree_path),
                mutating=True,
            )
        await self._run(
            repo_dir,
            "worktree",
            "remove",
            "--force",
            "--force",
            str(worktree_path),
            mutating=True,
        )

    async def worktree_removal_pending(self, repo_dir: Path, worktree_path: Path) -> bool:
        """Return whether a removal of `worktree_path` began and did not finish."""
        reasons = await self._worktree_lock_reasons(repo_dir)
        return reasons.get(worktree_path.resolve()) == _REMOVAL_LOCK_REASON

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
        await self._run(repo_dir, "branch", "-D", branch, mutating=True)

    async def current_branch(self, repo_dir: Path) -> str:
        """Return the name of the branch currently checked out in `repo_dir`."""
        result = await self._run(repo_dir, "rev-parse", "--abbrev-ref", "HEAD")
        return result.stdout.strip()

    async def head_branch(self, repo_dir: Path) -> str | None:
        """Return the branch `HEAD` is on in `repo_dir`, or `None` when it is detached."""
        result = await self._run(
            repo_dir, "symbolic-ref", "--quiet", "--short", "HEAD", check=False
        )
        return result.stdout.strip() if result.returncode == 0 else None

    async def is_repository_root(self, path: Path) -> bool:
        """Return whether `path` is the top-level directory of a git working tree."""
        return await self.repository_root(path) == path.resolve()

    async def repository_root(self, path: Path) -> Path | None:
        """Return the top-level directory containing `path`, or `None` outside a worktree."""
        result = await self._run(path, "rev-parse", "--show-toplevel", check=False)
        if result.returncode != 0:
            return None
        return Path(result.stdout.strip()).resolve()

    async def has_commit(self, repo_dir: Path) -> bool:
        """Return whether `repo_dir` has a commit at `HEAD`."""
        result = await self._run(
            repo_dir, "rev-parse", "--verify", "--quiet", _HEAD_REF, check=False
        )
        return result.returncode == 0

    async def rev_parse(self, repo_dir: Path, ref: str) -> str:
        """Resolve `ref` to a commit sha in the repo at `repo_dir`."""
        result = await self._run(repo_dir, "rev-parse", ref)
        return result.stdout.strip()

    async def log_oneline(self, repo_dir: Path, commit_range: str) -> list[str]:
        """Return one line per commit in `commit_range` (oldest first as git orders them)."""
        result = await self._run(repo_dir, "log", "--oneline", commit_range)
        return [line for line in result.stdout.splitlines() if line]

    async def commit_parents(self, repo_dir: Path, commit: str) -> tuple[str, ...]:
        """Return the parents of `commit` in their recorded order."""
        result = await self._run(repo_dir, "rev-list", "--parents", "-n", "1", commit)
        return tuple(result.stdout.split()[1:])

    async def diff_stat(self, repo_dir: Path, commit_range: str) -> str:
        """Return `git diff --stat` for `commit_range`, verbatim.

        This output is display-formatted for humans: long paths are elided to `...` and
        renames are shown as `old => new`. Never parse paths out of it; use `diff_name_only`.
        """
        result = await self._run(repo_dir, "diff", "--stat", commit_range)
        return result.stdout

    async def merge_base(self, repo_dir: Path, ref: str, other: str) -> str:
        """Return the sha of the best common ancestor of `ref` and `other`."""
        result = await self._run(repo_dir, "merge-base", ref, other)
        return result.stdout.strip()

    async def working_tree_line_changes(
        self, repo_dir: Path, *, against: str = _HEAD_REF
    ) -> LineChangeStats:
        """Count the working tree's changed paths against `against`, and its line changes.

        A path is counted once however git reports it: committed and uncommitted edits to a
        tracked file, or a tracked deletion and an untracked file at the same path, are one
        path. Untracked paths carry no line counts.
        """
        tracked = await self._run(repo_dir, "diff", "--numstat", "-z", against)
        tracked_paths, added_lines, deleted_lines, binary_entries = _parse_numstat(tracked.stdout)
        untracked = await self._run(repo_dir, "ls-files", "--others", "--exclude-standard", "-z")
        untracked_paths = {
            path for path in untracked.stdout.split(_NUMSTAT_RECORD_SEPARATOR) if path
        }
        return LineChangeStats(
            changed_paths=len(tracked_paths | untracked_paths),
            added_lines=added_lines,
            deleted_lines=deleted_lines,
            binary_entries=binary_entries,
            untracked_paths=len(untracked_paths),
        )

    async def diff_name_only(self, repo_dir: Path, commit_range: str) -> list[str]:
        """Return the full, unformatted path of every file changed in `commit_range`."""
        result = await self._run(repo_dir, "diff", "--name-only", commit_range)
        return [line for line in result.stdout.splitlines() if line]

    async def diff_patch(self, repo_dir: Path, commit_range: str, paths: Sequence[str]) -> str:
        """Return the binary-safe patch of `paths` changed in `commit_range`, empty if none."""
        result = await self._run(repo_dir, "diff", "--binary", commit_range, "--", *paths)
        return result.stdout

    async def apply_patch(self, repo_dir: Path, patch: str) -> None:
        """Apply `patch` to the index and working tree of `repo_dir`, merging three-way."""
        await self._run(repo_dir, "apply", "--3way", "--index", input_text=patch, mutating=True)

    async def commit(self, repo_dir: Path, message: str) -> None:
        """Commit the index of `repo_dir` as the engine's own identity."""
        await self._run(
            repo_dir,
            "-c",
            f"user.name={_ENGINE_NAME}",
            "-c",
            f"user.email={_ENGINE_EMAIL}",
            "commit",
            "-m",
            message,
            mutating=True,
        )

    async def status_porcelain(self, repo_dir: Path, *, include_ignored: bool = False) -> list[str]:
        """Return one `git status --porcelain` line per entry, optionally including ignored ones."""
        args = ["status", _PORCELAIN_FLAG]
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
        await self._run(repo_dir, "rebase", base, mutating=True)

    async def rebase_abort(self, repo_dir: Path) -> None:
        """Abort the rebase in progress, restoring the branch to where it was before it began."""
        await self._run(repo_dir, "rebase", "--abort", mutating=True)

    async def rebase_continue(self, repo_dir: Path) -> None:
        """Continue a rebase after its conflicts have been resolved and staged."""
        await self._run(repo_dir, "-c", "core.editor=true", "rebase", "--continue", mutating=True)

    async def merge_ff_only(self, repo_dir: Path, branch: str) -> None:
        """Fast-forward the checked-out base branch to `branch` only."""
        await self._run(repo_dir, "merge", "--ff-only", branch, mutating=True)

    async def fast_forward_checked_out(self, repo_dir: Path, base: str, target: str) -> None:
        """Fast-forward the checked-out `base` to `target`, ignoring inherited merge options.

        Autostash is off, and the branch's configured `mergeOptions` are blanked, so no
        configured flag or strategy can turn the update into a squash or a real merge.
        """
        await self._run(
            repo_dir,
            "-c",
            f"branch.{base}.mergeOptions=",
            "merge",
            "--ff-only",
            "--no-autostash",
            target,
            mutating=True,
        )

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
            mutating=True,
        )

    async def commit_tree(self, repo_dir: Path, tree_rev: str, parent: str, message: str) -> str:
        """Create, as the engine's own identity, a commit of `tree_rev`'s tree on `parent`.

        No ref or working tree moves; the new commit's sha is returned.
        """
        result = await self._run(
            repo_dir,
            "-c",
            f"user.name={_ENGINE_NAME}",
            "-c",
            f"user.email={_ENGINE_EMAIL}",
            "commit-tree",
            f"{tree_rev}^{{tree}}",
            "-p",
            parent,
            "-m",
            message,
            mutating=True,
        )
        return result.stdout.strip()

    async def find_commit(self, repo_dir: Path, commit_range: str, text: str) -> str | None:
        """Return the newest commit in `commit_range` whose message contains `text`, if any."""
        result = await self._run(
            repo_dir, "log", "--format=%H", "--fixed-strings", f"--grep={text}", "-n1", commit_range
        )
        return result.stdout.strip() or None

    async def committed_line_changes(
        self, repo_dir: Path, before: str, after: str
    ) -> LineChangeStats:
        """Count the paths and lines changed between the commits `before` and `after`."""
        result = await self._run(repo_dir, "diff", "--numstat", "-z", before, after)
        paths, added_lines, deleted_lines, binary_entries = _parse_numstat(result.stdout)
        return LineChangeStats(
            changed_paths=len(paths),
            added_lines=added_lines,
            deleted_lines=deleted_lines,
            binary_entries=binary_entries,
            untracked_paths=0,
        )

    async def remote_configured(self, repo_dir: Path, remote: str) -> bool:
        """Return whether the repo at `repo_dir` has a remote named `remote` configured."""
        result = await self._run(repo_dir, "remote")
        return remote in result.stdout.splitlines()

    async def remote_branch_sha(self, repo_dir: Path, remote: str, branch: str) -> str | None:
        """Return the sha `branch` had when last pushed to or fetched from `remote`, if ever."""
        result = await self._run(
            repo_dir,
            "rev-parse",
            "--verify",
            "--quiet",
            f"refs/remotes/{remote}/{branch}",
            check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else None

    async def fetch_remote_branch(
        self, repo_dir: Path, remote: str, branch: str, *, timeout: float
    ) -> str | None:
        """Fetch `remote`'s branch to its tracking ref; return the fetched SHA if present."""
        remote_ref = f"{_HEADS_REF_PREFIX}{branch}"
        result = await self._run(
            repo_dir,
            "ls-remote",
            _REMOTE_HEADS_OPTION,
            remote,
            remote_ref,
            timeout=timeout,
        )
        matching_shas = [
            fields[0]
            for line in result.stdout.splitlines()
            if len(fields := line.split()) == _REMOTE_REF_FIELDS and fields[1] == remote_ref
        ]
        if not matching_shas:
            return None
        if len(matching_shas) > 1:
            message = f"git ls-remote returned multiple values for {remote_ref}"
            raise InvariantError(message)
        tracking_ref = f"{_REMOTE_TRACKING_REF_PREFIX}{remote}/{branch}"
        await self.fetch(
            repo_dir,
            remote,
            f"{_FORCE_REFSPEC_PREFIX}{remote_ref}{_REFSPEC_SEPARATOR}{tracking_ref}",
            timeout=timeout,
        )
        return await self.remote_branch_sha(repo_dir, remote, branch)

    async def local_branch_sha(self, repo_dir: Path, branch: str) -> str | None:
        """Return the sha `branch` currently has locally, or `None` when that ref is missing."""
        result = await self._run(
            repo_dir,
            "rev-parse",
            "--verify",
            "--quiet",
            f"{_HEADS_REF_PREFIX}{branch}",
            check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else None

    async def fetch(self, repo_dir: Path, remote: str, *refspecs: str, timeout: float) -> None:
        """Fetch `refspecs` from `remote` within `timeout` seconds, changing only the refs named."""
        await self._run(
            repo_dir,
            "fetch",
            "--no-tags",
            remote,
            *refspecs,
            timeout=timeout,
            mutating=True,
        )

    async def push(self, repo_dir: Path, remote: str, branch: str, *, lease: str | None) -> None:
        """Push `branch` to `remote` as its upstream, overwriting it only if it is still `lease`.

        Without a `lease` the push never overwrites the remote branch.
        """
        lease_args = () if lease is None else (f"--force-with-lease={branch}:{lease}",)
        await self._run(
            repo_dir,
            "push",
            *lease_args,
            "-u",
            remote,
            branch,
            timeout=_PUSH_TIMEOUT_SECONDS,
            mutating=True,
        )

    async def push_base(self, repo_dir: Path, remote: str, branch: str) -> None:
        """Push local `branch` to the same-named branch of `remote`, never forcing or adding refs.

        A remote branch that has diverged rejects the push instead of being overwritten.
        """
        ref = f"refs/heads/{branch}"
        await self._run(
            repo_dir,
            "push",
            "--no-follow-tags",
            remote,
            f"{ref}:{ref}",
            timeout=_PUSH_TIMEOUT_SECONDS,
            mutating=True,
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
            status = entry[:_STATUS_CODE_LENGTH]
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

    async def unmerged_paths(self, repo_dir: Path) -> list[str]:
        """Return the paths with unresolved merge conflicts in `repo_dir`."""
        result = await self._run(repo_dir, "diff", "--name-only", "--diff-filter=U")
        return [line for line in result.stdout.splitlines() if line]

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
        return set(await self._worktree_lock_reasons(repo_dir))

    async def _worktree_lock_reasons(self, repo_dir: Path) -> dict[Path, str | None]:
        result = await self._run(repo_dir, "worktree", "list", "--porcelain")
        reasons: dict[Path, str | None] = {}
        current: Path | None = None
        for line in result.stdout.splitlines():
            if line.startswith(_WORKTREE_LIST_PREFIX):
                current = Path(line[len(_WORKTREE_LIST_PREFIX) :]).resolve()
                reasons[current] = None
            elif line.startswith(_WORKTREE_LOCKED_PREFIX) and current is not None:
                reasons[current] = line[len(_WORKTREE_LOCKED_PREFIX) :].strip()
        return reasons

    async def branch_checkouts(self, repo_dir: Path, branch: str) -> list[Path]:
        """Return the worktrees whose `HEAD` is on `branch`, main checkout first."""
        result = await self._run(repo_dir, "worktree", "list", "--porcelain")
        checked_out_ref = f"{_WORKTREE_BRANCH_PREFIX}{_HEADS_REF_PREFIX}{branch}"
        checkouts: list[Path] = []
        current: Path | None = None
        for line in result.stdout.splitlines():
            if line.startswith(_WORKTREE_LIST_PREFIX):
                current = Path(line[len(_WORKTREE_LIST_PREFIX) :]).resolve()
            elif line == checked_out_ref and current is not None:
                checkouts.append(current)
        return checkouts

    async def _run(
        self,
        repo_dir: Path,
        *args: str,
        check: bool = True,
        timeout: float | None = None,
        input_text: str | None = None,
        mutating: bool = False,
    ) -> ProcessResult:
        return await self._runner.run(
            [_GIT_BIN, *args],
            timeout=self._command_timeout if timeout is None else timeout,
            cwd=repo_dir,
            check=check,
            input_text=input_text,
            stop_grace=GIT_STOP_GRACE if mutating else None,
        )


def _parse_numstat(output: str) -> tuple[set[str], int, int, int]:
    changed_paths: set[str] = set()
    added_lines = 0
    deleted_lines = 0
    binary_entries = 0
    records = output.split(_NUMSTAT_RECORD_SEPARATOR)
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            continue
        added, separator, remainder = record.partition(_NUMSTAT_SEPARATOR)
        deleted, second_separator, path = remainder.partition(_NUMSTAT_SEPARATOR)
        if not separator or not second_separator:
            raise InvariantError(_NUMSTAT_MALFORMED_RECORD)
        if not path:
            new_path_index = index + 1
            if new_path_index >= len(records):
                raise InvariantError(_NUMSTAT_MALFORMED_RECORD)
            path = records[new_path_index]
            index += _NUMSTAT_RENAME_PATH_RECORDS
        changed_paths.add(path)
        if _NUMSTAT_BINARY_MARKER in (added, deleted):
            binary_entries += 1
        else:
            added_lines += int(added)
            deleted_lines += int(deleted)
    return changed_paths, added_lines, deleted_lines, binary_entries
