"""Unit tests for the no-literals rule checker (docs/design/07-engineering.md §2.5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support.literal_rule import find_violations

_ALLOWED: list[str] = [
    "LIMIT = 100\nTIMEOUTS = {'a': 30, 'b': 2.5}\n",
    "from enum import IntEnum\nclass Level(IntEnum):\n    HIGH = 3\n",
    "def f(x):\n    return x * 1 + 0 - 1 + -1\n",
    "def f(x: Literal[2, 3]) -> Literal[5]:\n    return x\n",
    "def f():\n    '''Wait 30 seconds, see 'done'.'''\n",
    "def f(x):\n    return f'took {x} of 10 seconds'\n",
    "def f(row):\n    return {'id': row['id'], 'n': row.get('n')}\n",
    "def f(x):\n    return x[:LIMIT]\n",
    "if __name__ == '__main__':\n    pass\n",
    "def f(run, tail):\n    run([BINARY, *tail])\n",
    "def f(text):\n    return text.replace('-', ' ')\n",
    "def f(x):\n    return x * 100  # keep: percent scale\n",
    "def f(status):\n    return status == 'ok'  # keep: wire value\n",
]
_VIOLATIONS: list[tuple[str, list[int]]] = [
    ("def f(x):\n    return x * 100\n", [2]),
    ("def f(x):\n    return x * 2.5\n", [2]),
    ("def f(x):\n    return x[:2]\n", [2]),
    ("def f(x):\n    return x * -2\n", [2]),
    ("def f(x=30):\n    return x\n", [1]),
    ("class C:\n    LIMIT = 5\n", [2]),
    ("def f(x):\n    y: int = 7\n    return y\n", [2]),
    ("def f(status):\n    return status == 'completed'\n", [2]),
    ("def f(status):\n    return status not in ('a', 'b')\n", [2, 2]),
    ("def f(run):\n    run(['git', 'init'])\n", [2, 2]),
    ("def f(runner):\n    runner.spawn(('tmux', '-V'))\n", [2, 2]),
    ("def f(make):\n    make(argv=['git'])\n", [2]),
    ("def f(emit):\n    emit(kind='turn_started')\n", [2]),
    ("def f(x):\n    return x * 100  # keep:\n", [2]),
    ("def f(x):\n    return x * 100  # note\n", [2]),
    ("def f(x):\n    y = x * 100\n    return y  # keep: another line\n", [2]),
]
_KEPT: list[tuple[str, int]] = [
    ("def f(x):\n    return x * 100  # keep: percent scale\n", 1),
    ("def f(x):\n    return x * 100 + x * 3  # keep: percent and triple\n", 2),
    ("LIMIT = 5\n", 0),
]


@pytest.mark.parametrize("source", _ALLOWED)
def test_named_constants_trivial_numbers_and_unchecked_positions_are_allowed(source: str) -> None:
    assert find_violations(source, Path("x.py")).violations == []


@pytest.mark.parametrize(("source", "lines"), _VIOLATIONS)
def test_bare_numbers_and_protocol_strings_are_violations(source: str, lines: list[int]) -> None:
    report = find_violations(source, Path("x.py"))

    assert [violation.line for violation in report.violations] == lines


@pytest.mark.parametrize(("source", "kept"), _KEPT)
def test_a_keep_marker_with_a_reason_excludes_its_line_and_is_counted(
    source: str, kept: int
) -> None:
    report = find_violations(source, Path("x.py"))

    assert (report.violations, report.kept) == ([], kept)
