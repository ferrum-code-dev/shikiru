"""Release Shikiru: bump the version, commit it, tag it and push; the Release workflow publishes."""

import argparse
import os
import re
import shlex
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

REPOSITORY = "ferrum-code-dev/shikiru"
GITHUB_URL = "https://github.com"
RELEASE_WORKFLOW = "release.yml"
REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE = "shikiru"
PYPROJECT_NAME = "pyproject.toml"
LOCKFILE_NAME = "uv.lock"
README_NAME = "README.md"
REMOTE = "origin"
RELEASE_BRANCH = "main"
TAG_PREFIX = "v"
BUMP_PATCH = "patch"
BUMP_MINOR = "minor"
BUMP_MAJOR = "major"
RELEASE_SUBJECT = "chore: release {version}"
CO_AUTHOR_TRAILER = "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
SHORT_SHA_LENGTH = 7
VERSION_PATTERN = re.compile(r"(\d+)\.(\d+)\.(\d+)")
EXIT_SUCCESS = 0
EXIT_FAILURE = 1

Version = tuple[int, int, int]


class ReleaseError(Exception):
    """The release cannot go ahead or a step failed; the message says why."""


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class CommandRunner(Protocol):
    def run(self, argv: Sequence[str], *, cwd: Path) -> CommandResult: ...


@dataclass(frozen=True)
class SubprocessRunner:
    environ: Mapping[str, str]

    def run(self, argv: Sequence[str], *, cwd: Path) -> CommandResult:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            env=dict(self.environ),
            capture_output=True,
            text=True,
            check=False,
        )
        return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def parse_version(text: str) -> Version | None:
    match = VERSION_PATTERN.fullmatch(text)
    if match is None:
        return None
    major, minor, patch = (int(part) for part in match.groups())
    return (major, minor, patch)


def format_version(version: Version) -> str:
    return ".".join(str(part) for part in version)


def next_version(current: Version, request: str) -> Version:
    major, minor, patch = current
    if request == BUMP_PATCH:
        return (major, minor, patch + 1)
    if request == BUMP_MINOR:
        return (major, minor + 1, 0)
    if request == BUMP_MAJOR:
        return (major + 1, 0, 0)
    explicit = parse_version(request)
    if explicit is None:
        message = f"{request} is not {BUMP_PATCH}, {BUMP_MINOR}, {BUMP_MAJOR} or <x.y.z>"
        raise ReleaseError(message)
    if explicit <= current:
        message = (
            f"{format_version(explicit)} is not newer than the current version "
            f"{format_version(current)}"
        )
        raise ReleaseError(message)
    return explicit


def replace_once(text: str, pattern: re.Pattern[str], replacement: str, name: str) -> str:
    rewritten, count = pattern.subn(replacement, text)
    if count != 1:
        message = f"{name} must hold the current version once in its place; found {count}"
        raise ReleaseError(message)
    return rewritten


def rewrite_pyproject(text: str, old: Version, new: Version) -> str:
    pattern = re.compile(rf'^version = "{re.escape(format_version(old))}"$', re.MULTILINE)
    return replace_once(text, pattern, f'version = "{format_version(new)}"', PYPROJECT_NAME)


def rewrite_lockfile(text: str, old: Version, new: Version) -> str:
    entry = re.escape(f'[[package]]\nname = "{PACKAGE}"\nversion = "')
    pattern = re.compile(rf'(?<={entry}){re.escape(format_version(old))}(?=")')
    return replace_once(text, pattern, format_version(new), LOCKFILE_NAME)


def rewrite_readme(text: str, old: Version, new: Version) -> str:
    pattern = re.compile(rf"(?<![0-9.]){re.escape(format_version(old))}(?![0-9]|\.[0-9])")
    return pattern.sub(format_version(new), text)


REWRITES = (
    (PYPROJECT_NAME, rewrite_pyproject),
    (LOCKFILE_NAME, rewrite_lockfile),
    (README_NAME, rewrite_readme),
)


