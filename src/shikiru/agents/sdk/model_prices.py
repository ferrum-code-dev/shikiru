"""Daily OpenRouter model-list cache."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast
from weakref import WeakKeyDictionary

import httpx

_URL = "https://openrouter.ai/api/v1/models"
_CACHE_FILENAME = "openrouter-models.json"
_REFRESH_INTERVAL = timedelta(days=1)
_RESPONSE_INDEX = 2
_LOCKS: WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = WeakKeyDictionary()


def _decode(text: str) -> dict[str, Any] | None:
    try:
        data: object = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    entries: object = cast("dict[str, Any]", data).get("data")
    if not isinstance(entries, list) or not entries:
        return None
    result: dict[str, Any] = {}
    for item in cast("list[object]", entries):
        if not isinstance(item, dict):
            return None
        entry = cast("dict[str, Any]", item)
        identifier = entry.get("id")
        if not isinstance(identifier, str) or not identifier or identifier in result:
            return None
        result[identifier] = entry
    return result


def _read(path: Path) -> tuple[dict[str, Any], datetime, str | None] | None:
    try:
        data: object = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        envelope = cast("dict[str, Any]", data)
        saved_at = datetime.fromisoformat(envelope["saved_at"])
        response = envelope["response"]
        models = {} if response is None else _decode(response)
        if models is None or saved_at.tzinfo is None:
            return None
    except OSError, UnicodeError, ValueError, TypeError, KeyError:
        return None
    else:
        return models, saved_at, response


def _save(path: Path, text: str | None, now: datetime) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, filename = tempfile.mkstemp(dir=path.parent, prefix=path.name)
    temporary = Path(filename)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump({"saved_at": now.isoformat(), "response": text}, output)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


async def model_prices(cache_dir: Path, now: datetime) -> dict[str, Any]:
    """Return cached prices or refresh them, keeping the last valid list during outages."""
    loop = asyncio.get_running_loop()
    lock = _LOCKS.setdefault(loop, asyncio.Lock())
    async with lock:
        return await _model_prices(cache_dir, now)


async def _model_prices(cache_dir: Path, now: datetime) -> dict[str, Any]:
    path = cache_dir / _CACHE_FILENAME
    saved = await asyncio.to_thread(_read, path)
    if saved is not None and timedelta(0) <= now - saved[1] < _REFRESH_INTERVAL:
        return saved[0]
    text = saved[_RESPONSE_INDEX] if saved else None
    models = saved[0] if saved else {}
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(_URL)
            response.raise_for_status()
        fetched = _decode(response.text)
        if fetched is not None:
            text = response.text
            models = fetched
    except httpx.HTTPError, UnicodeError:
        pass
    try:
        await asyncio.to_thread(_save, path, text, now)
    except OSError:
        return models
    return models
