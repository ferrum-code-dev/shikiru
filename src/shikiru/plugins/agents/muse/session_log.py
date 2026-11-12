"""Muse Code session log: locating it, and reading compactions and per-call model usage."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from shikiru.agents.contracts import Compacted
from shikiru.plugins.agents.muse.options import (
    BUDGET_AFTER_KEY,
    BUDGET_BEFORE_KEY,
    CACHED_TOKENS_KEY,
    COMPACTION_INSTALLED_EVENT,
    DEFAULT_DATA_HOME_PARTS,
    ESTIMATED_PROMPT_TOKENS_KEY,
    EVENT_KIND_KEY,
    FRAME_CHILD_RECORD_KEY,
    FRAME_CHILDREN_KEY,
    FRAME_MARKER_KEY,
    INPUT_TOKENS_KEY,
    MODEL_COMPLETED_EVENT,
    MODEL_KEY,
    MUSE_HOME_DIRECTORY,
    OUTPUT_TOKENS_KEY,
    PAYLOAD_EVENT_KEY,
    RECORD_PAYLOAD_KEY,
    RECORD_PAYLOAD_TYPE_KEY,
    SESSION_DAY_GLOB,
    SESSION_LOG_FILENAME,
    SESSION_PAYLOAD_TYPE,
    SESSIONS_DIRECTORY,
    USAGE_KEY,
    XDG_DATA_HOME_ENV_VAR,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Sequence

_MALFORMED_RECORD_MESSAGE = "Muse session log holds a malformed record"


class SessionLogError(Exception):
    """A session log record does not have the shape the reader expects."""


@dataclass(frozen=True)
class ModelCompletion:
    """Exact token usage of one model call, as the session log recorded it."""

    model: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    estimated_prompt_tokens: int | None


def muse_home(environ: Mapping[str, str], user_home: Path) -> Path:
    """Return Muse's data home, which holds the `sessions` directory."""
    data_home = environ.get(XDG_DATA_HOME_ENV_VAR)
    base = Path(data_home) if data_home else user_home.joinpath(*DEFAULT_DATA_HOME_PARTS)
    return base / MUSE_HOME_DIRECTORY


def locate_session_log(home: Path, session_id: str) -> Path | None:
    """Return the session's log, found under whichever date directory it was created in."""
    for day in (home / SESSIONS_DIRECTORY).glob(SESSION_DAY_GLOB):
        log = day / session_id / SESSION_LOG_FILENAME
        if log.is_file():
            return log
    return None


def read_session_records(path: Path) -> list[Mapping[str, Any]]:
    """Read a session log's records, unwrapping permission-transaction frames."""
    return session_records(path.read_text(encoding="utf-8").splitlines())


def session_records(lines: Iterable[str]) -> list[Mapping[str, Any]]:
    """Decode session log lines; a frame contributes its child records in place of itself."""
    records: list[Mapping[str, Any]] = []
    for line in lines:
        records.extend(_unwrap(_decode(line)))
    return records


def compaction_count(records: Sequence[Mapping[str, Any]]) -> int:
    """Count the compactions the session has installed so far."""
    return sum(1 for _ in _events(records, COMPACTION_INSTALLED_EVENT))


def compactions_after(records: Sequence[Mapping[str, Any]], previous_count: int) -> list[Compacted]:
    """Return the compactions installed after the first `previous_count` of them."""
    installed = list(_events(records, COMPACTION_INSTALLED_EVENT))
    return [_compaction(event) for event in installed[previous_count:]]


def model_completions(records: Sequence[Mapping[str, Any]]) -> list[ModelCompletion]:
    """Return the usage of every model call the session log recorded, in order."""
    return [_completion(event) for event in _events(records, MODEL_COMPLETED_EVENT)]


def _events(records: Iterable[Mapping[str, Any]], kind: str) -> Iterator[Mapping[str, Any]]:
    for record in records:
        if record.get(RECORD_PAYLOAD_TYPE_KEY) != SESSION_PAYLOAD_TYPE:
            continue
        payload = _optional_mapping(record.get(RECORD_PAYLOAD_KEY))
        event = None if payload is None else _optional_mapping(payload.get(PAYLOAD_EVENT_KEY))
        if event is not None and event.get(EVENT_KIND_KEY) == kind:
            yield event


def _compaction(event: Mapping[str, Any]) -> Compacted:
    return Compacted(
        pre_tokens=_reported_count(
            _optional_mapping(event.get(BUDGET_BEFORE_KEY)), ESTIMATED_PROMPT_TOKENS_KEY
        ),
        post_tokens=_reported_count(
            _optional_mapping(event.get(BUDGET_AFTER_KEY)), ESTIMATED_PROMPT_TOKENS_KEY
        ),
    )


def _completion(event: Mapping[str, Any]) -> ModelCompletion:
    usage = _mapping(event.get(USAGE_KEY))
    return ModelCompletion(
        model=_text(event, MODEL_KEY),
        input_tokens=_count(usage, INPUT_TOKENS_KEY),
        output_tokens=_count(usage, OUTPUT_TOKENS_KEY),
        cached_tokens=_count(usage, CACHED_TOKENS_KEY),
        estimated_prompt_tokens=_reported_count(event, ESTIMATED_PROMPT_TOKENS_KEY),
    )


def _unwrap(record: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if FRAME_MARKER_KEY not in record:
        return [record]
    children = record.get(FRAME_CHILDREN_KEY)
    if not isinstance(children, list):
        raise SessionLogError(_MALFORMED_RECORD_MESSAGE)
    return [
        _decode(_text(_mapping(child), FRAME_CHILD_RECORD_KEY))
        for child in cast("list[object]", children)
    ]


def _decode(line: str) -> Mapping[str, Any]:
    try:
        value: object = json.loads(line)
    except json.JSONDecodeError as error:
        raise SessionLogError(_MALFORMED_RECORD_MESSAGE) from error
    return _mapping(value)


def _optional_mapping(value: object) -> Mapping[str, Any] | None:
    return cast("Mapping[str, Any]", value) if isinstance(value, Mapping) else None


def _mapping(value: object) -> Mapping[str, Any]:
    mapping = _optional_mapping(value)
    if mapping is None:
        raise SessionLogError(_MALFORMED_RECORD_MESSAGE)
    return mapping


def _text(record: Mapping[str, Any], key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str):
        raise SessionLogError(_MALFORMED_RECORD_MESSAGE)
    return value


def _count(record: Mapping[str, Any], key: str) -> int:
    value = record.get(key)
    if type(value) is not int:
        raise SessionLogError(_MALFORMED_RECORD_MESSAGE)
    return value


def _reported_count(record: Mapping[str, Any] | None, key: str) -> int | None:
    if record is None:
        return None
    value = record.get(key)
    return value if type(value) is int else None
