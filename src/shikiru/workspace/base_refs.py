"""Where task roots start from: the local or the verified fetched remote base."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Protocol

from shikiru.errors import CommandFailed, CommandTimeoutError, ShikiruError

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.workspace.git import Git

BASE_REF_PREFIX = "refs/shikiru/bases/"
_HEADS_PREFIX = "refs/heads/"
_REMOTE_TRACKING_PREFIX = "refs/remotes/"
_MISSING_REF_SHA = "0" * 40
_DIVERGED_REASON = "it has local commits the remote base does not contain"
_NOT_MOVED_REASON = "git left the branch where it was"


class MergedBaseUnavailable(ShikiruError):
    """A merged pull request's commit is not on the fetched remote base."""

    def __init__(self, base: str, commit: str | None) -> None:
        """Name the base and the merge commit that could not be verified on it."""
        super().__init__(f"merge commit {commit} is not on the fetched remote base {base}")


class BaseFetchFailed(ShikiruError):
    """The remote base could not be fetched: the remote is unreachable or the fetch timed out."""

    def __init__(self, base: str, remote: str, cause: ShikiruError) -> None:
        """Name the `base` and `remote` and say why the fetch failed."""
        super().__init__(f"cannot fetch {base} from {remote}: {cause}")


class LocalBaseBlocked(ShikiruError):
    """The local base branch cannot fast-forward to the remote tip without losing work."""

    def __init__(self, base: str, tip: str, reason: str) -> None:
        """Name the `base`, the remote `tip` it could not reach and why."""
        super().__init__(f"local base {base} cannot fast-forward to {tip}: {reason}")


class BaseRefs(Protocol):
    """The single rule choosing the git ref a task root starts from."""

    async def resolve(self, base: str) -> str:
        """Return the ref new task roots of `base` start from and rebase onto."""
        ...


class LocalBaseRefs:
    """`integration = "merge"`: work lands on the local base branch, so it is the ref."""

    async def resolve(self, base: str) -> str:
        """Return `base` itself."""
        return base


class FetchedBaseRefs:
    """`integration = "pr"`: work lands on the remote base, which the engine fetches.

    Every root starts from the freshly fetched remote base, so local commits that were never
    pushed are not in it.
    """

    def __init__(self, git: Git, project_root: Path, remote: str, *, fetch_timeout: float) -> None:
        """Fetch from `remote` into Shikiru's own refs of the repository at `project_root`.

        A fetch that takes longer than `fetch_timeout` seconds fails.
        """
        self._git = git
        self._project_root = project_root
        self._remote = remote
        self._fetch_timeout = fetch_timeout

    async def resolve(self, base: str) -> str:
        """Fetch the remote `base` and return the ref it was fetched into.

        Raises `BaseFetchFailed` when the fetch fails: a root is never built from a stale ref.
        """
        return await self._fetch(base)

    async def sync(self, base: str, merge_commit: str | None) -> str:
        """Fetch the remote `base` and return its tip, once it contains `merge_commit`.

        The fetch updates Shikiru's ref and the remote-tracking ref of `base`; the local branch
        is advanced separately by `LocalBaseSync`. Raises `BaseFetchFailed` when the fetch fails
        and `MergedBaseUnavailable` when the merge commit is missing or not on the base.
        """
        ref = await self._fetch(base)
        tip = await self._git.rev_parse(self._project_root, ref)
        if merge_commit is None or not await self._git.merge_base_is_ancestor(
            self._project_root, merge_commit, tip
        ):
            raise MergedBaseUnavailable(base, merge_commit)
        return tip

    async def _fetch(self, base: str) -> str:
        ref = f"{BASE_REF_PREFIX}{base}"
        tracking_ref = f"{_REMOTE_TRACKING_PREFIX}{self._remote}/{base}"
        try:
            await self._git.fetch(
                self._project_root,
                self._remote,
                f"+{_HEADS_PREFIX}{base}:{ref}",
                f"+{_HEADS_PREFIX}{base}:{tracking_ref}",
                timeout=self._fetch_timeout,
            )
        except (CommandFailed, CommandTimeoutError) as error:
            raise BaseFetchFailed(base, self._remote, error) from error
        return ref


class LocalBaseSync:
    """Moves the local base branch to the verified remote tip, and only fast-forwards it.

    A checked-out base advances in its own worktree, so git refuses any merge that would
    overwrite uncommitted work. A base that is not checked out moves by compare-and-swap, so a
    concurrent change makes the update fail. A local base holding commits the remote tip lacks
    is never moved.
    """

    def __init__(self, git: Git, project_root: Path) -> None:
        """Advance the branches of the repository at `project_root` through `git`."""
        self._git = git
        self._project_root = project_root
        self._lock = asyncio.Lock()

    async def advance(self, base: str, tip: str) -> None:
        """Point the local `base` at `tip`, or raise `LocalBaseBlocked` when that is not safe.

        The move is verified: a base that does not reach `tip` raises rather than reporting
        success. Idempotent: a base already at `tip` is left alone, so retries and restarts
        are safe.
        """
        async with self._lock:
            current = await self._git.local_branch_sha(self._project_root, base)
            if current == tip:
                return
            if current is not None and not await self._git.merge_base_is_ancestor(
                self._project_root, current, tip
            ):
                raise LocalBaseBlocked(base, tip, _DIVERGED_REASON)
            try:
                await self._move(base, tip, current)
            except (CommandFailed, CommandTimeoutError) as error:
                raise LocalBaseBlocked(base, tip, str(error)) from error
            if await self._git.local_branch_sha(self._project_root, base) != tip:
                raise LocalBaseBlocked(base, tip, _NOT_MOVED_REASON)

    async def _move(self, base: str, tip: str, current: str | None) -> None:
        checkouts = await self._git.branch_checkouts(self._project_root, base)
        if checkouts:
            await self._git.fast_forward_checked_out(checkouts[0], base, tip)
            return
        await self._git.update_ref(self._project_root, base, tip, current or _MISSING_REF_SHA)
