"""
GameData.db 客户端
从游戏本体 SQLite 数据库读取物品/技能数据，转换为 howbazaar 兼容格式
"""
import difflib
import json
import re
import sqlite3
import unicodedata
from pathlib import Path
from typing import Any

# Action 类型 -> Amount Attribute 键名映射（逆向自 BazaarGameClient TooltipComponentAbility.GetAmountAttribute）
ACTION_ATTR_MAP = {
    "TActionPlayerDamage":          "DamageAmount",
    "TActionPlayerShieldApply":     "ShieldApplyAmount",
    "TActionPlayerHeal":            "HealAmount",   # 游戏实际使用的类型名
    "TActionPlayerHealApply":       "HealAmount",   # 旧版兼容
    "TActionPlayerPoisonApply":     "PoisonApplyAmount",
    "TActionPlayerPoisonRemove":    "PoisonRemoveAmount",
    "TActionPlayerBurnApply":       "BurnApplyAmount",
    "TActionPlayerBurnRemove":      "BurnRemoveAmount",
    "TActionPlayerRegenApply":      "RegenApplyAmount",
    "TActionPlayerRegenRemove":     "RegenRemoveAmount",
    "TActionPlayerRageApply":       "RageApplyAmount",
    "TActionPlayerRageRemove":      "RageRemoveAmount",
    "TActionPlayerShieldRemove":    "ShieldRemoveAmount",
    "TActionPlayerTempoApply":      "TempoApplyAmount",
    "TActionPlayerTempoRemove":     "TempoRemoveAmount",
    "TActionPlayerGoldModify":      "Gold",
    # 卡牌动作
    "TActionCardSlow":              "SlowAmount",
    "TActionCardFreeze":            "FreezeAmount",
    "TActionCardHaste":             "HasteAmount",
    "TActionCardCharge":            "ChargeAmount",
    "TActionCardReload":            "ReloadAmount",
    # 旧版兼容
    "TActionPlayerFreezeApply":     "FreezeAmount",
    "TActionPlayerSlowApply":       "SlowAmount",
    "TActionPlayerHasteApply":      "HasteAmount",
    "TActionPlayerChargeApply":     "ChargeAmount",
}

# Action 类型 -> Targets Attribute 键名映射（逆向自 BazaarGameClient TooltipComponentAbility.GetTargetsAttribute）
ACTION_TARGETS_MAP = {
    "TActionCardCharge":            "ChargeTargets",
    "TActionCardDestroy":           "DestroyTargets",
    "TActionCardDisable":           "DisableTargets",
    "TActionCardEnchantRemove":     "EnchantRemoveTargets",
    "TActionCardForceUse":          "ForceUseTargets",
    "TActionCardFreeze":            "FreezeTargets",
    "TActionCardHaste":             "HasteTargets",
    "TActionCardReload":            "ReloadTargets",
    "TActionCardRepair":            "RepairTargets",
    "TActionCardSlow":              "SlowTargets",
    "TActionCardTransform":         "TransformTargets",
    "TActionCardTransformDestroyed":"TransformTargets",
    "TActionCardUpgrade":           "UpgradeTargets",
    "TActionCardFlyingStart":       "FlyingTargets",
    "TActionCardFlyingStop":        "FlyingTargets",
    "TActionCardFlyingToggle":      "FlyingTargets",
}

# GameData 的时间属性统一以毫秒存储；游戏原生 Tooltip 展示时转换为秒。
# 冷却修改类效果在不同版本的数据中使用过以下属性名。
MS_ATTRS = {
    "SlowAmount", "FreezeAmount", "HasteAmount", "ChargeAmount", "ReloadAmount",
    "CooldownMax", "FlatCooldownReduction",
}

RUNTIME_REFERENCE_VALUE_TYPES = {
    "TReferenceValueCardAttributeAggregate",
    "TReferenceValueCardAttributeUnscaled",
    "TReferenceValuePlayerAttribute",
    "TReferenceValuePlayerAttributeUnscaled",
}


def ms_to_s(ms: float) -> str:
    """毫秒转秒，整数时去掉小数点"""
    s = ms / 1000
    return str(int(s)) if s == int(s) else str(round(s, 1))


def normalize_card_name(value: str) -> str:
    """规范化卡牌名称，统一 Unicode、大小写和连续空白。"""
    if not value:
        return ""
    value = unicodedata.normalize("NFKC", str(value)).strip().casefold()
    return " ".join(value.split())


def fmt_val(v: Any, attr_key: str = "") -> str:
    """格式化数值，毫秒属性自动转秒"""
    if v is None:
        return "?"
    if attr_key in MS_ATTRS:
        try:
            return ms_to_s(float(v))
        except (ValueError, TypeError):
            pass
    if isinstance(v, float):
        return str(int(v)) if v == int(v) else str(round(v, 1))
    return str(v)


def _round_native(value: float) -> float:
    """对应原版 TValueModifier.GetRounded：正数 0~1 至少为 1，四舍五入远离 0。"""
    if 0 < value < 1:
        return 1.0
    import math
    return float(math.floor(value + 0.5) if value >= 0 else math.ceil(value - 0.5))


