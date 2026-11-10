"""Print the version a release of one commit should get, from the commits since the last release."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from enum import IntEnum, StrEnum, auto
from typing import TYPE_CHECKING

import release_notes

if TYPE_CHECKING:
    from collections.abc import Sequence

Version = release_notes.Version

TAG_PREFIX = "v"
VERSION_SEPARATOR = "."
BASE_VERSION: Version = (0, 0, 0)
STABLE_MAJOR = 1
FEATURE_TYPE = release_notes.FEATURE_TYPE
FIX_TYPE = release_notes.FIX_TYPE
PERF_TYPE = "perf"
EXIT_SUCCESS = 0
EXIT_FAILURE = 1
EXIT_NOTHING_TO_RELEASE = 2
NOTHING_TO_RELEASE = "nothing to release"


class NothingToRelease(Exception):
    """No commit since the last release changes the version."""


class ProposalError(Exception):
    """The requested version cannot be released; the message says why."""


class Bump(StrEnum):
    MAJOR = "major"
    MINOR = "minor"
    PATCH = "patch"


class Change(IntEnum):
    FIX = auto()
    FEATURE = auto()
    BREAKING = auto()


@dataclass(frozen=True)
class Rule:
    before_stable: Bump
    from_stable: Bump

    def bump_for(self, version: Version) -> Bump:
        return self.before_stable if version[0] < STABLE_MAJOR else self.from_stable


RULES = {
    Change.BREAKING: Rule(before_stable=Bump.MINOR, from_stable=Bump.MAJOR),
    Change.FEATURE: Rule(before_stable=Bump.PATCH, from_stable=Bump.MINOR),
    Change.FIX: Rule(before_stable=Bump.PATCH, from_stable=Bump.PATCH),
}
CHANGE_BY_TYPE = {FEATURE_TYPE: Change.FEATURE, FIX_TYPE: Change.FIX, PERF_TYPE: Change.FIX}


def change_of(commit: release_notes.Commit) -> Change | None:
    if release_notes.section_of(commit) is release_notes.Section.BREAKING:
        return Change.BREAKING
    parsed = release_notes.CONVENTIONAL_SUBJECT.fullmatch(commit.subject)
    if parsed is None:
        return None
    return CHANGE_BY_TYPE.get(parsed["type"])


def bumped(version: Version, bump: Bump) -> Version:
    major, minor, patch = version
    if bump is Bump.MAJOR:
        return (major + 1, 0, 0)
    if bump is Bump.MINOR:
        return (major, minor + 1, 0)
    return (major, minor, patch + 1)


def propose(last: Version, commits: Sequence[release_notes.Commit]) -> Version:
    changes = [change for commit in commits if (change := change_of(commit)) is not None]
    if not changes:
        raise NothingToRelease(NOTHING_TO_RELEASE)
    return bumped(last, RULES[max(changes)].bump_for(last))


def format_version(version: Version) -> str:
    return ".".join(str(part) for part in version)


def last_release(commit: str) -> tuple[Version, str | None]:
    released = [
        (version, name)
        for name in release_notes.git("tag", "--merged", commit).split()
        if (version := release_notes.parse_version(name)) is not None
    ]
    if not released:
        return BASE_VERSION, None
    version, name = max(released)
    return version, name


def requested_version(text: str, last: Version, tags: Sequence[str]) -> Version:
    version = release_notes.parse_version(TAG_PREFIX + text)
    if version is None:
        message = f"{text} is not <major>.<minor>.<patch>"
        raise ProposalError(message)
    if any(len(part) > 1 and part.startswith("0") for part in text.split(VERSION_SEPARATOR)):
        message = f"{text} has a leading zero in a component; SemVer does not allow one"
        raise ProposalError(message)
    if version <= last:
        message = f"{text} is not higher than the last release {format_version(last)}"
        raise ProposalError(message)
    if TAG_PREFIX + text in tags:
        message = f"tag {TAG_PREFIX}{text} already exists"
        raise ProposalError(message)
    return version


def decide(commit: str, requested: str | None) -> Version:
    last, last_tag = last_release(commit)
    if requested is not None:
        return requested_version(requested, last, release_notes.git("tag", "--list").split())
    return propose(last, release_notes.read_commits(last_tag, commit))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print the next release version.")
    parser.add_argument("--commit", required=True, help="the commit to release")
    parser.add_argument("--version", help="release this version instead of the proposal")
    arguments = parser.parse_args(argv)
    try:
        version = decide(arguments.commit, arguments.version)
    except NothingToRelease as error:
        sys.stderr.write(f"{error}\n")
        return EXIT_NOTHING_TO_RELEASE
    except (ProposalError, release_notes.NotesError) as error:
        sys.stderr.write(f"{error}\n")
        return EXIT_FAILURE
    sys.stdout.write(f"{format_version(version)}\n")
    return EXIT_SUCCESS


if __name__ == "__main__":
    raise SystemExit(main())