def read_current_version(pyproject: Path) -> Version:
    raw = str(tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"])
    current = parse_version(raw)
    if current is None:
        message = f"{PYPROJECT_NAME} has version {raw}, which is not <x.y.z>"
        raise ReleaseError(message)
    return current


def planned_edits(repo: Path, old: Version, new: Version) -> dict[str, str]:
    return {
        name: rewrite((repo / name).read_text(encoding="utf-8"), old, new)
        for name, rewrite in REWRITES
    }


def git(runner: CommandRunner, repo: Path, *args: str) -> str:
    result = runner.run(("git", *args), cwd=repo)
    if result.returncode != 0:
        message = f"git {shlex.join(args)} failed: {result.stderr.strip()}"
        raise ReleaseError(message)
    return result.stdout.strip()


def check_repository(runner: CommandRunner, repo: Path, tag: str) -> None:
    branch = git(runner, repo, "branch", "--show-current")
    if branch != RELEASE_BRANCH:
        message = f"the branch is {branch or 'a detached HEAD'}, not {RELEASE_BRANCH}"
        raise ReleaseError(message)
    if git(runner, repo, "status", "--porcelain"):
        message = "the work tree has uncommitted or untracked changes"
        raise ReleaseError(message)
    git(runner, repo, "fetch", "--quiet", REMOTE)
    head = git(runner, repo, "rev-parse", "HEAD")
    published = git(runner, repo, "rev-parse", f"{REMOTE}/{RELEASE_BRANCH}")
    if head != published:
        message = (
            f"HEAD {head[:SHORT_SHA_LENGTH]} is not {REMOTE}/{RELEASE_BRANCH} at "
            f"{published[:SHORT_SHA_LENGTH]}; push or pull first"
        )
        raise ReleaseError(message)
    if git(runner, repo, "tag", "--list", tag):
        message = f"the tag {tag} already exists"
        raise ReleaseError(message)


def release_commands(version: str) -> tuple[tuple[str, ...], ...]:
    tag = f"{TAG_PREFIX}{version}"
    return (
        ("git", "add", PYPROJECT_NAME, LOCKFILE_NAME, README_NAME),
        ("git", "commit", "-m", RELEASE_SUBJECT.format(version=version), "-m", CO_AUTHOR_TRAILER),
        ("git", "push", REMOTE, RELEASE_BRANCH),
        ("git", "tag", tag),
        ("git", "push", REMOTE, tag),
    )


def workflow_runs_url() -> str:
    return f"{GITHUB_URL}/{REPOSITORY}/actions/workflows/{RELEASE_WORKFLOW}"


def release(
    runner: CommandRunner,
    repo: Path,
    request: str,
    *,
    dry_run: bool,
    report: Callable[[str], None],
) -> None:
    current = read_current_version(repo / PYPROJECT_NAME)
    version = next_version(current, request)
    old_text = format_version(current)
    new_text = format_version(version)
    check_repository(runner, repo, f"{TAG_PREFIX}{new_text}")
    edits = planned_edits(repo, current, version)
    verb = "would rewrite" if dry_run else "rewriting"
    for name in edits:
        report(f"{verb} {name}: version {old_text} -> {new_text}")
    if not dry_run:
        for name, text in edits.items():
            (repo / name).write_text(text, encoding="utf-8")
    for argv in release_commands(new_text):
        command = shlex.join(argv)
        if dry_run:
            report(f"would run: {command}")
            continue
        report(f"running: {command}")
        result = runner.run(argv, cwd=repo)
        if result.returncode != 0:
            message = f"`{command}` failed: {result.stderr.strip()}"
            raise ReleaseError(message)
    report(f"Release workflow runs: {workflow_runs_url()}")


def say(message: str) -> None:
    sys.stdout.write(f"{message}\n")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Release Shikiru: bump the version, commit, tag and push."
    )
    parser.add_argument(
        "bump", help=f"{BUMP_PATCH}, {BUMP_MINOR}, {BUMP_MAJOR} or an explicit <x.y.z>"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="run the checks and print every file change and command, changing nothing",
    )
    arguments = parser.parse_args(argv)
    try:
        release(
            SubprocessRunner(os.environ),
            REPO_ROOT,
            arguments.bump,
            dry_run=arguments.dry_run,
            report=say,
        )
    except ReleaseError as error:
        sys.stderr.write(f"release stopped: {error}\n")
        return EXIT_FAILURE
    return EXIT_SUCCESS


if __name__ == "__main__":
    raise SystemExit(main())
