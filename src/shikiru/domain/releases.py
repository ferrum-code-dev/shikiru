"""Release rules for `shikiru update`: the newest release by number, and a wheel's checksum."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

TAG_PREFIX = "v"
NUMBER_PATTERN = re.compile(r"(\d+)\.(\d+)\.(\d+)")
CHECKSUM_LINE = re.compile(r"([0-9a-f]{64}) [ *](.+)")
UTF8 = "utf-8"

ReleaseNumber = tuple[int, int, int]


class ReleaseRuleError(ValueError):
    """A release listing or checksum file breaks a rule; the message names the cause."""


@dataclass(frozen=True)
class ReleaseAsset:
    """A file attached to a release, and the address it downloads from."""

    name: str
    url: str


@dataclass(frozen=True)
class Release:
    """A GitHub release as `shikiru update` reads it."""

    tag: str
    draft: bool
    prerelease: bool
    assets: tuple[ReleaseAsset, ...]

    def asset(self, name: str) -> ReleaseAsset | None:
        """Return the attached file called `name`, or None when the release has no such file."""
        return next((asset for asset in self.assets if asset.name == name), None)


def parse_number(text: str) -> ReleaseNumber | None:
    """Return the numbers of `major.minor.patch`, or None when `text` has another form."""
    matched = NUMBER_PATTERN.fullmatch(text)
    if matched is None:
        return None
    major, minor, patch = matched.groups()
    return int(major), int(minor), int(patch)


def newest_release(releases: Sequence[Release]) -> tuple[ReleaseNumber, Release] | None:
    """Return the highest-numbered `v<major>.<minor>.<patch>` release that is not a draft.

    Pre-releases count. Tags of any other form are skipped, and None means none is usable.
    """
    newest: tuple[ReleaseNumber, Release] | None = None
    for release in releases:
        if release.draft or not release.tag.startswith(TAG_PREFIX):
            continue
        number = parse_number(release.tag.removeprefix(TAG_PREFIX))
        if number is None:
            continue
        if newest is None or number > newest[0]:
            newest = (number, release)
    return newest


def checksum_for(listing: bytes, file_name: str) -> str:
    """Return the SHA-256 that a `sha256sum` listing gives `file_name`.

    Every line must be `<sha256>  <name>` (or ` *` for binary mode), and the file must be listed
    exactly once.
    """
    try:
        text = listing.decode(UTF8)
    except UnicodeDecodeError as error:
        message = "SHA256SUMS is not UTF-8 text"
        raise ReleaseRuleError(message) from error
    digests: list[str] = []
    for line in text.splitlines():
        matched = CHECKSUM_LINE.fullmatch(line)
        if matched is None:
            message = f"SHA256SUMS has a malformed line: {line!r}"
            raise ReleaseRuleError(message)
        digest, name = matched.groups()
        if name == file_name:
            digests.append(digest)
    if not digests:
        message = f"SHA256SUMS does not list {file_name}"
        raise ReleaseRuleError(message)
    if len(digests) > 1:
        message = f"SHA256SUMS lists {file_name} more than once"
        raise ReleaseRuleError(message)
    return digests[0]


def digest_matches(content: bytes, expected: str) -> bool:
    """Return whether the SHA-256 of `content` is `expected`."""
    return hashlib.sha256(content).hexdigest() == expected