def _resolve_value_object(value: dict, tier_attrs: dict) -> tuple[Any, str]:
    """按原版 ITValue 的静态数据子集求值，返回 (值, 属性键)。"""
    vtype = value.get("$type", "")
    if vtype == "TFixedValue":
        return value.get("Value"), ""
    if vtype in RUNTIME_REFERENCE_VALUE_TYPES:
        return value.get("DefaultValue", 0.0), ""
    ref_attr = value.get("AttributeType", "")
    if vtype == "TReferenceValueCardAttribute":
        raw = tier_attrs.get(ref_attr, value.get("DefaultValue", 0.0))
        attr_key = ref_attr
        modifier = value.get("Modifier")
        if isinstance(modifier, dict):
            modified, _ = _apply_modifier(raw, modifier, tier_attrs)
            return modified, attr_key
        return raw, attr_key
    return None, ""


def _apply_modifier(original: Any, modifier: dict, tier_attrs: dict) -> tuple[Any, str]:
    modifier_value = modifier.get("Value")
    if not isinstance(modifier_value, dict):
        return original, ""
    amount, amount_key = _resolve_value_object(modifier_value, tier_attrs)
    if amount is None:
        return original, amount_key
    try:
        mode = modifier.get("ModifyMode", "")
        if mode == "Add":
            result = original + amount
        elif mode == "Subtract":
            result = original - amount
        elif mode == "Multiply":
            result = original * amount
        elif mode == "Divide":
            result = original / amount if amount != 0 else original
        else:
            result = original
        if modifier.get("ShouldRound") and mode in ("Multiply", "Divide"):
            result = _round_native(float(result))
        return result, amount_key
    except (TypeError, ValueError, ZeroDivisionError):
        return original, amount_key


def _resolve_raw_value_object(value: dict, tier_attrs: dict) -> tuple[Any, str]:
    """对应原版 ITValue.GetRawValue：只解析引用本体，不应用其 Modifier。"""
    vtype = value.get("$type", "")
    if vtype == "TFixedValue":
        return value.get("Value"), ""
    if vtype in RUNTIME_REFERENCE_VALUE_TYPES:
        return value.get("DefaultValue", 0.0), ""
    if vtype == "TReferenceValueCardAttribute":
        ref_attr = value.get("AttributeType", "")
        return tier_attrs.get(ref_attr, value.get("DefaultValue", 0.0)), ref_attr
    return None, ""


def extract_value(action: dict, tier_attrs: dict) -> tuple[Any, str]:
    """
    从 action 中提取数值，返回 (值, attr_key)
    attr_key 用于判断是否需要毫秒转秒
    """
    atype = action.get("$type", "")

    # 修改属性类动作：值的来源是 Value 字段，AttributeType 是被修改的目标属性
    if atype in (
        "TActionCardModifyAttribute",
        "TAuraActionCardModifyAttribute",
        "TActionPlayerModifyAttribute",
        "TAuraActionPlayerModifyAttribute",
    ):
        attr_type = action.get("AttributeType", "")
        val = action.get("Value", {})
        if not isinstance(val, dict):
            return None, ""
        vtype = val.get("$type", "")
        if vtype == "TFixedValue":
            return val.get("Value"), attr_type
        if "ReferenceValue" in vtype or vtype.endswith("Attribute"):
            resolved, source_attr = _resolve_value_object(val, tier_attrs)
            # 原生 Tooltip 根据动作修改的目标属性决定显示单位，而不是根据
            # 引用值的来源属性决定。例：FlatCooldownReduction 引用 Custom_0，
            # Custom_0=1000 仍应显示为 1 秒。
            return resolved, attr_type or source_attr
        # fallback：直接读目标属性值
        if attr_type and attr_type in tier_attrs:
            return tier_attrs[attr_type], attr_type
        return None, ""

    # 游戏原版对 Reroll 读取 SpawnCount，而不是 AttributeType。
    if atype == "TActionGameReroll":
        spawn_count = action.get("SpawnCount")
        if isinstance(spawn_count, dict) and spawn_count.get("$type") == "TFixedValue":
            return spawn_count.get("Value"), ""
        return None, ""

    # TActionGameSpawnCards / TActionGameDealCards：取 SpawnContext.Limit
    if atype in ("TActionGameSpawnCards", "TActionGameDealCards"):
        sc = action.get("SpawnContext", {})
        if isinstance(sc, dict):
            lim = sc.get("Limit")
            if isinstance(lim, dict) and lim.get("$type") == "TFixedValue":
                return lim.get("Value"), ""
        return None, ""

    # 直接动作：从 ACTION_ATTR_MAP 映射
    attr_key = ACTION_ATTR_MAP.get(atype, "")
    if attr_key and attr_key in tier_attrs:
        return tier_attrs[attr_key], attr_key

    # 尝试从 action 内嵌 Value 读固定值
    val = action.get("Value", {})
    if isinstance(val, dict) and val.get("$type") == "TFixedValue":
        return val.get("Value"), ""

    return None, ""


# 遭遇 ID → 名称缓存（懒加载）
_encounter_name_cache: dict[str, str] = {}
_encounter_cache_loaded = False

