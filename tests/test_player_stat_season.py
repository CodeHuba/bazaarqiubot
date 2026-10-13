import asyncio
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import httpx
import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "plugins" / "bazaar_plugin" / "data_client.py"


def _load_data_client(monkeypatch):
    package = ModuleType("season_test_package")
    package.__path__ = [str(MODULE_PATH.parent)]
    paths = ModuleType("season_test_package.card_data_paths")
    paths.get_gamedata_db_path = lambda *_args, **_kwargs: None
    monkeypatch.setitem(sys.modules, package.__name__, package)
    monkeypatch.setitem(sys.modules, paths.__name__, paths)

    spec = importlib.util.spec_from_file_location(
        "season_test_package.data_client", MODULE_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_player_stat_chain_uses_season_20_without_changing_current_season(monkeypatch):
    module = _load_data_client(monkeypatch)
    captured = {}

    class Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"success": True, "data": {}}

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, params):
            captured["url"] = url
            captured["params"] = params
            return Response()

    monkeypatch.setattr(module.httpx, "AsyncClient", Client)

    asyncio.run(module.BazaarDataClient().get_player_stat("Koucha_"))

    assert module.CURRENT_SEASON_ID == 18
    assert module.RUNS_SEASON_ID == 18
    assert captured["url"] == "https://bazaarapi.mrmao.life/api/user/comprehensive-info"
    assert captured["params"] == {"username": "Koucha_", "seasonId": 20}


@pytest.mark.parametrize("command", ["stat", "history", "hist", "历史", "history --cb"])
def test_player_commands_send_api_season_20(command, monkeypatch):
    from test_bz_db_command import load_plugin_module, FakeEvent

    module = load_plugin_module(monkeypatch)
    plugin = module.BazaarPlugin()
    plugin.client = module.BazaarDataClient()
    captured = []
    client_class = httpx.AsyncClient

    def respond(request):
        captured.append(dict(request.url.params))
        return httpx.Response(200, json={"success": True, "data": {}})

    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: client_class(transport=transport, **kwargs),
    )

    sub, _, flags = command.partition(" ")
    result = asyncio.run(plugin._dispatch(sub, f"Koucha_ {flags}".strip(), FakeEvent()))

    assert captured == [{"username": "Koucha_", "seasonId": "20"}]
    assert "本赛季无记录" in result
