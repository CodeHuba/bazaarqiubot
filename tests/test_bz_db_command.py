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


def test_official_text_key_translations_apply_to_db_metadata(monkeypatch):
    load_plugin_module(monkeypatch)
    trans = importlib.import_module("plugins.bazaar_plugin.translations")
    assert trans.get_zh_by_text_key("Vanessa") == "瓦内莎"
    assert trans.get_zh_by_text_key("Stelle") == "斯黛尔"
    assert trans.get_zh_by_text_key("Poison") == "剧毒"
    assert trans.get_zh_by_text_key("Apparel") == "服饰"
    assert trans.get_zh_by_text_key("Property") == "地产"
    assert trans.get_zh_by_text_key("Merchant") == "商人"
    assert trans.get_zh_by_text_key("Trap") == "陷阱"


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


def test_db_returns_single_local_candidate_as_full_card(monkeypatch, tmp_path):
    module = load_plugin_module(monkeypatch)
    plugin = module.BazaarPlugin()
    db_path = tmp_path / "GameData.db"
    db_path.write_text("unused")
    candidate = {
        "Id": "pipe-organ-id",
        "Type": "Item",
        "InternalName": "Pipe Organ",
        "Localization": {"Title": {"Text": "Pipe Organ"}},
    }
    monkeypatch.setattr(module.card_data_paths, "get_gamedata_db_path", lambda *args, **kwargs: db_path)
    gdc_module = importlib.import_module("plugins.bazaar_plugin.gamedata_client")
    monkeypatch.setattr(gdc_module, "query_raw_by_name", lambda *args: None)
    monkeypatch.setattr(gdc_module, "suggest_cards", lambda *args: [candidate, candidate.copy()])
    monkeypatch.setattr(
        gdc_module,
        "format_card_from_raw",
        lambda raw, **kwargs: f"📦 完整资料：{raw['InternalName']} / 附魔={kwargs['show_enchants']}",
    )
    cih_module = importlib.import_module("plugins.bazaar_plugin.card_image_helper")
    monkeypatch.setattr(cih_module, "get_art_url", lambda **kwargs: "https://cdn.test/pipe-organ.webp")
    monkeypatch.setattr(module.bdb, "query_card_by_name", lambda name: pytest.fail("不应调用远程 fallback"))

    result = asyncio.run(plugin._cmd_db("pipe orgn -e"))

    assert result == (
        "[CQ:image,file=https://cdn.test/pipe-organ.webp]\n"
        "📦 完整资料：Pipe Organ / 附魔=True"
    )


def test_db_multiple_local_candidates_still_returns_suggestions(monkeypatch, tmp_path):
    module = load_plugin_module(monkeypatch)
    plugin = module.BazaarPlugin()
    db_path = tmp_path / "GameData.db"
    db_path.write_text("unused")
    monkeypatch.setattr(module.card_data_paths, "get_gamedata_db_path", lambda *args, **kwargs: db_path)
    gdc_module = importlib.import_module("plugins.bazaar_plugin.gamedata_client")
    monkeypatch.setattr(gdc_module, "query_raw_by_name", lambda *args: None)
    monkeypatch.setattr(gdc_module, "suggest_cards", lambda *args: [
        {"Id": "one", "Type": "Item", "InternalName": "Pipe Organ", "Localization": {"Title": {"Text": "Pipe Organ"}}},
        {"Id": "two", "$type": "TCardSkill", "InternalName": "Pipe Dream", "Localization": {"Title": {"Text": "Pipe Dream"}}},
    ])
    monkeypatch.setattr(module.bdb, "query_card_by_name", lambda name: pytest.fail("不应调用远程 fallback"))

    result = asyncio.run(plugin._cmd_db("pipe"))

    assert "未精确匹配『pipe』" in result
    assert "Pipe Organ（物品）" in result
    assert "Pipe Dream（技能）" in result


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
    monkeypatch.setattr(
        gdc_module,
        "format_card_from_raw",
        lambda raw, **kwargs: f"📦 完整资料：{raw['InternalName']}",
    )
    monkeypatch.setattr(module.bdb, "query_card_by_name", lambda name: pytest.fail("不应调用远程 fallback"))

    result = asyncio.run(plugin._cmd_db("管风"))

    assert queried == ["管风", "Pipe Organ"]
    assert result == "📦 完整资料：Pipe Organ"


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