def _get_encounter_name(encounter_id: str, db_path: "str | Path") -> str:
    """根据遭遇 ID 从 GameData.db 查名称，优先返回中文。"""
    global _encounter_cache_loaded
    if not _encounter_cache_loaded:
        _encounter_cache_loaded = True
        try:
            from . import translations as _trans
            conn = sqlite3.connect(str(db_path))
            rows = conn.execute("SELECT Data FROM cards").fetchall()
            conn.close()
            for (data,) in rows:
                if isinstance(data, bytes):
                    data = data.decode("utf-8")
                d = json.loads(data)
                if d.get("Type") != "EventEncounter":
                    continue
                eid = d.get("Id", "")
                loc = d.get("Localization") or {}
                key = loc.get("Title", {}).get("Key", "")
                txt = loc.get("Title", {}).get("Text", "")
                zh = (_trans.get_zh_by_hash(key) if key else None) or txt
                if eid and zh:
                    _encounter_name_cache[eid] = zh
        except Exception:
            pass
    return _encounter_name_cache.get(encounter_id, "")


def build_tier_attrs(item_data: dict, tier_name: str) -> dict:
    """构建某个 tier 的完整属性（累加 Bronze→目标tier）"""
    tier_order = ["Bronze", "Silver", "Gold", "Diamond", "Legendary"]
    base: dict = {}
    for t in tier_order:
        tier = item_data.get("Tiers", {}).get(t, {})
        base.update(tier.get("Attributes", {}))
        if t == tier_name:
            break
    return base


def render_tooltip(
    text: str,
    abilities: dict,
    auras: dict,
    tier_attrs: dict,
) -> str:
    """渲染 tooltip 模板，替换 {ability.X} / {aura.X} 占位符。
    逻辑对齐 BazaarGameClient TooltipComponentAbility，支持 {A ?? B} coalesce。
    """

    def resolve_single(ph: str):
        parts = ph.strip().split(".")
        if len(parts) < 2:
            # {custom_0} / {custom_1} / {TempoCost} 等直接从 tier_attrs 取
            key = ph.strip()
            # custom_0 → Custom_0
            for try_key in (key, key.capitalize(), "Custom_" + key.split("_")[-1] if "_" in key else ""):
                if try_key and try_key in tier_attrs:
                    return fmt_val(tier_attrs[try_key])
            # TempoCost 大小写精确匹配
            if key in tier_attrs:
                return fmt_val(tier_attrs[key])
            return None
        prefix, ab_id = parts[0], parts[1]
        sub = parts[2] if len(parts) > 2 else ""

        if prefix == "ability":
            ab = abilities.get(ab_id)
            if ab is None:
                return None
            action = ab.get("Action", {})
            atype = action.get("$type", "")
            if sub == "targets":
                targets_attr = ACTION_TARGETS_MAP.get(atype)
                if targets_attr and targets_attr in tier_attrs:
                    return fmt_val(tier_attrs[targets_attr])
                return "0"
            if sub == "ref":
                val = action.get("Value", {})
                if isinstance(val, dict):
                    value, attr_key = _resolve_raw_value_object(val, tier_attrs)
                    if value is not None:
                        return fmt_val(value, attr_key)
                return None
            if sub == "mod":
                # ability.X.mod → action.Value.Modifier.Value（逆向 GetModifierValue）
                val = action.get("Value", {})
                if isinstance(val, dict):
                    mod = val.get("Modifier", {})
                    if isinstance(mod, dict):
                        mv = mod.get("Value", {})
                        if isinstance(mv, dict) and mv.get("$type") == "TFixedValue":
                            return fmt_val(mv.get("Value"))
                        # modifier.Value 是 TReferenceValueCardAttribute
                        if isinstance(mv, dict) and "ReferenceValue" in mv.get("$type", ""):
                            value, _ = _resolve_value_object(mv, tier_attrs)
                            if value is not None:
                                return fmt_val(value)
                return None
            v, ak = extract_value(action, tier_attrs)
            if v is None:
                # 原版 GetValue() 对未知/缺失属性通过 TryGetAttributeValue(...).GetValueOrDefault() 返回 0。
                return "0"
            return fmt_val(v, ak)

        elif prefix == "aura":
            aura = auras.get(ab_id)
            if aura is None:
                # 原生组件不存在时不会把 token 原样写回文本。
                return ""
            action = aura.get("Action", {})
            if sub == "targets":
                # 原版 TooltipComponentAura.Resolve() 对 Targets 明确返回 null，
                # 由组件渲染为空字符串，而不是保留占位符。
                return ""
            if sub == "mod":
                # aura.X.mod → action.Value.Modifier.Value（逆向 GetModifierValue）
                val = action.get("Value", {})
                if isinstance(val, dict):
                    mod = val.get("Modifier", {})
                    if isinstance(mod, dict):
                        mv = mod.get("Value", {})
                        if isinstance(mv, dict) and mv.get("$type") == "TFixedValue":
                            return fmt_val(mv.get("Value"))
                        # modifier.Value 是 TReferenceValueCardAttribute
                        if isinstance(mv, dict) and "ReferenceValue" in mv.get("$type", ""):
                            value, _ = _resolve_value_object(mv, tier_attrs)
                            if value is not None:
                                return fmt_val(value)
                return ""
            v, ak = extract_value(action, tier_attrs)
            if v is None:
                return ""
            return fmt_val(v, ak)

        return None

    def _coalesce_value(value: str | None) -> str | None:
        """原版 IsZero：数值绝对值小于 0.0001 时视为未解析。"""
        if value is None:
            return None
        try:
            if abs(float(value)) < 0.0001:
                return None
        except (TypeError, ValueError):
            pass
        return value

    def replace_ph(m: re.Match) -> str:
        ph = m.group(1)
        if "??" in ph:
            left, right = ph.split("??", 1)
            left_value = _coalesce_value(resolve_single(left.strip()))
            if left_value is not None:
                return left_value
            right_value = _coalesce_value(resolve_single(right.strip()))
            if right_value is not None:
                return right_value
            return ""
        r = resolve_single(ph)
        return r if r is not None else m.group(0)

    return re.sub(r"\{([^}]+)\}", replace_ph, text)


