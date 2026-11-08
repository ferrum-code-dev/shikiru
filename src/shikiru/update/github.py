"""The GitHub release source: the release listing and attached files, read over https."""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any, cast

from shikiru.domain.releases import Release, ReleaseAsset
from shikiru.update.errors import ReleaseStepError

if TYPE_CHECKING:
    from collections.abc import Mapping
    from http.client import HTTPMessage
    from typing import IO

REPOSITORY = "ferrum-code-dev/shikiru"
API_ADDRESS = "https://api.github.com"
RELEASES_PATH = f"/repos/{REPOSITORY}/releases"
HTTPS_PREFIX = "https://"
USER_AGENT = "shikiru-update"
LISTING_HEADERS = {"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT}
FILE_HEADERS = {"User-Agent": USER_AGENT}


class HttpsRedirects(urllib.request.HTTPRedirectHandler):
    """Follows redirects to https addresses only."""

    def redirect_request(  # noqa: PLR0917 -- urllib's override takes its arguments positionally
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        """Return the follow-up request for an https `newurl`, and refuse any other address."""
        if not newurl.startswith(HTTPS_PREFIX):
            message = f"redirect to {newurl} is not https"
            raise urllib.error.URLError(message)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class GitHubReleaseSource:
    """Reads the repository's releases from the GitHub API, and the files attached to them."""

    def __init__(self, *, timeout_seconds: float, max_bytes: int) -> None:
        """Wait at most `timeout_seconds` for each request and refuse responses over `max_bytes`."""
        self._timeout_seconds = timeout_seconds
        self._max_bytes = max_bytes
        self._opener = urllib.request.build_opener(HttpsRedirects())

    async def releases(self) -> list[Release]:
        """Return the repository's releases in the order GitHub lists them."""
        listing = await asyncio.to_thread(
            self._read, f"{API_ADDRESS}{RELEASES_PATH}", LISTING_HEADERS
        )
        return parse_releases(listing)

    async def download(self, url: str) -> bytes:
        """Return the content at `url`, which must be an https address."""
        return await asyncio.to_thread(self._read, url, FILE_HEADERS)

    def _read(self, url: str, headers: Mapping[str, str]) -> bytes:
        if not url.startswith(HTTPS_PREFIX):
            message = f"refusing to read {url}: only https addresses are read"
            raise ReleaseStepError(message)
        request = urllib.request.Request(url, headers=dict(headers))  # noqa: S310 -- https only
        try:
            with self._opener.open(request, timeout=self._timeout_seconds) as response:
                content = response.read(self._max_bytes + 1)
        except OSError as error:
            message = f"reading {url} failed: {error}"
            raise ReleaseStepError(message) from error
        if len(content) > self._max_bytes:
            message = f"{url} is larger than {self._max_bytes} bytes"
            raise ReleaseStepError(message)
        return content


def parse_releases(listing: bytes) -> list[Release]:
    """Return the releases of a GitHub `GET /releases` listing, in the order it gives."""
    try:
        return [_release(cast("Mapping[str, Any]", item)) for item in json.loads(listing)]
    except (ValueError, KeyError, TypeError) as error:
        message = f"GitHub's release listing is not understood: {error}"
        raise ReleaseStepError(message) from error


def _release(item: Mapping[str, Any]) -> Release:
    return Release(
        tag=item["tag_name"],
        draft=item["draft"],
        prerelease=item["prerelease"],
        assets=tuple(
            ReleaseAsset(name=asset["name"], url=asset["browser_download_url"])
            for asset in item["assets"]
        ),
    )
