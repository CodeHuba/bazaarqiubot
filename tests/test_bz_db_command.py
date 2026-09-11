import asyncio
import importlib
import sys
from collections import defaultdict
from types import ModuleType
from unittest.mock import AsyncMock

import pytest


def load_plugin_module(monkeypatch) -> ModuleType:
    """加载插件模块所需的最小 ncatbot 桩，避免测试依赖真实 QQ 运行时。"""
    plugin_system = ModuleType("ncatbot.plugin_system")
    core_event = ModuleType("ncatbot.core.event")

    class NcatBotPlugin:
        pass

    def on_message(func):
        return func

    class BaseMessageEvent:
        pass

    plugin_system.NcatBotPlugin = NcatBotPlugin
    plugin_system.on_message = on_message
    core_event.BaseMessageEvent = BaseMessageEvent
    ncatbot = ModuleType("ncatbot")
    core = ModuleType("ncatbot.core")
    monkeypatch.setitem(sys.modules, "ncatbot", ncatbot)
    monkeypatch.setitem(sys.modules, "ncatbot.plugin_system", plugin_system)
    monkeypatch.setitem(sys.modules, "ncatbot.core", core)
    monkeypatch.setitem(sys.modules, "ncatbot.core.event", core_event)

    sys.modules.pop("plugins.bazaar_plugin.bazaar_plugin", None)
    sys.modules.pop("plugins.bazaar_plugin", None)
    return importlib.import_module("plugins.bazaar_plugin.bazaar_plugin")


class FakeEvent:
    def __init__(self, raw_message="#bz db 管风琴"):
        self.raw_message = raw_message
        self.user_id = 10001
        self.message_type = "private"
        self.replies = []

    async def reply(self, value):
        self.replies.append(value)


@pytest.mark.parametrize("case", ["text", "error"])
def test_db_command_always_replies(case, monkeypatch):
    module = load_plugin_module(monkeypatch)
    plugin = module.BazaarPlugin()
    plugin._cooldown = defaultdict(float)
    if case == "text":
        plugin._cmd_db = AsyncMock(return_value="📦 管风琴\n描述")
    else:
        plugin._cmd_db = AsyncMock(side_effect=RuntimeError("GameData.db unavailable"))

    event = FakeEvent()
    asyncio.run(plugin.handle(event))

    assert len(event.replies) == 1
    if case == "text":
        assert event.replies == ["📦 管风琴\n描述"]
    else:
        assert "处理失败" in event.replies[0]
        assert "GameData.db unavailable" in event.replies[0]


def test_db_fallback_does_not_pass_enchants_flag_as_card_name(monkeypatch, tmp_path):
    module = load_plugin_module(monkeypatch)
    plugin = module.BazaarPlugin()
    plugin._cooldown = defaultdict(float)
    monkeypatch.delenv("GAMEDATA_DB", raising=False)
    monkeypatch.setattr(
        module.card_data_paths,
        "get_gamedata_db_path",
        lambda *args, **kwargs: None,
    )
    queried = []

    def query_card(name):
        queried.append(name)
        return None

    monkeypatch.setattr(module.bdb, "query_card_by_name", query_card)
    monkeypatch.setattr(module.bdb, "search_cards", lambda *args: [])

    result = asyncio.run(plugin._cmd_db("Pipe Organ --enchants"))

    assert "未找到" in result
    assert queried == ["Pipe Organ"]


def test_image_send_failure_retries_text_only(monkeypatch):
    module = load_plugin_module(monkeypatch)
    plugin = module.BazaarPlugin()
    plugin._cooldown = defaultdict(float)
    plugin._cmd_db = AsyncMock(return_value="[CQ:image,file=https://cdn.invalid/card.webp]\n📦 管风琴\n描述")

    event = FakeEvent()
    attempts = []

    async def reply(value):
        attempts.append(value)
        if len(attempts) == 1:
            raise RuntimeError("image rejected")
        event.replies.append(value)

    event.reply = reply
    asyncio.run(plugin.handle(event))

    assert attempts == [
        "[CQ:image,file=https://cdn.invalid/card.webp]\n📦 管风琴\n描述",
        "📦 管风琴\n描述",
    ]
    assert event.replies == ["📦 管风琴\n描述"]