def _get_quest_reward_attributes(reward: dict, tier_name: str) -> dict:
    """按游戏 TooltipContextExtensions 选择任务奖励属性。

    游戏中传奇卡实际使用钻石等级的任务奖励属性。
    """
    reward_tiers = reward.get("Tiers") or {}
    lookup_tier = "Diamond" if tier_name == "Legendary" else tier_name
    tier = reward_tiers.get(lookup_tier)
    if isinstance(tier, dict):
        return tier.get("Attributes") or {}
    return reward.get("Attributes") or {}


def get_quest_tooltips(item_data: dict, tier_name: str, db_path: "str | Path" = "") -> list[dict]:
    """解析 Quest 条件和奖励，返回列表 [{condition, reward_tooltips}]"""
    TRIGGER_ZH = {
        "TTriggerOnCardFired":              "触发本卡",
        "TTriggerOnCardUsed":               "使用本卡",
        "TTriggerOnCardSold":               "出售本卡",
        "TTriggerOnCardBought":             "购买本卡",
        "TTriggerOnCardPurchased":          "购买本卡",
        "TTriggerOnEncounterSelected":      "选择一个遭遇",
        "TTriggerOnDayStart":               "每天开始",
        "TTriggerOnCombatStart":            "战斗开始",
        "TTriggerOnCombatEnd":              "战斗结束",
        "TTriggerOnFightEnded":             "战斗结束",
        "TTriggerOnPlayerDamaged":          "受到伤害",
        "TTriggerOnPlayerKilled":           "击杀对手",
        "TTriggerOnItemUsed":               "使用物品",
        "TTriggerOnCardStartedFlying":      "物品开始飞行",
        "TTriggerOnCardStartsFlying":       "物品开始飞行",
        "TTriggerOnCardAttributeChanged":   "属性改变",
        "TTriggerOnCardQuestCompleted":     "任务完成",
        "TTriggerOnCardPerformedSlow":      "触发减速",
        "TTriggerOnCardPerformedPoison":    "触发中毒",
        "TTriggerOnCardPerformedRegen":     "触发回复",
        "TTriggerOnCardPerformedBurn":      "触发燃烧",
        "TTriggerOnCardPerformedFreeze":    "触发冻结",
        "TTriggerOnCardPerformedHaste":     "触发加速",
        "TTriggerOnCardPerformedDestruction": "触发摧毁",
    }
    ATTR_ZH = {
        "Quest_1": "任务1", "Quest_2": "任务2", "Quest_3": "任务3",
        "Quest_4": "任务4", "Quest_5": "任务5",
    }
    TIER_ZH = {"Bronze": "铜", "Silver": "银", "Gold": "金", "Diamond": "钻", "Legendary": "传说"}
    COND_TYPE_ZH = {
        "TCardConditionalTier": lambda c: "品质为" + "/".join(TIER_ZH.get(t, t) for t in (c.get("Tiers") or [])),
        "TCardConditionalTag":  lambda c: "有标签" + str(c.get("Tag", "")),
    }

    def get_trigger_name(trigger: dict) -> str:
        """递归解析 trigger，支持 TTriggerOr"""
        ttype = trigger.get("$type", "")
        if ttype == "TTriggerOr":
            sub_triggers = trigger.get("Triggers") or []
            parts = [get_trigger_name(t) for t in sub_triggers]
            # 去重后用"或"连接
            seen, unique = set(), []
            for p in parts:
                if p not in seen:
                    seen.add(p)
                    unique.append(p)
            return "或".join(unique) if unique else "触发"
        name = TRIGGER_ZH.get(ttype, ttype.replace("TTriggerOn", ""))
        cond = trigger.get("Conditions")
        if cond:
            ctype = cond.get("$type", "")
            if ctype == "TCardConditionalId" and db_path:
                enc_id = cond.get("Id", "")
                enc_name = _get_encounter_name(enc_id, db_path) if enc_id else ""
                if enc_name:
                    name = f"选择遭遇「{enc_name}」"
            else:
                handler = COND_TYPE_ZH.get(ctype)
                if handler:
                    try:
                        name += "（" + handler(cond) + "）"
                    except Exception:
                        pass
        return name

    quests = item_data.get("Quests") or []
    tier_attrs = build_tier_attrs(item_data, tier_name)
    abilities = item_data.get("Abilities", {})
    auras = item_data.get("Auras", {})
    result = []

    for qg in quests:
        for entry in (qg.get("Entries") or []):
            # 触发条件
            trigger = entry.get("Trigger") or {}
            tname = get_trigger_name(trigger)
            target = entry.get("Target", 1)
            condition_text = f"{tname} x{target}" if target > 1 else tname

            # 奖励
            reward = entry.get("Reward") or {}
            reward_tips = []
            rew_loc = reward.get("Localization") or {}
            for t in rew_loc.get("Tooltips") or []:
                txt = (t.get("Content") or {}).get("Text", "")
                key = (t.get("Content") or {}).get("Key", "")
                if txt:
                    # 先用 hash 查官方翻译模板，再填值
                    from . import translations as _trans
                    txt_zh = (_trans.get_zh_by_hash(key) if key else None) or _trans.get_tooltip_zh(txt) or txt
                    rew_abs = {**abilities, **(reward.get("Abilities") or {})}
                    rew_auras = {**auras, **(reward.get("Auras") or {})}
                    rew_attrs = {**tier_attrs}
                    rew_attrs.update(_get_quest_reward_attributes(reward, tier_name))
                    reward_tips.append(render_tooltip(txt_zh, rew_abs, rew_auras, rew_attrs))

            result.append({
                "condition": condition_text,
                "reward_tooltips": reward_tips,
            })

    return result


