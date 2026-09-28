"""A fake `gh` for pull request tests; its pull requests are JSON files in a state directory."""

from __future__ import annotations

import json
import sys
from pathlib import Path

URL_PREFIX = "https://github.invalid/shikiru/fake/pull/"
PULLS_DIR = "pulls"
CALLS_LOG = "calls.jsonl"
HOLD_CREATE = "hold-create"
_OPEN = "OPEN"


def _records(state: Path) -> list[tuple[str, dict[str, str]]]:
    pulls = state / PULLS_DIR
    count = len(list(pulls.glob("*.json")))
    return [
        (f"{URL_PREFIX}{number}", json.loads((pulls / f"{number}.json").read_text()))
        for number in range(1, count + 1)
    ]


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
    hold = state / HOLD_CREATE
    if hold.exists():
        with hold.open() as fifo:
            fifo.read()
    return f"{URL_PREFIX}{number}"


def _list(state: Path, options: list[str]) -> str:
    values = dict(zip(options[::2], options[1::2], strict=True))
    matching = [
        {"url": url}
        for url, record in _records(state)
        if record["head"] == values["--head"]
        and record["base"] == values["--base"]
        and record["state"].lower() == values["--state"]
    ]
    return json.dumps(matching)


def _view(state: Path, url: str, options: list[str]) -> str:
    fields = options[options.index("--json") + 1].split(",")
    record = json.loads((state / PULLS_DIR / f"{url.removeprefix(URL_PREFIX)}.json").read_text())
    return json.dumps({field: record[field] for field in fields})


def main(argv: list[str]) -> int:
    """Serve `gh pr create`, `gh pr list` and `gh pr view URL --json FIELDS`; reject the rest.

    While the `HOLD_CREATE` FIFO exists, `pr create` records its pull request and then blocks
    before printing it, as if the engine died between opening and recording it."""
    state = Path(argv[0])
    args = argv[1:]
    with (state / CALLS_LOG).open("a") as log:
        log.write(json.dumps(args) + "\n")
    match args:
        case ["pr", "create", *options]:
            output = _create(state, options)
        case ["pr", "list", *options]:
            output = _list(state, options)
        case ["pr", "view", url, *options]:
            output = _view(state, url, options)
        case _:
            sys.stderr.write(f"fake gh does not support {args}\n")
            return 1
    sys.stdout.write(output + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
