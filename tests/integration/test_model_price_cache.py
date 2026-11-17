import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from shikiru.agents.sdk.model_prices import model_prices

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.asyncio
async def test_daily_refresh_and_saved_list_during_outage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests = 0
    available = True
    original_client = httpx.AsyncClient

    def respond(_request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        if not available:
            return httpx.Response(503)
        return httpx.Response(200, json={"data": [{"id": "openai/test", "pricing": {}}]})

    def client(**kwargs: Any) -> httpx.AsyncClient:
        return original_client(transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    first, second = await asyncio.gather(model_prices(tmp_path, now), model_prices(tmp_path, now))
    assert first == second == {"openai/test": {"id": "openai/test", "pricing": {}}}
    assert await model_prices(tmp_path, now + timedelta(hours=23))
    assert requests == 1
    available = False
    assert await model_prices(tmp_path, now + timedelta(days=1))
    assert requests == 2
    assert await model_prices(tmp_path, now + timedelta(days=1, hours=2))
    assert requests == 2
    assert await model_prices(tmp_path / "empty", now) == {}
    assert await model_prices(tmp_path / "empty", now + timedelta(hours=1)) == {}
    assert requests == 3