def get_tier_tooltips(item_data: dict, tier_name: str) -> list[str]:
    """获取某个 tier 的 tooltip 列表（已渲染，含 CD）"""
    tier_attrs = build_tier_attrs(item_data, tier_name)
    abilities = item_data.get("Abilities", {})
    auras = item_data.get("Auras", {})

    tooltips: list[str] = []

    # Cooldown
    cooldown = tier_attrs.get("CooldownMax")
    if cooldown:
        tooltips.append(f"Cooldown {ms_to_s(cooldown)}s")

    # Multicast
    multicast = tier_attrs.get("Multicast", 1)
    if multicast and multicast > 1:
        tooltips.append(f"Multicast {int(multicast)}")

    # 主 tooltips：严格按当前品质的 TooltipIds 选择，并过滤状态条件。
    all_tooltips = item_data.get("Localization", {}).get("Tooltips", [])
    tier_data = (item_data.get("Tiers") or {}).get(tier_name) or {}
    tooltip_ids = tier_data.get("TooltipIds")
    if tooltip_ids is None:
        selected_tooltips = all_tooltips
    else:
        selected_tooltips = [
            all_tooltips[i] for i in tooltip_ids
            if isinstance(i, int) and 0 <= i < len(all_tooltips)
        ]
    for t in selected_tooltips:
        tip_cond = t.get("TooltipCondition")
        if tip_cond and tip_cond not in (None, "None"):
            continue  # Chilled/Heated/Enraged 等状态条件 tooltip 跳过
        txt = t.get("Content", {}).get("Text", "")
        if txt:
            tooltips.append(render_tooltip(txt, abilities, auras, tier_attrs))

    return tooltips



def get_enchant_tooltips(item_data: dict, ench_name: str) -> list[str]:
    """获取附魔的 tooltip 列表"""
    ench = item_data.get("Enchantments", {}).get(ench_name, {})
    base_tier = item_data.get("StartingTier", "Bronze")
    base_attrs = build_tier_attrs(item_data, base_tier)
    ench_attrs = {**base_attrs, **ench.get("Attributes", {})}

    # 合并 base + 附魔的 abilities/auras
    all_abilities = {**item_data.get("Abilities", {}), **ench.get("Abilities", {})}
    all_auras = {**item_data.get("Auras", {}), **ench.get("Auras", {})}

    tooltips: list[str] = []
    # 官方中文是按 tooltip Content.Key 存在 zh-CN.bytes 中的模板。必须先取
    # 模板、再替换 {ability.*}/{aura.*}，否则数值渲染后无法再以英文全文匹配翻译。
    from . import translations as trans
    for t in ench.get("Localization", {}).get("Tooltips", []):
        content = t.get("Content") or {}
        txt = content.get("Text", "")
        if txt:
            key = content.get("Key", "")
            txt_zh = (trans.get_zh_by_hash(key) if key else None) or trans.get_tooltip_zh(txt) or txt
            tooltips.append(render_tooltip(txt_zh, all_abilities, all_auras, ench_attrs))
    return tooltips


def convert_item_to_howbazaar_format(item_data: dict) -> dict:
    """将 GameData.db 格式的 item 转换为 howbazaar 兼容格式"""
    loc = item_data.get("Localization", {})
    name = loc.get("Title", {}).get("Text", item_data.get("InternalName", "Unknown"))

    # 构建 tiers
    tiers: dict = {}
    for tier_name in ["Bronze", "Silver", "Gold", "Diamond", "Legendary"]:
        if tier_name in item_data.get("Tiers", {}):
            tiers[tier_name] = {"tooltips": get_tier_tooltips(item_data, tier_name)}

    # 构建 enchantments
    enchantments: list[dict] = []
    for ench_name in (item_data.get("Enchantments") or {}):
        tips = get_enchant_tooltips(item_data, ench_name)
        if tips:
            enchantments.append({"type": ench_name, "tooltips": tips})

    # unifiedTooltips：用 startingTier 的 tooltips
    unified = get_tier_tooltips(item_data, item_data.get("StartingTier", "Bronze"))

    return {
        "id": item_data.get("Id", ""),
        "name": name,
        "startingTier": item_data.get("StartingTier", "Bronze"),
        "tiers": tiers,
        "tags": item_data.get("Tags", []),
        "hiddenTags": item_data.get("HiddenTags", []),
        "customTags": [],
        "size": item_data.get("Size", "Medium"),
        "heroes": item_data.get("Heroes", []),
        "enchantments": enchantments,
        "quests": get_quest_tooltips(item_data, item_data.get("StartingTier", "Bronze")),
        "unifiedTooltips": unified,
        "combatEncounters": [],
    }


