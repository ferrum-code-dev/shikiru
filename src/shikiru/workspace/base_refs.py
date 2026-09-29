"""Where task roots start from: the local or the verified fetched remote base (03 §6)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from shikiru.errors import ShikiruError

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
    """`integration = "pr"`: work lands on the remote base, which the engine fetches (03 §6)."""

    def __init__(self, git: Git, project_root: Path, remote: str) -> None:
        """Fetch from `remote` into Shikiru's own refs of the repository at `project_root`."""
        self._git = git
        self._project_root = project_root
        self._remote = remote

    async def resolve(self, base: str) -> str:
        """Return the fetched remote base once a merge verified it, else the local `base`."""
        ref = self._ref(base)
        return ref if await self._git.ref_exists(self._project_root, ref) else base

    async def sync(self, base: str, merge_commit: str | None) -> None:
        """Fetch the remote `base` and verify it contains the pull request's `merge_commit`.

        Never touches the local branch, the index or the working tree of the project checkout.
        Raises `MergedBaseUnavailable` when the merge commit is missing or not on the base.
        """
        ref = self._ref(base)
        await self._git.fetch(self._project_root, self._remote, f"+{_HEADS_PREFIX}{base}:{ref}")
        if merge_commit is None or not await self._git.merge_base_is_ancestor(
            self._project_root, merge_commit, ref
        ):
            raise MergedBaseUnavailable(base, merge_commit)

    @staticmethod
    def _ref(base: str) -> str:
        return f"{BASE_REF_PREFIX}{base}"
