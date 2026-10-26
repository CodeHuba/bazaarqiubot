import asyncio
from collections import defaultdict

import httpx
import pytest

from test_bz_db_command import FakeEvent, load_plugin_module


@pytest.mark.parametrize(
    "command,argument,api_season",
    [
        ("stat", "Koucha_", 20),
        ("history", "Koucha_", 20),
        ("hist", "Koucha_", 20),
        ("历史", "Koucha_", 20),
        ("history", "Koucha_ --cb", 20),
        ("stat", "Koucha_ 19", 20),
        ("history", "Koucha_ 19", 20),
        ("hist", "Koucha_ 19", 20),
        ("历史", "Koucha_ 19", 20),
        ("history", "Koucha_ 19 --cb", 20),
        ("stat", "Koucha_ 18", 19),
        ("history", "Koucha_ 20 --cb", 21),
        ("stat", "Player Name 19", 20),
        ("stat", "12345", 20),
    ],
)
def test_player_command_reaches_api(command, argument, api_season, monkeypatch):
    module = load_plugin_module(monkeypatch)
    plugin = module.BazaarPlugin()
    plugin._cooldown = defaultdict(float)
    plugin.client = module.BazaarDataClient()
    requests = []
    client_class = httpx.AsyncClient

    def respond(request):
        requests.append(dict(request.url.params))
        return httpx.Response(200, json={"success": True, "data": {}})

    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda **kwargs: client_class(transport=httpx.MockTransport(respond), **kwargs),
    )
    event = FakeEvent(f"#bz {command} {argument}")

    asyncio.run(plugin.handle(event))

    username = "Player Name" if argument.startswith("Player Name") else argument.split()[0]
    assert requests == [{"username": username, "seasonId": str(api_season)}]
    assert len(event.replies) == 1
    assert "本赛季无记录" in event.replies[0]


@pytest.mark.parametrize(
    "command,argument",
    [("stat", "Koucha_ 0"), ("history", "Koucha_ -1"), ("history", "--cb")],
)
def test_invalid_season_or_missing_username_does_not_query(command, argument, monkeypatch):
    module = load_plugin_module(monkeypatch)
    plugin = module.BazaarPlugin()
    from unittest.mock import AsyncMock
    from types import SimpleNamespace

    query = AsyncMock()
    plugin.client = SimpleNamespace(get_player_stat=query)

    result = asyncio.run(plugin._dispatch(command, argument, FakeEvent()))

    query.assert_not_awaited()
    assert "[巴扎]" in result
