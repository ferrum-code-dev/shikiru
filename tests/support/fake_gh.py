"""A fake `gh` for pull request tests; its pull requests are JSON files in a state directory."""

from __future__ import annotations

import json
import sys
from pathlib import Path

URL_PREFIX = "https://github.invalid/shikiru/fake/pull/"
PULLS_DIR = "pulls"
CALLS_LOG = "calls.jsonl"
_OPEN = "OPEN"


def _create(state: Path, options: list[str]) -> str:
    values = dict(zip(options[::2], options[1::2], strict=True))
    pulls = state / PULLS_DIR
    number = len(list(pulls.glob("*.json"))) + 1
    record = {
        "state": _OPEN,
        "base": values["--base"],
        "head": values["--head"],
        "title": values["--title"],
        "body": values["--body"],
    }
    (pulls / f"{number}.json").write_text(json.dumps(record))
    return f"{URL_PREFIX}{number}"


def _view(state: Path, url: str, options: list[str]) -> str:
    fields = options[options.index("--json") + 1].split(",")
    record = json.loads((state / PULLS_DIR / f"{url.removeprefix(URL_PREFIX)}.json").read_text())
    return json.dumps({field: record[field] for field in fields})


def main(argv: list[str]) -> int:
    """Serve `gh pr create` and `gh pr view URL --json FIELDS`; reject everything else."""
    state = Path(argv[0])
    args = argv[1:]
    with (state / CALLS_LOG).open("a") as log:
        log.write(json.dumps(args) + "\n")
    match args:
        case ["pr", "create", *options]:
            output = _create(state, options)
        case ["pr", "view", url, *options]:
            output = _view(state, url, options)
        case _:
            sys.stderr.write(f"fake gh does not support {args}\n")
            return 1
    sys.stdout.write(output + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
