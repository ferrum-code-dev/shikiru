"""Unit tests for the no-literals rule checker."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support.literal_rule import find_timeout_violations, find_violations

_CASES: list[tuple[str, list[int], int]] = [
    ("LIMIT = 100\nTIMEOUTS = {'a': 30, 'b': 2.5}\n", [], 0),
    ("from enum import IntEnum\nclass Level(IntEnum):\n    HIGH = 3\n", [], 0),
    ("def f(x):\n    return x * 1 + 0 - 1 + -1\n", [], 0),
    ("def f(x: Literal[2, 3]) -> Literal[5]:\n    return x\n", [], 0),
    ("def f():\n    '''Wait 30 seconds, see 'done'.'''\n", [], 0),
    ("def f(x):\n    return f'took {x} of 10 seconds'\n", [], 0),
    ("def f(row):\n    return {'id': row['id'], 'n': row.get('n')}\n", [], 0),
    ("def f(run, tail):\n    run([BINARY, *tail])\n", [], 0),
    ("def f(x):\n    return x * 100\n", [2], 0),
    ("def f(x):\n    return x[:2]\n", [2], 0),
    ("def f(x):\n    return x * -2\n", [2], 0),
    ("def f(x=30):\n    return x\n", [1], 0),
    ("class C:\n    LIMIT = 5\n", [2], 0),
    ("def f(x):\n    y: int = 7\n    return y\n", [2], 0),
    ("def f(status):\n    return status == 'completed'\n", [2], 0),
    ("def f(status):\n    return status not in ('a', 'b')\n", [2, 2], 0),
    ("def f(run):\n    run(['git', 'init'])\n", [2, 2], 0),
    ("def f(runner):\n    runner.spawn(('tmux', '-V'))\n", [2, 2], 0),
    ("def f(make):\n    make(argv=['git'])\n", [2], 0),
    ("def f(emit):\n    emit(kind='turn_started')\n", [2], 0),
    ("def f(x):\n    return x * 100  # keep: percent scale\n", [], 1),
    ("def f(x):\n    return x * 100 + x * 3  # keep: percent and triple\n", [], 2),
    ("def f(status):\n    return status == 'ok'  # keep: wire value\n", [], 1),
    ("def f(x):\n    return x * 100  # keep:\n", [2], 0),
    ("def f(x):\n    y = x * 100\n    return y  # keep: another line\n", [2], 0),
]


@pytest.mark.parametrize(("source", "lines", "kept"), _CASES)
def test_bare_numbers_and_protocol_strings_are_violations_unless_named_or_kept_with_a_reason(
    source: str, lines: list[int], kept: int
) -> None:
    report = find_violations(source, Path("x.py"))

    assert ([violation.line for violation in report.violations], report.kept) == (lines, kept)


_TIMEOUT_CASES: list[tuple[str, list[int]]] = [
    ("wait_until(ready, timeout=SHORT_WAIT_SECONDS)\n", []),
    ("run(argv, timeout=scaled(SHORT_WAIT_SECONDS))\n", []),
    ("run(argv, timeout=0)\n", []),
    ("wait_until(ready, timeout=5)\n", [1]),
    ("run(argv, timeout=5.0)\n", [1]),
    ("wait_until(ready, 5)\n", [1]),
    ("wait_until_advancing(clock, ready, 5)\n", [1]),
    ("@pytest.mark.timeout(240)\ndef test_x() -> None:\n    pass\n", [1]),
    ("wait_until(ready, timeout=5)  # keep: the fake clock waits in real time\n", []),
    ("x = scaled(5)\n", []),
]


@pytest.mark.parametrize(("source", "lines"), _TIMEOUT_CASES)
def test_inline_numeric_timeouts_are_violations_unless_named_or_kept_with_a_reason(
    source: str, lines: list[int]
) -> None:
    found = find_timeout_violations(source, Path("x.py"))

    assert [violation.line for violation in found] == lines