def convert_skill_to_howbazaar_format(skill_data: dict) -> dict:
    """将 GameData.db 格式的 skill 转换为 howbazaar 兼容格式"""
    loc = skill_data.get("Localization", {})
    name = loc.get("Title", {}).get("Text", skill_data.get("InternalName", "Unknown"))

    tiers: dict = {}
    for tier_name in ["Bronze", "Silver", "Gold", "Diamond", "Legendary"]:
        if tier_name in skill_data.get("Tiers", {}):
            tiers[tier_name] = {"tooltips": get_tier_tooltips(skill_data, tier_name)}

    unified = get_tier_tooltips(skill_data, skill_data.get("StartingTier", "Bronze"))

    return {
        "id": skill_data.get("Id", ""),
        "name": name,
        "startingTier": skill_data.get("StartingTier", "Bronze"),
        "tiers": tiers,
        "tags": skill_data.get("Tags", []),
        "hiddenTags": skill_data.get("HiddenTags", []),
        "customTags": [],
        "heroes": skill_data.get("Heroes", []),
        "enchantments": [],
        "quests": [],
        "unifiedTooltips": unified,
        "combatEncounters": [],
    }


TIER_ZH   = {"Bronze": "铜", "Silver": "银", "Gold": "金", "Diamond": "钻", "Legendary": "传说"}
HERO_ZH   = {
    "Common": "通用", "Pygmalien": "皮格马利翁", "Vanessa": "瓦内萨",
    "Dooley": "杜利", "Stelle": "斯特尔", "Jules": "朱尔斯",
    "Mak": "马克", "The Dragons": "双龙", "Hero8": "双龙", "Karnok": "卡诺克",
}
TAG_ZH    = {
    "Weapon": "武器", "Shield": "护盾", "Heal": "治疗", "Damage": "伤害",
    "Burn": "灼烧", "Freeze": "冻结", "Poison": "毒素", "Slow": "减速",
    "Haste": "加速", "Crit": "暴击", "Ammo": "弹药", "Tool": "工具",
    "Food": "食物", "Toy": "玩具", "Apparel": "服装", "Property": "房产",
    "Vehicle": "载具", "Aquatic": "水系", "Dinosaur": "恐龙", "Dragon": "龙",
    "Drone": "无人机", "Flying": "飞行", "Friend": "伙伴", "Tech": "科技",
    "Relic": "遗物", "Reagent": "试剂", "Potion": "药水", "Quest": "任务",
    "Economy": "经济", "Income": "收入", "Gold": "黄金", "Loot": "战利品",
    "Experience": "经验", "Level": "等级", "Health": "生命值", "Regen": "回复",
    "Core": "核心", "Ray": "射线", "Ticket": "票券", "Value": "价值",
    "Joy": "喜悦", "Charge": "充能", "Cooldown": "冷却", "Instrument": "乐器",
    "Unpurchasable": "不可购买",
}
ENC_ZH    = {
    "Golden": "黄金", "Heavy": "沉重", "Icy": "寒冰", "Turbo": "疾速",
    "Shielded": "护盾", "Restorative": "回复", "Toxic": "毒素",
    "Fiery": "炽焰", "Shiny": "闪亮", "Obsidian": "黑曜石",
    "Deadly": "致命", "Radiant": "辉耀", "Mossy": "长青",
}