def test_db_returns_local_suggestions_before_remote_fallback(monkeypatch, tmp_path):
    module = load_plugin_module(monkeypatch)
    plugin = module.BazaarPlugin()
    plugin._cooldown = defaultdict(float)
    db_path = tmp_path / "GameData.db"
    db_path.write_text("unused")
    monkeypatch.setattr(module.card_data_paths, "get_gamedata_db_path", lambda *args, **kwargs: db_path)
    gdc_module = importlib.import_module("plugins.bazaar_plugin.gamedata_client")
    monkeypatch.setattr(gdc_module, "query_raw_by_name", lambda *args: None)
    monkeypatch.setattr(gdc_module, "suggest_cards", lambda *args: [{
        "Type": "Item",
        "InternalName": "Pipe Organ",
        "Localization": {"Title": {"Text": "Pipe Organ"}},
    }])
    remote_called = []
    monkeypatch.setattr(module.bdb, "query_card_by_name", lambda name: remote_called.append(name))

    result = asyncio.run(plugin._cmd_db("pipe orgn"))

    assert "未精确匹配『pipe orgn』" in result
    assert "Pipe Organ" in result
    assert remote_called == []


def test_db_partial_chinese_name_returns_candidates(monkeypatch, tmp_path):
    module = load_plugin_module(monkeypatch)
    plugin = module.BazaarPlugin()
    db_path = tmp_path / "GameData.db"
    db_path.write_text("unused")
    monkeypatch.setattr(module.card_data_paths, "get_gamedata_db_path", lambda *args, **kwargs: db_path)
    gdc_module = importlib.import_module("plugins.bazaar_plugin.gamedata_client")
    queried = []

    def query_raw(name, *_args):
        queried.append(name)
        if name == "Pipe Organ":
            return {
                "Type": "Item",
                "InternalName": "Pipe Organ",
                "Localization": {"Title": {"Text": "Pipe Organ"}},
            }
        return None

    monkeypatch.setattr(gdc_module, "query_raw_by_name", query_raw)
    trans_module = importlib.import_module("plugins.bazaar_plugin.translations")
    monkeypatch.setattr(trans_module, "has_chinese", lambda value: True)
    monkeypatch.setattr(trans_module, "get_en", lambda value: None)
    monkeypatch.setattr(trans_module, "search_zh", lambda value, limit=5: ["Pipe Organ"])
    monkeypatch.setattr(trans_module, "get_zh", lambda value: "管风琴" if value == "Pipe Organ" else None)
    monkeypatch.setattr(module.bdb, "query_card_by_name", lambda name: pytest.fail("不应调用远程 fallback"))

    result = asyncio.run(plugin._cmd_db("管风"))

    assert queried == ["管风", "Pipe Organ"]
    assert "管风琴（物品）" in result


def test_db_enchant_tooltip_uses_official_hash_translation(monkeypatch):
    load_plugin_module(monkeypatch)
    gdc_module = importlib.import_module("plugins.bazaar_plugin.gamedata_client")
    trans_module = importlib.import_module("plugins.bazaar_plugin.translations")

    monkeypatch.setattr(
        trans_module,
        "get_zh_by_hash",
        lambda key: "护盾 {ability.e1}" if key == "official-enchant-key" else None,
    )
    monkeypatch.setattr(trans_module, "get_tooltip_zh", lambda text: None)

    raw = {
        "$type": "TCardItem",
        "InternalName": "Test Item",
        "StartingTier": "Bronze",
        "Tiers": {"Bronze": {}},
        "Enchantments": {
            "Shielded": {
                "Attributes": {"ShieldApplyAmount": 25},
                "Abilities": {
                    "e1": {"Action": {"$type": "TActionPlayerShieldApply"}},
                },
                "Auras": {},
                "Localization": {
                    "Tooltips": [{
                        "Content": {
                            "Key": "official-enchant-key",
                            "Text": "Shield {ability.e1}",
                        },
                    }],
                },
            },
        },
    }

    result = gdc_module.format_card_from_raw(raw, show_enchants=True)

    assert "[护盾] 护盾 25" in result
    assert "Shield 25" not in result
