import importlib.util
from pathlib import Path
from types import ModuleType
from typing import cast

MODULE_PATH = Path(__file__).resolve().parents[1] / "plugins" / "bazaar_plugin" / "gamedata_client.py"


def load_gamedata_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gamedata_client_under_test", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(ModuleType, module)


def test_render_tooltip_replaces_ability_damage_value():
    module = load_gamedata_module()
    text = "造成 {ability.Damage} 点伤害"
    abilities = {"Damage": {"Action": {"$type": "TActionPlayerDamage"}}}
    assert module.render_tooltip(text, abilities, {}, {"DamageAmount": 25}) == "造成 25 点伤害"


def test_render_tooltip_preserves_unknown_placeholder():
    module = load_gamedata_module()
    assert module.render_tooltip("获得 {ability.Missing}", {}, {}, {}) == "获得 {ability.Missing}"


def test_render_tooltip_uses_coalesce_fallback():
    module = load_gamedata_module()
    result = module.render_tooltip("造成 {ability.Damage ?? Custom_0} 点伤害", {}, {}, {"Custom_0": 7})
    assert result == "造成 7 点伤害"


def test_coalesce_treats_zero_as_unresolved_like_game_client():
    module = load_gamedata_module()
    abilities = {"Damage": {"Action": {"$type": "TActionPlayerDamage"}}}
    assert module.render_tooltip(
        "造成 {ability.Damage ?? Custom_0} 点伤害", abilities, {},
        {"DamageAmount": 0, "Custom_0": 7},
    ) == "造成 7 点伤害"


def test_render_tooltip_formats_milliseconds_as_seconds():
    module = load_gamedata_module()
    abilities = {"Slow": {"Action": {"$type": "TActionCardSlow"}}}
    assert module.render_tooltip("减速 {ability.Slow}", abilities, {}, {"SlowAmount": 1500}) == "减速 1.5"


def test_ability_targets_defaults_to_zero_like_game_client():
    module = load_gamedata_module()
    abilities = {"Charge": {"Action": {"$type": "TActionCardCharge"}}}
    assert module.render_tooltip("目标 {ability.Charge.targets}", abilities, {}, {}) == "目标 0"


def test_ability_targets_does_not_fallback_to_target_count():
    module = load_gamedata_module()
    abilities = {"Destroy": {"Action": {"$type": "TActionCardDestroy", "TargetCount": {"$type": "TFixedValue", "Value": 3}}}}
    assert module.render_tooltip("目标 {ability.Destroy.targets}", abilities, {}, {}) == "目标 0"


def test_aura_targets_is_empty_like_game_client():
    module = load_gamedata_module()
    auras = {"Aura": {"Action": {"$type": "TAuraActionCardModifyAttribute"}}}
    assert module.render_tooltip("目标 {aura.Aura.targets}", {}, auras, {}) == "目标 "


def test_unknown_ability_action_resolves_to_zero_like_game_client():
    module = load_gamedata_module()
    abilities = {"Unknown": {"Action": {"$type": "TActionUnknown"}}}
    assert module.render_tooltip("数值 {ability.Unknown}", abilities, {}, {}) == "数值 0"


def test_game_reroll_uses_spawn_count():
    module = load_gamedata_module()
    abilities = {"Reroll": {"Action": {"$type": "TActionGameReroll", "SpawnCount": {"$type": "TFixedValue", "Value": 3}}}}
    assert module.render_tooltip("重掷 {ability.Reroll}", abilities, {}, {}) == "重掷 3"


def test_reference_value_reads_referenced_tier_attribute():
    module = load_gamedata_module()
    abilities = {"Damage": {"Action": {"$type": "TActionCardModifyAttribute", "AttributeType": "DamageAmount", "Value": {"$type": "TReferenceValueCardAttribute", "AttributeType": "Custom_1"}}}}
    assert module.render_tooltip("造成 {ability.Damage} 点伤害", abilities, {}, {"Custom_1": 42}) == "造成 42 点伤害"


def test_reference_value_applies_embedded_modifier():
    module = load_gamedata_module()
    abilities = {
        "Poison": {
            "Action": {
                "$type": "TActionCardModifyAttribute",
                "AttributeType": "PoisonApplyAmount",
                "Value": {
                    "$type": "TReferenceValueCardAttribute",
                    "AttributeType": "Custom_0",
                    "DefaultValue": 0.0,
                    "Modifier": {
                        "ModifyMode": "Multiply",
                        "Value": {"$type": "TFixedValue", "Value": 5.0},
                        "ShouldRound": True,
                    },
                },
            }
        }
    }
    assert module.render_tooltip("中毒 {ability.Poison}", abilities, {}, {"Custom_0": 2}) == "中毒 10"