def format_card_from_raw(raw: dict, zh_name: str = "", db_path: str = "", show_enchants: bool = False) -> str:
    """将 GameData.db 原始 dict 格式化为可读文本（用于 #bz db 输出）。"""
    from . import translations as trans

    card_type = raw.get("$type", "")
    is_item = card_type == "TCardItem"
    is_skill = card_type == "TCardSkill"

    loc = raw.get("Localization") or {}
    title_text = loc.get("Title", {}).get("Text", "") or raw.get("InternalName", "")
    title_key  = loc.get("Title", {}).get("Key", "")
    name_zh = zh_name or trans.get_zh(title_text) or (trans.get_zh_by_hash(title_key) if title_key else "") or title_text
    name_en = title_text

    size_label = {"Small": "小", "Medium": "中", "Large": "大", "Small Large": "小/大"}.get(
        raw.get("Size", ""), raw.get("Size", ""))
    type_label = {"TCardItem": "物品", "TCardSkill": "技能"}.get(card_type, "")
    starting_tier = raw.get("StartingTier", "Bronze")
    heroes = "、".join(HERO_ZH.get(h, h) for h in (raw.get("Heroes") or [])) or "通用"
    tags = [TAG_ZH.get(t, t) for t in (raw.get("Tags") or [])]

    out = []
    header = f"📦 {name_zh}"
    if name_en and name_en != name_zh:
        header += f"（{name_en}）"
    out.append(header)

    meta = f"类型:{type_label}  尺寸:{size_label}  起始品质:{TIER_ZH.get(starting_tier, starting_tier)}  英雄:{heroes}"
    out.append(meta)
    if tags:
        out.append("标签: " + " ".join(tags))

    # 按 tier 展示 tooltips
    tier_order = [t for t in ("Bronze", "Silver", "Gold", "Diamond", "Legendary")
                  if t in (raw.get("Tiers") or {})]
    abilities = raw.get("Abilities") or {}
    auras = raw.get("Auras") or {}

    if tier_order:
        out.append("─")
        # 合并相同内容的 tier（避免重复展示）
        prev_block = None
        prev_tiers: list[str] = []
        def flush(tiers_list, block):
            label = "/".join(TIER_ZH.get(t, t) for t in tiers_list)
            out.append(f"[{label}] " + "  ".join(block))

        for tn in tier_order:
            tier_attrs = build_tier_attrs(raw, tn)
            lines: list[str] = []
            cd = tier_attrs.get("CooldownMax")
            if cd:
                lines.append(f"冷却{ms_to_s(cd)}s")
            multicast = tier_attrs.get("Multicast", 1)
            if multicast and multicast > 1:
                lines.append(f"多重x{int(multicast)}")
            # 主 tooltips
            for t in loc.get("Tooltips") or []:
                tip_cond = t.get("TooltipCondition")
                if tip_cond and tip_cond not in (None, "None"):
                    continue
                txt = (t.get("Content") or {}).get("Text", "")
                if txt:
                    # 用 hash 查官方翻译模板，再填值（和游戏本体逻辑对齐）
                    key = (t.get("Content") or {}).get("Key", "")
                    txt_zh = (trans.get_zh_by_hash(key) if key else None) or trans.get_tooltip_zh(txt) or txt
                    rendered = render_tooltip(txt_zh, abilities, auras, tier_attrs)
                    lines.append(rendered)
            if lines == prev_block:
                prev_tiers.append(tn)
            else:
                if prev_block is not None:
                    flush(prev_tiers, prev_block)
                prev_block = lines
                prev_tiers = [tn]
        if prev_block is not None:
            flush(prev_tiers, prev_block)

    # Quest
    quests = raw.get("Quests") or []
    if quests:
        quest_tips = get_quest_tooltips(raw, starting_tier, db_path=db_path)
        if quest_tips:
            out.append("─")
            out.append("任务:")
            for q in quest_tips:
                reward_str = "  ".join(q["reward_tooltips"]) if q["reward_tooltips"] else "（升级效果）"
                out.append(f"  ✦ {q['condition']} → {reward_str}")

    # 附魔
    enchs = raw.get("Enchantments") or {}
    if enchs and show_enchants:
        out.append("─")
        out.append("附魔效果:")
        for enc_key, enc_data in enchs.items():
            enc_name = ENC_ZH.get(enc_key, enc_key)
            tips = get_enchant_tooltips(raw, enc_key)
            if tips:
                zh_tips = [trans.get_tooltip_zh(t) or t for t in tips]
                out.append(f"  [{enc_name}] " + "  ".join(zh_tips))
            else:
                out.append(f"  [{enc_name}]")

    return "\n".join(out)


_CARD_NAME_INDEX_CACHE: dict[str, tuple[tuple[int, int], dict[str, list[dict]]]] = {}


def build_card_name_index(db_path: str | Path) -> dict[str, list[dict]]:
    """读取一次 cards 表并建立规范化名称索引。"""
    path = Path(db_path)
    stat = path.stat()
    signature = (stat.st_mtime_ns, stat.st_size)
    cached = _CARD_NAME_INDEX_CACHE.get(str(path))
    if cached and cached[0] == signature:
        return cached[1]

    conn = sqlite3.connect(str(path))
    try:
        rows = conn.execute("SELECT Data FROM cards").fetchall()
    finally:
        conn.close()

    index: dict[str, list[dict]] = {}
    for (data,) in rows:
        if isinstance(data, bytes):
            data = data.decode("utf-8")
        try:
            card = json.loads(data)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        aliases = (
            card.get("InternalName", ""),
            (card.get("Localization") or {}).get("Title", {}).get("Text", ""),
        )
        for alias in aliases:
            key = normalize_card_name(alias)
            if key and card not in index.setdefault(key, []):
                index[key].append(card)
    _CARD_NAME_INDEX_CACHE[str(path)] = (signature, index)
    return index


def suggest_cards(name: str, db_path: "str | Path", limit: int = 5) -> list[dict]:
    """从本地名称索引返回相近的物品/技能候选，不进行远程查询。"""
    query = normalize_card_name(name)
    if not query or limit <= 0:
        return []

    scored: list[tuple[float, str, dict]] = []
    seen_ids: set[str] = set()
    for alias, cards in build_card_name_index(db_path).items():
        ratio = difflib.SequenceMatcher(None, query, alias).ratio()
        contains = query in alias or alias in query
        if not contains and ratio < 0.6:
            continue
        score = ratio + (0.4 if contains else 0.0)
        for card in cards:
            if card.get("Type") not in ("Item", "Skill"):
                continue
            card_id = str(card.get("Id", ""))
            if card_id in seen_ids:
                continue
            seen_ids.add(card_id)
            scored.append((score, alias, card))

    scored.sort(key=lambda item: (-item[0], item[1]))
    if not scored:
        return []
    best_score = scored[0][0]
    close_matches = [item for item in scored if item[0] >= best_score - 0.2]
    return [card for _, _, card in close_matches[:limit]]


def query_raw_by_name(name: str, db_path: "str | Path") -> dict | None:
    """从 GameData.db 按名称查卡牌原始数据，优先返回 Item/Skill。"""
    index = build_card_name_index(db_path)
    matches = index.get(normalize_card_name(name), [])
    fallback = None
    for card in matches:
        if card.get("Type") in ("Item", "Skill"):
            return card
        if fallback is None:
            fallback = card
    return fallback


