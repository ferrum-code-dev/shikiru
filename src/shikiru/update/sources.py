"""The port `shikiru update` reads releases through."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Sequence

    from shikiru.domain.releases import Release


class ReleaseSource(Protocol):
    """Lists the repository's releases and downloads the files attached to them."""

    async def releases(self) -> Sequence[Release]:
        """Return every release of the repository, drafts included."""
        ...

    async def download(self, url: str) -> bytes:
        """Return the content at `url`; refuse anything that is not an https address."""
        ...
