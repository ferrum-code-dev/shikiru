"""The project literal rules, checked mechanically."""

from __future__ import annotations

import ast
import io
import re
import sys
import tokenize
from dataclasses import dataclass
from pathlib import Path

from tests.support.comment_rule import EXCLUSION_MARKER, IGNORED_DIRECTORIES

CHECKED_ROOTS = ("src", "tests/support")
TIMEOUT_ROOTS = ("tests",)
PLAIN_NUMBERS = frozenset({0, 1, -1})
TIMEOUT_KEYWORD = "timeout"
TIMEOUT_MARK = "timeout"
WAIT_TIMEOUT_POSITIONS = {"wait_until": 1, "wait_until_advancing": 2}
ARGV_CALLS = frozenset({"run", "spawn", "spawn_detached", "replace"})
NAME_KEYWORDS = frozenset({"kind", "event", "type"})
ARGV_KEYWORD = "argv"
SCRIPT_NAME = "__name__"
SCRIPT_ENTRY = "__main__"
ENUM_BASE_SUFFIX = "Enum"

_KEEP = re.compile(rf"#\s*{EXCLUSION_MARKER}\s*\S")


@dataclass(frozen=True)
class LiteralViolation:
    path: Path
    line: int
    literal: str


@dataclass(frozen=True)
class LiteralReport:
    violations: list[LiteralViolation]
    kept: int


def _keep_lines(source: str) -> frozenset[int]:
    tokens = tokenize.generate_tokens(io.StringIO(source).readline)
    return frozenset(
        token.start[0]
        for token in tokens
        if token.type == tokenize.COMMENT and _KEEP.search(token.string)
    )


def _is_enum(node: ast.ClassDef) -> bool:
    return any(
        (base.id if isinstance(base, ast.Name) else getattr(base, "attr", "")).endswith(
            ENUM_BASE_SUFFIX
        )
        for base in node.bases
    )


def _is_script_guard(node: ast.Compare) -> bool:
    return (
        isinstance(node.left, ast.Name)
        and node.left.id == SCRIPT_NAME
        and len(node.comparators) == 1
        and isinstance(node.comparators[0], ast.Constant)
        and node.comparators[0].value == SCRIPT_ENTRY
    )


def _call_name(node: ast.Call) -> str:
    return node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")


def _number(node: ast.expr) -> int | float | None:
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        inner = _number(node.operand)
        return None if inner is None else -inner
    if (
        isinstance(node, ast.Constant)
        and isinstance(node.value, int | float)
        and not isinstance(node.value, bool)
    ):
        return node.value
    return None


class _Scanner(ast.NodeVisitor):
    def __init__(self, keep_lines: frozenset[int]) -> None:
        self._keep_lines = keep_lines
        self.found: list[tuple[int, str]] = []
        self.kept = 0

    def _flag(self, node: ast.expr, literal: object) -> None:
        end = node.end_lineno or node.lineno
        if self._keep_lines.intersection(range(node.lineno, end + 1)):
            self.kept += 1
        else:
            self.found.append((node.lineno, repr(literal)))

    def _flag_strings(self, nodes: list[ast.expr]) -> None:
        for node in nodes:
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                self._flag(node, node.value)
            elif isinstance(node, ast.List | ast.Tuple | ast.Set):
                self._flag_strings(node.elts)

    def visit_Module(self, node: ast.Module) -> None:
        for statement in node.body:
            if not isinstance(statement, ast.Assign | ast.AnnAssign):
                self.visit(statement)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for statement in node.body:
            if not (_is_enum(node) and isinstance(statement, ast.Assign | ast.AnnAssign)):
                self.visit(statement)

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        defaults = [*node.args.defaults, *(d for d in node.args.kw_defaults if d is not None)]
        for child in (*node.decorator_list, *defaults, *node.body):
            self.visit(child)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.visit_FunctionDef(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            self.visit(node.value)

    def visit_UnaryOp(self, node: ast.UnaryOp) -> None:
        number = _number(node)
        if number is None:
            self.generic_visit(node)
        elif number not in PLAIN_NUMBERS:
            self._flag(node, number)

    def visit_Constant(self, node: ast.Constant) -> None:
        number = _number(node)
        if number is not None and number not in PLAIN_NUMBERS:
            self._flag(node, number)

    def visit_Compare(self, node: ast.Compare) -> None:
        operands = [node.left, *node.comparators]
        if not _is_script_guard(node):
            self._flag_strings(operands)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if (
            _call_name(node) in ARGV_CALLS
            and node.args
            and isinstance(node.args[0], ast.List | ast.Tuple)
        ):
            self._flag_strings([node.args[0]])
        self._flag_strings(
            [kw.value for kw in node.keywords if kw.arg in NAME_KEYWORDS | {ARGV_KEYWORD}]
        )
        self.generic_visit(node)


def find_violations(source: str, path: Path) -> LiteralReport:
    """Return every literal outside the rule's allowed forms, and how many a `keep:` excluded."""
    scanner = _Scanner(_keep_lines(source))
    scanner.visit(ast.parse(source))
    return LiteralReport(
        [LiteralViolation(path, line, literal) for line, literal in sorted(set(scanner.found))],
        scanner.kept,
    )


def _timeout_arguments(node: ast.Call) -> list[ast.expr]:
    arguments = [keyword.value for keyword in node.keywords if keyword.arg == TIMEOUT_KEYWORD]
    if _call_name(node) == TIMEOUT_MARK and node.args:
        arguments.append(node.args[0])
    position = WAIT_TIMEOUT_POSITIONS.get(_call_name(node))
    if position is not None and len(node.args) > position:
        arguments.append(node.args[position])
    return [argument for argument in arguments if _is_nonzero_number(argument)]


def _is_nonzero_number(node: ast.expr) -> bool:
    number = _number(node)
    return number is not None and number != 0


class _TimeoutScanner(ast.NodeVisitor):
    def __init__(self, keep_lines: frozenset[int]) -> None:
        self._keep_lines = keep_lines
        self.found: list[tuple[int, str]] = []

    def visit_Call(self, node: ast.Call) -> None:
        for argument in _timeout_arguments(node):
            end = argument.end_lineno or argument.lineno
            if not self._keep_lines.intersection(range(argument.lineno, end + 1)):
                self.found.append((argument.lineno, ast.unparse(argument)))
        self.generic_visit(node)


def find_timeout_violations(source: str, path: Path) -> list[LiteralViolation]:
    """Return every inline numeric timeout or wait limit that no `keep:` excludes."""
    scanner = _TimeoutScanner(_keep_lines(source))
    scanner.visit(ast.parse(source))
    return [LiteralViolation(path, line, literal) for line, literal in sorted(set(scanner.found))]


def _python_files(roots: tuple[str, ...]) -> list[Path]:
    return sorted(
        path
        for root in roots
        for path in Path(root).rglob("*.py")
        if not IGNORED_DIRECTORIES.intersection(path.parts)
    )


def main() -> int:
    reports = [
        (path, find_violations(path.read_text(encoding="utf-8"), path))
        for path in _python_files(CHECKED_ROOTS)
    ]
    violations = [violation for _, report in reports for violation in report.violations]
    violations += [
        violation
        for path in _python_files(TIMEOUT_ROOTS)
        for violation in find_timeout_violations(path.read_text(encoding="utf-8"), path)
    ]
    for violation in violations:
        sys.stderr.write(f"{violation.path}:{violation.line}: {violation.literal}\n")
    sys.stdout.write(
        f"literals kept by `# {EXCLUSION_MARKER}`: {sum(r.kept for _, r in reports)}\n"
    )
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
