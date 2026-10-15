import copy
import importlib.util
import json
import sqlite3
import sys
import types
from pathlib import Path

import pytest
from flask import Flask

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins/bazaar_plugin"
for name, directory in (("plugins", ROOT / "plugins"), ("plugins.bazaar_plugin", PLUGIN)):
    package = types.ModuleType(name)
    package.__path__ = [str(directory)]
    sys.modules.setdefault(name, package)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


preview = load("plugins.bazaar_plugin.card_preview", PLUGIN / "card_preview.py")
api = load("preview_api_under_test", ROOT / "web_runs/card_preview_api.py")
CARD_ID = "03842226-6605-466f-9ea3-a8a2dbea67e1"


@pytest.fixture
def card():
    return {"Id": CARD_ID, "Type": "Item", "StartingTier": "Silver", "Size": "Medium", "Version": "fixture-v1",
            "Localization": {"Title": {"Key": "title", "Text": "Example"}, "Tooltips": [
                {"TooltipType": "Active", "Content": {"Key": "damage", "Text": "Deal {ability.0} Damage"}},
                {"TooltipType": "Passive", "TooltipCondition": "Heated", "Content": {"Text": "Only while heated"}},
                {"TooltipType": "Passive", "Content": {"Text": "Gold unlock"}}]},
            "Tiers": {"Silver": {"Attributes": {"DamageAmount": 10, "CooldownMax": 2500}, "TooltipIds": [0, 1], "AuraIds": []},
                      "Gold": {"Attributes": {"DamageAmount": 20}, "TooltipIds": [0, 1, 2], "AuraIds": []}},
            "Abilities": {"0": {"Action": {"$type": "TActionPlayerDamage"}}},
            "Enchantments": {"Fiery": {"Auras": {"e": {"Action": {"$type": "TAuraActionCardModifyAttribute",
                "Target": {"$type": "TTargetCardSelf"}, "AttributeType": "DamageAmount", "Operation": "Multiply",
                "Value": {"$type": "TFixedValue", "Value": 2}}}}, "Localization": {"Tooltips": [
                    {"TooltipType": "Passive", "Content": {"Text": "Double damage"}}]}}}}


def render(card, quality="", enchantment="", language="zh-CN"):
    localized = {"title": "示例卡牌", "damage": "造成 {ability.0} 伤害"}
    return preview.build_preview(card, quality, enchantment, language,
                                 lambda c, lang: localized.get(c.get("Key"), c.get("Text", "")) if lang == "zh-CN" else c.get("Text", ""), {}, "test")


def test_only_real_qualities_and_localized_templates_are_used(card):
    result = render(card)
    assert result["quality"] == "Silver"
    assert [q["value"] for q in result["availableQualities"]] == ["Silver", "Gold"]
    assert result["effects"][0]["text"] == "造成 10 伤害"
    assert result["name"] == "示例卡牌"
    assert result["attributes"][0]["value"] == "2.5秒"
    assert len(result["effects"]) == 2
    assert result["effects"][1]["condition"] == "灼热时"
    assert len(render(card, "Gold")["effects"]) == 3
    assert render(card, language="en-US")["name"] == "Example"
    with pytest.raises(ValueError):
        render(card, "Bronze")


def test_enchantment_modifies_selected_quality_without_mutating_template(card):
    original = copy.deepcopy(card)
    assert render(card, "Gold", "Fiery")["effects"][0]["text"] == "造成 40 伤害"
    result = render(card, "Silver", "Fiery")
    assert result["effects"][0]["text"] == "造成 20 伤害"
    assert result["effects"][-1]["source"] == "enchantment"
    assert card == original
    with pytest.raises(ValueError):
        render(card, enchantment="Missing")


def test_runtime_values_and_coalesce_are_unknown_not_zero(card):
    card["Abilities"]["0"]["Action"] = {"$type": "TActionCardModifyAttribute", "AttributeType": "DamageAmount",
        "Value": {"$type": "TReferenceValuePlayerAttribute", "AttributeType": "Gold", "DefaultValue": 0}}
    card["Localization"]["Tooltips"][0]["Content"] = {"Text": "Deal {ability.0 ?? DamageAmount} Damage"}
    result = render(card)
    assert "〔动态数值〕" in result["effects"][0]["text"]
    assert "10" not in result["effects"][0]["text"]
    assert result["hasDynamicValues"] is True


@pytest.mark.parametrize("context", [{"Prerequisites": [{"$type": "TPrerequisitePlayerAttribute"}]}, {"WorksIn": "CombatOnly"}])
def test_contextual_self_auras_do_not_leave_a_false_base_value(card, context):
    aura = card["Enchantments"]["Fiery"]["Auras"]["e"]
    aura.update(context)
    result = render(card, "Gold", "Fiery")
    assert result["effects"][0]["text"] == "造成 〔动态数值〕 伤害"
    assert not any(a["key"] == "DamageAmount" for a in result["attributes"])