def test_reference_value_ref_uses_raw_value_without_modifier():
    module = load_gamedata_module()
    abilities = {"Poison": {"Action": {
        "$type": "TActionCardModifyAttribute",
        "Value": {"$type": "TReferenceValueCardAttribute", "AttributeType": "Custom_0",
                  "Modifier": {"ModifyMode": "Multiply", "Value": {"$type": "TFixedValue", "Value": 5.0}}}
    }}}
    assert module.render_tooltip("{ability.Poison.ref}", abilities, {}, {"Custom_0": 2}) == "2"


def test_modifier_accessor_reads_modifier_value():
    module = load_gamedata_module()
    abilities = {"Damage": {"Action": {"$type": "TActionCardModifyAttribute", "AttributeType": "DamageAmount", "Value": {"$type": "TValueModifier", "Modifier": {"Value": {"$type": "TFixedValue", "Value": 9}}}}}}
    assert module.render_tooltip("增加 {ability.Damage.mod}", abilities, {}, {}) == "增加 9"


def test_modify_attribute_uses_action_value_before_target_attribute():
    module = load_gamedata_module()
    abilities = {"Damage": {"Action": {"$type": "TActionCardModifyAttribute", "AttributeType": "DamageAmount", "Value": {"$type": "TFixedValue", "Value": 9}}}}
    assert module.render_tooltip("修改 {ability.Damage}", abilities, {}, {"DamageAmount": 100}) == "修改 9"


def test_legendary_quest_uses_diamond_reward_tier():
    module = load_gamedata_module()
    reward = {"Tiers": {
        "Diamond": {"Attributes": {"Custom_0": 20}},
        "Legendary": {"Attributes": {"Custom_0": 99}},
    }}
    assert module._get_quest_reward_attributes(reward, "Legendary") == {"Custom_0": 20}


def test_runtime_reference_values_use_default_without_runtime_targets():
    module = load_gamedata_module()
    for value_type in (
        "TReferenceValueCardAttributeAggregate",
        "TReferenceValueCardAttributeUnscaled",
        "TReferenceValuePlayerAttribute",
        "TReferenceValuePlayerAttributeUnscaled",
    ):
        value = {
            "$type": value_type,
            "AttributeType": "Custom_0",
            "DefaultValue": 7,
        }
        assert module._resolve_value_object(value, {"Custom_0": 99}) == (7, "")
        assert module._resolve_raw_value_object(value, {"Custom_0": 99}) == (7, "")


def test_tier_tooltips_respect_tooltip_ids():
    module = load_gamedata_module()
    item = {
        "Tiers": {
            "Gold": {"TooltipIds": [0, 1], "Attributes": {}},
            "Diamond": {"TooltipIds": [0], "Attributes": {}},
        },
        "Localization": {"Tooltips": [
            {"Content": {"Text": "always"}},
            {"Content": {"Text": "gold only"}},
        ]},
    }
    assert module.get_tier_tooltips(item, "Gold") == ["always", "gold only"]
    assert module.get_tier_tooltips(item, "Diamond") == ["always"]


def test_cooldown_modifier_formats_milliseconds_as_seconds():
    module = load_gamedata_module()
    abilities = {"Trail": {"Action": {
        "$type": "TActionCardModifyAttribute",
        "AttributeType": "CooldownMax",
        "Value": {"$type": "TFixedValue", "Value": 1000},
    }}}
    assert module.render_tooltip(
        "双方所有物品的冷却时间延长 {ability.Trail} 秒",
        abilities, {}, {},
    ) == "双方所有物品的冷却时间延长 1 秒"


def test_flat_cooldown_reduction_aura_uses_target_attribute_unit():
    module = load_gamedata_module()
    auras = {"Trail": {"Action": {
        "$type": "TAuraActionCardModifyAttribute",
        "AttributeType": "FlatCooldownReduction",
        "Operation": "Subtract",
        "Value": {
            "$type": "TReferenceValueCardAttribute",
            "AttributeType": "Custom_0",
            "DefaultValue": 0.0,
        },
    }}}
    assert module.render_tooltip(
        "双方所有物品的冷却时间延长 {aura.Trail} 秒",
        {}, auras, {"Custom_0": 2000},
    ) == "双方所有物品的冷却时间延长 2 秒"