class GameDataClient:
    """从 GameData.db 读取并转换为 howbazaar 兼容格式"""

    def __init__(self, db_path: "str | Path"):
        self.db_path = Path(db_path)
        self._items: list[dict] = []
        self._skills: list[dict] = []
        self._monsters: list[dict] = []      # Boss/怪物
        self._pedestals: list[dict] = []     # 附魔台
        self._combats: list[dict] = []       # 战斗遭遇
        # id -> raw data 索引（用于 monster 手牌反查）
        self._item_by_id: dict[str, dict] = {}

    def load(self) -> None:
        """加载并转换数据"""
        if not self.db_path.exists():
            raise FileNotFoundError(f"GameData.db 不存在: {self.db_path}")

        conn = sqlite3.connect(str(self.db_path))
        cur = conn.cursor()

        # 加载 cards 表
        cur.execute("SELECT Data FROM cards")
        rows = cur.fetchall()

        # 加载 monsters 表
        cur.execute("SELECT Data FROM monsters")
        monster_rows = cur.fetchall()

        conn.close()

        # 处理 cards
        items: list[dict] = []
        skills: list[dict] = []
        pedestals: list[dict] = []
        combats: list[dict] = []
        raw_items: dict[str, dict] = {}  # id -> raw data

        for (data,) in rows:
            if isinstance(data, bytes):
                data = data.decode("utf-8")
            try:
                d = json.loads(data)
            except Exception:
                continue

            card_type = d.get("$type")
            if card_type == "TCardItem":
                converted = convert_item_to_howbazaar_format(d)
                items.append(converted)
                raw_items[d.get("Id", "")] = converted  # 保存转换后的物品
            elif card_type == "TCardSkill":
                skills.append(convert_skill_to_howbazaar_format(d))
            elif card_type == "TCardEncounterPedestal":
                pedestals.append(self._convert_pedestal(d))
            elif card_type == "TCardEncounterCombat":
                combats.append(d)  # 先保存原始数据，等 monsters 加载后再处理

        # 处理 monsters，关联手牌物品名
        monsters: list[dict] = []
        for (data,) in monster_rows:
            if isinstance(data, bytes):
                data = data.decode("utf-8")
            try:
                d = json.loads(data)
            except Exception:
                continue
            monsters.append(self._convert_monster(d, raw_items))

        self._items = items
        self._skills = skills
        self._monsters = monsters
        self._pedestals = pedestals
        self._item_by_id = raw_items
        # 战斗遭遇关联 monster
        self._combats = [self._convert_combat(c) for c in combats]

        print(f"[GameDataClient] 已加载 {len(items)} 物品, {len(skills)} 技能, "
              f"{len(monsters)} Boss, {len(pedestals)} 附魔台, {len(combats)} 战斗遭遇")

    def _convert_monster(self, d: dict, item_by_id: dict) -> dict:
        """转换 monster 数据"""
        attrs = d.get("Player", {}).get("Attributes", {})
        hand = d.get("Player", {}).get("Hand", {})
        hand_items = hand.get("Items", [])

        # 把 TemplateId 转换成物品名
        items_in_hand = []
        for inst in hand_items:
            tid = inst.get("TemplateId", "")
            tier = inst.get("Tier", "")
            ench = inst.get("EnchantmentType")
            item = item_by_id.get(tid)
            name = item.get("name", tid) if item else tid
            items_in_hand.append({
                "name": name,
                "tier": tier,
                "enchantment": ench,
            })

        return {
            "id": d.get("Id", ""),
            "name": d.get("InternalName", "Unknown"),
            "health": attrs.get("HealthMax", 0),
            "level": attrs.get("Level", 0),
            "prestige": attrs.get("Prestige", 0),
            "items": items_in_hand,
        }

    def _convert_pedestal(self, d: dict) -> dict:
        """转换附魔台数据"""
        loc = d.get("Localization", {})
        name = loc.get("Title", {}).get("Text", d.get("InternalName", ""))
        desc = loc.get("Description", {}).get("Text", "") if loc.get("Description") else ""
        return {
            "id": d.get("Id", ""),
            "name": name,
            "internal_name": d.get("InternalName", ""),
            "tier": d.get("StartingTier", ""),
            "heroes": d.get("Heroes", []),
            "description": desc,
        }

    def _convert_combat(self, d: dict) -> dict:
        """转换战斗遭遇数据"""
        loc = d.get("Localization", {})
        name = loc.get("Title", {}).get("Text", d.get("InternalName", ""))
        combatant = d.get("CombatantType", {})
        monster_id = combatant.get("MonsterTemplateId", "")
        level = combatant.get("Level", 0)
        return {
            "id": d.get("Id", ""),
            "name": name,
            "internal_name": d.get("InternalName", ""),
            "tier": d.get("StartingTier", ""),
            "heroes": d.get("Heroes", []),
            "monster_id": monster_id,
            "level": level,
            "gold": d.get("RewardCombatGold", 0),
            "xp": d.get("RewardCombatXp", 0),
            "sandstorm": d.get("SandstormEnabled", False),
        }

    def items(self) -> list[dict]:
        return self._items

    def skills(self) -> list[dict]:
        return self._skills

    def monsters(self) -> list[dict]:
        return self._monsters

    def pedestals(self) -> list[dict]:
        return self._pedestals

    def combats(self) -> list[dict]:
        return self._combats

