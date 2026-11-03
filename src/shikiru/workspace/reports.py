"""Read validated reports from one completed turn directory."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from pydantic import ValidationError

from shikiru.domain.reports import (
    COMMIT_SUBJECT_FIELD,
    DECISION_ACCEPT,
    HELPER_REPORT_FILENAME,
    REPORT_DECISION_FIELD,
    REPORT_FILENAME,
    REPORT_KIND_FILENAME,
    FeedbackVerdict,
    PlanReport,
    Report,
    ReportKind,
    Verdict,
    model_for,
)
from shikiru.errors import ShikiruError

if TYPE_CHECKING:
    from pathlib import Path

_REPORT_FILENAMES = (REPORT_FILENAME, HELPER_REPORT_FILENAME)
ENGINE_VERDICT_FILENAME = "engine-verdict.json"
LEGACY_COMMIT_SUBJECT = "chore: legacy report"


class InvalidTurnReportError(ShikiruError):
    """A turn report file exists but its kind, JSON or schema is invalid."""


@dataclass(frozen=True)
class LoadedReport:
    """A validated report and the kind declared by its turn directory."""

    kind: ReportKind
    report: Report


def load_turn_report(turn_dir: Path) -> LoadedReport | None:
    """Load a valid report, return `None` only when no report files exist."""
    kind_path = turn_dir / REPORT_KIND_FILENAME
    kind = _read_kind(kind_path)
    if kind is None:
        if any(_file_exists(turn_dir / filename) for filename in _REPORT_FILENAMES):
            message = f"report kind is missing: {kind_path}"
            raise InvalidTurnReportError(message)
        return None
    model = model_for(kind)
    invalid: InvalidTurnReportError | None = None
    for filename in _REPORT_FILENAMES:
        try:
            report = _read_report(turn_dir / filename, model)
        except InvalidTurnReportError as error:
            if invalid is None:
                invalid = error
            continue
        if report is not None:
            return LoadedReport(kind=kind, report=report)
    if invalid is not None:
        raise invalid
    return None


def load_engine_verdict(turn_dir: Path) -> Verdict | None:
    """Load the engine-computed verdict stored beside a turn's agent report."""
    path = turn_dir / ENGINE_VERDICT_FILENAME
    try:
        text = path.read_text()
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError) as error:
        message = f"cannot read engine verdict {path}: {error}"
        raise InvalidTurnReportError(message) from error
    model = (
        FeedbackVerdict
        if _read_kind(turn_dir / REPORT_KIND_FILENAME) is ReportKind.FEEDBACK_VERDICT
        else Verdict
    )
    return model.model_validate_json(text)


def _file_exists(path: Path) -> bool:
    try:
        path.stat()
    except FileNotFoundError:
        return False
    except OSError as error:
        message = f"cannot inspect report file {path}: {error}"
        raise InvalidTurnReportError(message) from error
    return True


def _read_kind(path: Path) -> ReportKind | None:
    try:
        value = path.read_text().strip()
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError) as error:
        message = f"cannot read report kind {path}: {error}"
        raise InvalidTurnReportError(message) from error
    try:
        return ReportKind(value)
    except ValueError as error:
        message = f"invalid report kind in {path}: {value!r}"
        raise InvalidTurnReportError(message) from error


def _read_report(path: Path, model: type[Report]) -> Report | None:
    try:
        text = path.read_text()
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError) as error:
        message = f"cannot read report {path}: {error}"
        raise InvalidTurnReportError(message) from error
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        message = f"invalid JSON in report {path}: {error}"
        raise InvalidTurnReportError(message) from error
    try:
        return _validate_stored_report(data, model)
    except ValidationError as error:
        message = f"invalid report {path}: {error}"
        raise InvalidTurnReportError(message) from error


def _validate_stored_report(data: object, model: type[Report]) -> Report:
    if not isinstance(data, dict) or COMMIT_SUBJECT_FIELD in data:
        return model.model_validate(data)
    fields = cast("dict[str, object]", data)
    if model is PlanReport or (
        model in (Verdict, FeedbackVerdict) and fields.get(REPORT_DECISION_FIELD) == DECISION_ACCEPT
    ):
        normalized = model.model_validate({**fields, COMMIT_SUBJECT_FIELD: LEGACY_COMMIT_SUBJECT})
        return normalized.model_copy(update={COMMIT_SUBJECT_FIELD: ""})
    return model.model_validate(data)
