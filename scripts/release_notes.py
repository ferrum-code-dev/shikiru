"""Print the release notes of one tag from the commits since the previous release tag."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

REPOSITORY_ENVIRONMENT = "GITHUB_REPOSITORY"
GITHUB_URL = "https://github.com"
DISTRIBUTION = "shikiru-cli"
INSTALL_COMMAND = "uv tool install {distribution}=={version}"
FEATURE_TYPE = "feat"
FIX_TYPE = "fix"
BREAKING_LINE = re.compile(r"^BREAKING CHANGE:", re.MULTILINE)
CONVENTIONAL_SUBJECT = re.compile(
    r"(?P<type>[a-z]+)(?:\([^()\n]*\))?(?P<breaking>!)?: (?P<description>.+)"
)
TAG_PATTERN = re.compile(r"v(\d+)\.(\d+)\.(\d+)")
FIELD_SEPARATOR = "\x1f"
RECORD_SEPARATOR = "\x1e"
COMMIT_LOG_FORMAT = f"--format=%H{FIELD_SEPARATOR}%s{FIELD_SEPARATOR}%b{RECORD_SEPARATOR}"
SHORT_SHA_LENGTH = 7
NEWLINES = "\n"
UTF8 = "utf-8"
EXIT_SUCCESS = 0
EXIT_FAILURE = 1

Version = tuple[int, int, int]


class NotesError(Exception):
    """The release notes cannot be built; the message says why."""


class Section(StrEnum):
    BREAKING = "### Breaking changes"
    FEATURES = "### Features"
    FIXES = "### Fixes"
    OTHER = "### Other changes"


SECTION_BY_TYPE = {FEATURE_TYPE: Section.FEATURES, FIX_TYPE: Section.FIXES}


@dataclass(frozen=True)
class Commit:
    sha: str
    subject: str
    body: str


def parse_version(tag: str) -> Version | None:
    match = TAG_PATTERN.fullmatch(tag)
    if match is None:
        return None
    return (int(match[1]), int(match[2]), int(match[3]))


def previous_tag(current: Version, tags: Sequence[str]) -> str | None:
    earlier = [
        (version, name)
        for name in tags
        if (version := parse_version(name)) is not None and version < current
    ]
    if not earlier:
        return None
    return max(earlier)[1]


def section_of(commit: Commit) -> Section:
    parsed = CONVENTIONAL_SUBJECT.fullmatch(commit.subject)
    if BREAKING_LINE.search(commit.body) or (parsed is not None and parsed["breaking"]):
        return Section.BREAKING
    if parsed is None:
        return Section.OTHER
    return SECTION_BY_TYPE.get(parsed["type"], Section.OTHER)


def entry_line(commit: Commit, repository: str) -> str:
    short_sha = commit.sha[:SHORT_SHA_LENGTH]
    return f"* [{short_sha}]({GITHUB_URL}/{repository}/commit/{commit.sha}): {commit.subject}"


def install_command(tag: str) -> str:
    return INSTALL_COMMAND.format(distribution=DISTRIBUTION, version=tag.removeprefix("v"))


def render_notes(tag: str, previous: str | None, commits: Sequence[Commit], repository: str) -> str:
    blocks: list[str] = []
    for section in Section:
        entries = [
            entry_line(commit, repository) for commit in commits if section_of(commit) is section
        ]
        if entries:
            blocks.append(f"{section.value}\n\n" + "\n".join(entries))
    if previous is not None:
        compare = f"{GITHUB_URL}/{repository}/compare/{previous}...{tag}"
        blocks.append(f"**Full Changelog**: [{previous}...{tag}]({compare})")
    blocks.append(f"### Install\n\n```sh\n{install_command(tag)}\n```")
    return "\n\n".join(blocks) + "\n"


def git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        check=False,
        capture_output=True,
        text=True,
        encoding=UTF8,
    )
    if completed.returncode != 0:
        message = f"git {args[0]} failed: {completed.stderr.strip()}"
        raise NotesError(message)
    return completed.stdout


def read_commits(previous: str | None, commit: str) -> list[Commit]:
    revision = commit if previous is None else f"{previous}..{commit}"
    output = git("log", "--no-merges", COMMIT_LOG_FORMAT, revision)
    commits: list[Commit] = []
    for record in output.split(RECORD_SEPARATOR):
        line = record.strip(NEWLINES)
        if line:
            sha, subject, body = line.split(FIELD_SEPARATOR, 2)
            commits.append(Commit(sha, subject, body.strip()))
    return commits


def build_notes(tag: str, commit: str, repository: str | None) -> str:
    version = parse_version(tag)
    if version is None:
        message = f"{tag} is not a release tag v<major>.<minor>.<patch>"
        raise NotesError(message)
    if not repository:
        message = f"no repository: pass --repo or set {REPOSITORY_ENVIRONMENT}"
        raise NotesError(message)
    previous = previous_tag(version, git("tag", "--list").split())
    commits = read_commits(previous, commit)
    return render_notes(tag, previous, commits, repository)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print the release notes of one tag.")
    parser.add_argument("tag")
    parser.add_argument("--commit", help="last commit of the notes; default: the tag")
    parser.add_argument("--repo", default=os.environ.get(REPOSITORY_ENVIRONMENT))
    arguments = parser.parse_args(argv)
    try:
        notes = build_notes(
            arguments.tag,
            arguments.commit or arguments.tag,
            arguments.repo,
        )
    except NotesError as error:
        sys.stderr.write(f"{error}\n")
        return EXIT_FAILURE
    sys.stdout.write(notes)
    return EXIT_SUCCESS


if __name__ == "__main__":
    raise SystemExit(main())