def test_reload_is_ammo_not_time_while_cooldown_is_seconds(card):
    card["Tiers"]["Silver"]["Attributes"]["ReloadAmount"] = 1
    card["Abilities"]["0"]["Action"] = {"$type": "TActionCardReload"}
    card["Localization"]["Tooltips"][0]["Content"] = {"Text": "Reload {ability.0} Ammo"}
    result = render(card)
    assert result["effects"][0]["text"] == "Reload 1 Ammo"
    assert result["attributes"][0]["value"] == "2.5秒"


def test_dependent_auras_use_final_dependency_without_repeat_multiplication():
    auras = {"burn": {"Action": {"$type": "TAuraActionCardModifyAttribute", "Target": {"$type": "TTargetCardSelf"},
        "AttributeType": "BurnApplyAmount", "Operation": "Add", "Value": {"$type": "TReferenceValueCardAttribute",
            "AttributeType": "DamageAmount", "Target": {"$type": "TTargetCardSelf"}}}},
        "damage": {"Action": {"$type": "TAuraActionCardModifyAttribute", "Target": {"$type": "TTargetCardSelf"},
            "AttributeType": "DamageAmount", "Operation": "Multiply", "Value": {"$type": "TFixedValue", "Value": 2}}}}
    attrs, unknown = preview._apply_self_auras({"DamageAmount": 10}, auras)
    assert attrs == {"DamageAmount": 20, "BurnApplyAmount": 20}
    assert unknown == []


def test_skill_is_supported_and_markup_is_text(card):
    card["Type"] = "Skill"
    card["Enchantments"] = {}
    card["Localization"]["Title"] = {"Text": "<color=red>Title</color>"}
    result = render(card)
    assert result["name"] == "Title"
    assert result["availableEnchantments"] == []


def test_quest_goals_and_quality_specific_rewards_are_included(card):
    card["Quests"] = [{"Entries": [{"Localization": {"Tooltips": [{"Content": {"Text": "Visit a Gold Encounter"}}]},
        "Reward": {"Tiers": {"Silver": {"Attributes": {"DamageAmount": 30}}, "Gold": {"Attributes": {"DamageAmount": 50}}},
                   "Localization": {"Tooltips": [{"Content": {"Text": "Deal {ability.0} Damage"}}]}}}]}]
    quest = [e for e in render(card, "Gold")["effects"] if e["source"] == "quest"]
    assert len(quest) == 2
    assert quest[0]["text"] == "Visit a Gold Encounter"
    assert quest[0]["condition"] == "任务 1 目标"
    assert quest[1]["text"] == "Deal 50 Damage"
    assert quest[1]["condition"] == "任务 1 奖励"


@pytest.fixture
def repository(tmp_path, card):
    db = tmp_path / "GameData.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE cards (Id TEXT PRIMARY KEY, Data TEXT)")
        conn.execute("INSERT INTO cards VALUES (?, ?)", (CARD_ID, json.dumps(card)))
    translations = tmp_path / "zh-CN.json"
    translations.write_text(json.dumps({"title": "中文名", "damage": "造成 {ability.0} 伤害"}), encoding="utf-8")
    return preview.CardPreviewRepository(db, translations, tmp_path / "missing-images.json")


def test_read_only_lookup_and_translation_change_invalidate_cache(repository):
    first = repository.get(CARD_ID)
    assert first["name"] == "中文名"
    repository.translations.write_text(json.dumps({"title": "更新后的中文名"}), encoding="utf-8")
    second = repository.get(CARD_ID)
    assert second["name"] == "更新后的中文名"
    assert first["version"] != second["version"]
    assert sorted(p.name for p in repository.database.parent.glob("*.db*")) == ["GameData.db"]


def test_database_change_invalidates_cache(repository, card):
    first = repository.get(CARD_ID)
    card["Tiers"]["Silver"]["Attributes"]["DamageAmount"] = 123
    with sqlite3.connect(repository.database) as conn:
        conn.execute("UPDATE cards SET Data=?", (json.dumps(card),))
    assert repository.get(CARD_ID)["effects"][0]["text"] == "造成 123 伤害"
    assert repository.get(CARD_ID)["version"] != first["version"]


def test_http_boundary_validates_variants_and_supports_etag(repository):
    app = Flask(__name__)
    app.register_blueprint(api.create_card_preview_blueprint(repository))
    client = app.test_client()
    url = "/api/card-preview/" + CARD_ID
    response = client.get(url)
    assert response.status_code == 200
    assert client.get(url, headers={"If-None-Match": response.headers["ETag"]}).status_code == 304
    assert client.get(url + "?quality=Bronze").status_code == 400
    assert client.get(url + "?enchantment=Fake").status_code == 400
    assert client.get(url + "?lang=unknown").status_code == 400
    assert client.get("/api/card-preview/not-a-card").status_code == 400
    assert client.get("/api/card-preview/00000000-0000-0000-0000-000000000000").status_code == 404
    repository.database.unlink()
    unavailable = client.get(url)
    assert unavailable.status_code == 503
    assert str(repository.database) not in unavailable.get_data(as_text=True)
