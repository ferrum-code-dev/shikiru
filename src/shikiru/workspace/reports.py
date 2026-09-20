"""Read validated reports from one completed turn directory."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import ValidationError

from shikiru.domain.reports import (
    HELPER_REPORT_FILENAME,
    REPORT_FILENAME,
    REPORT_KIND_FILENAME,
    Report,
    ReportKind,
    model_for,
)

if TYPE_CHECKING:
    from pathlib import Path

_REPORT_FILENAMES = (REPORT_FILENAME, HELPER_REPORT_FILENAME)


@dataclass(frozen=True)
class LoadedReport:
    """A validated report and the kind declared by its turn directory."""

    kind: ReportKind
    report: Report


def load_turn_report(turn_dir: Path) -> LoadedReport | None:
    """Load the first valid `report.json` or Helper `helper.json` from `turn_dir`."""
    kind = _read_kind(turn_dir / REPORT_KIND_FILENAME)
    if kind is None:
        return None
    model = model_for(kind)
    for filename in _REPORT_FILENAMES:
        report = _read_report(turn_dir / filename, model)
        if report is not None:
            return LoadedReport(kind=kind, report=report)
    return None


def _read_kind(path: Path) -> ReportKind | None:
    try:
        return ReportKind(path.read_text().strip())
    except OSError, UnicodeError, ValueError:
        return None


def _read_report(path: Path, model: type[Report]) -> Report | None:
    try:
        data = json.loads(path.read_text())
    except OSError, UnicodeError, json.JSONDecodeError:
        return None
    try:
        return model.model_validate(data)
    except ValidationError:
        return None
