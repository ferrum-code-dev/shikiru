"""`shikiru report`: validates and atomically stores an agent turn's report (03 §4.3)."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import typer
from pydantic import ValidationError

from shikiru.domain.reports import ReportKind, model_for
from shikiru.errors import EXIT_NOT_INITIALISED, EXIT_USAGE_ERROR, ShikiruError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import BaseModel

RUN_DIR_ENV_VAR = "SHIKIRU_RUN_DIR"
ROLE_ENV_VAR = "SHIKIRU_ROLE"
CURRENT_TURN_LINK_NAME = "current_turn"
REPORT_KIND_FILENAME = "report.kind"
REPORT_FILENAME = "report.json"


class ReportError(ShikiruError):
    """A `shikiru report` failure, carrying the exit code it maps to (03 §4.3).

    `exit_code` is set per instance rather than per subclass, since 03 §4.3 gives `shikiru
    report` its own two exit codes (`EXIT_USAGE_ERROR` invalid, `EXIT_NOT_INITIALISED` no
    current turn) that both flow through this one error type instead of one subclass each.
    """

    def __init__(self, exit_code: int, message: str) -> None:
        """Record the process `exit_code` and the human-readable `message` for stderr."""
        super().__init__(message)
        self.exit_code = exit_code
        self.message = message


def resolve_current_turn_dir(environ: Mapping[str, str]) -> Path:
    """Resolve `$SHIKIRU_RUN_DIR/$SHIKIRU_ROLE/current_turn` to its target directory."""
    run_dir = environ.get(RUN_DIR_ENV_VAR)
    role = environ.get(ROLE_ENV_VAR)
    if not run_dir or not role:
        message = f"{RUN_DIR_ENV_VAR} and {ROLE_ENV_VAR} must both be set"
        raise ReportError(EXIT_NOT_INITIALISED, message)
    link = Path(run_dir) / role / CURRENT_TURN_LINK_NAME
    if not link.is_symlink() or not link.exists():
        message = f"no current turn: {link} is missing or does not point at a live turn dir"
        raise ReportError(EXIT_NOT_INITIALISED, message)
    return link.resolve()


def _report_kind(turn_dir: Path) -> ReportKind:
    kind_path = turn_dir / REPORT_KIND_FILENAME
    if not kind_path.is_file():
        message = f"{kind_path} is missing"
        raise ReportError(EXIT_USAGE_ERROR, message)
    raw_kind = kind_path.read_text().strip()
    try:
        return ReportKind(raw_kind)
    except ValueError:
        message = f"{kind_path}: unknown report kind {raw_kind!r}"
        raise ReportError(EXIT_USAGE_ERROR, message) from None


def _load_report_data(file_path: Path) -> object:
    try:
        raw_text = file_path.read_text()
    except OSError as error:
        message = f"cannot read {file_path}: {error}"
        raise ReportError(EXIT_USAGE_ERROR, message) from error
    try:
        return json.loads(raw_text)
    except json.JSONDecodeError as error:
        message = f"{file_path}: invalid JSON: {error}"
        raise ReportError(EXIT_USAGE_ERROR, message) from error


def _validation_error_messages(error: ValidationError) -> list[str]:
    messages: list[str] = []
    for violation in error.errors():
        field_path = ".".join(str(part) for part in violation["loc"]) or "<root>"
        messages.append(f"{field_path}: {violation['msg']}")
    return messages


def validate_report(turn_dir: Path, file_path: Path) -> BaseModel:
    """Validate `file_path`'s JSON against the model named in `turn_dir`'s `report.kind`."""
    kind = _report_kind(turn_dir)
    data = _load_report_data(file_path)
    model = model_for(kind)
    try:
        return model.model_validate(data)
    except ValidationError as error:
        message = "\n".join(_validation_error_messages(error))
        raise ReportError(EXIT_USAGE_ERROR, message) from error


def write_report(turn_dir: Path, report: BaseModel) -> Path:
    """Write `report` to `turn_dir/report.json`, replacing it atomically."""
    report_path = turn_dir / REPORT_FILENAME
    descriptor, temp_name = tempfile.mkstemp(dir=turn_dir, prefix=f".{REPORT_FILENAME}.")
    try:
        with os.fdopen(descriptor, "w") as temp_file:
            temp_file.write(report.model_dump_json(indent=2))
        Path(temp_name).replace(report_path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise
    return report_path


def submit_report(file_path: Path, environ: Mapping[str, str]) -> Path:
    """Resolve the current turn, validate `file_path` against its report kind, and store it."""
    turn_dir = resolve_current_turn_dir(environ)
    report = validate_report(turn_dir, file_path)
    return write_report(turn_dir, report)


_FILE_OPTION = typer.Option(..., "--file", exists=True, dir_okay=False, help="Report JSON.")


def report_command(file: Path = _FILE_OPTION) -> None:
    """Validate and store the current turn's report (03 §4.3)."""
    try:
        report_path = submit_report(file, os.environ)
    except ReportError as error:
        typer.echo(error.message, err=True)
        raise typer.Exit(code=error.exit_code) from None
    typer.echo(f"report accepted: {report_path}")
