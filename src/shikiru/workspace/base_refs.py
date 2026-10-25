"""Where task roots start from: the local or the verified fetched remote base."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from shikiru.errors import CommandFailed, CommandTimeoutError, ShikiruError

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.workspace.git import Git

BASE_REF_PREFIX = "refs/shikiru/bases/"
_HEADS_PREFIX = "refs/heads/"


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

    async def sync(self, base: str, merge_commit: str | None) -> None:
        """Fetch the remote `base` and verify it contains the pull request's `merge_commit`.

        Never touches the local branch, the index or the working tree of the project checkout.
        Raises `BaseFetchFailed` when the fetch fails and `MergedBaseUnavailable` when the merge
        commit is missing or not on the base.
        """
        ref = await self._fetch(base)
        if merge_commit is None or not await self._git.merge_base_is_ancestor(
            self._project_root, merge_commit, ref
        ):
            raise MergedBaseUnavailable(base, merge_commit)

    async def _fetch(self, base: str) -> str:
        ref = f"{BASE_REF_PREFIX}{base}"
        try:
            await self._git.fetch(
                self._project_root,
                self._remote,
                f"+{_HEADS_PREFIX}{base}:{ref}",
                timeout=self._fetch_timeout,
            )
        except (CommandFailed, CommandTimeoutError) as error:
            raise BaseFetchFailed(base, self._remote, error) from error
        return ref
