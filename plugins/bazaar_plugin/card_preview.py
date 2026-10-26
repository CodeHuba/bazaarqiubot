"""Read-only, versioned encyclopedia previews; never reconstruct a run's live state."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
from contextlib import closing
from collections import OrderedDict
from pathlib import Path

from . import gamedata_client as game

QUALITIES = ("Bronze", "Silver", "Gold", "Diamond", "Legendary")
QUALITY_LABELS = dict(zip(QUALITIES, ("青铜", "白银", "黄金", "钻石", "传奇")))
ROOT = Path(__file__).resolve().parents[2]
PARSER_VERSION = "card-preview-3"


def _fingerprint(path: Path | None) -> str:
    if path is None:
        return "missing"
    try:
        stat = path.stat()
        return f"{path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}"
    except OSError:
        return f"{path}:missing"


def _default_database() -> Path:
    configured = os.getenv("GAMEDATA_DB", "").strip()
    if configured:
        return Path(configured).expanduser()
    runtime = Path.home() / "AppData/LocalLow/Tempo Storm/The Bazaar/prod/cache/GameData.db"
    candidates = ((runtime,) if os.name == "nt" else ()) + (ROOT / "plugins/bazaar_plugin/cache/GameData.db", ROOT / "cache/GameData.db")
    return next((p for p in candidates if p.is_file() and p.stat().st_size), candidates[0])


def _default_translation(database: Path) -> Path:
    configured = os.getenv("ZH_TRANSLATIONS_DB", "").strip()
    if configured:
        return Path(configured).expanduser()
    candidates = (
        database.parent / "translations/zh-CN.bytes",
        database.parent / "zh-CN.bytes",
        ROOT / "plugins/bazaar_plugin/cache/zh-CN.bytes",
        ROOT / "AppData/LocalLow/Tempo Storm/The Bazaar/prod/cache/translations/zh-CN.bytes",
        Path.home() / "AppData/LocalLow/Tempo Storm/The Bazaar/prod/cache/translations/zh-CN.bytes",
        ROOT / "plugins/bazaar_plugin/cache/zh-CN.json",
    )
    return next((p for p in candidates if p.is_file()), candidates[0])


def _plain(text: str) -> str:
    # Game markup is text, never browser HTML. Keep readable line breaks and strip TMP tags.
    text = str(text).replace(r"\r\n", "\n").replace(r"\n", "\n").replace(r"\r", "\n")
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    return re.sub(r"</?[A-Za-z][^>]*>", "", text).strip()


def _apply_self_auras(attrs: dict, auras: dict) -> tuple[dict, list[str]]:
    """Evaluate unconditional self modifiers once in dependency order; leave all other context unknown."""
    result = dict(attrs)
    pending = []
    unknown = set()
    for aura in auras.values():
        action = aura.get("Action") or {}
        target = action.get("Target") or {}
        if action.get("$type") != "TAuraActionCardModifyAttribute" or target.get("$type") != "TTargetCardSelf":
            continue
        attribute = action.get("AttributeType")
        if not attribute:
            continue
        if (aura.get("Prerequisites") or target.get("Conditions")
                or aura.get("WorksIn") not in (None, "Anywhere")
                or aura.get("ActiveIn") not in (None, "HandOnly", "HandAndStash")
                or game._preview_requires_context(action)):
            unknown.add(attribute)
            continue
        pending.append((attribute, action))
    while pending:
        progressed = False
        for attribute, action in list(pending):
            value = action.get("Value") or {}
            dependencies = set()
            def references(node):
                if isinstance(node, dict):
                    if node.get("$type") == "TReferenceValueCardAttribute":
                        dependencies.add(node.get("AttributeType"))
                    for child in node.values():
                        references(child)
                elif isinstance(node, list):
                    for child in node:
                        references(child)
            references(value)
            if (dependencies - {attribute}) & {key for key, _ in pending}:
                continue
            if dependencies & unknown:
                unknown.add(attribute)
            else:
                amount, _ = game._resolve_value_object(value, result)
                original = result.get(attribute, 0)
                operation = action.get("Operation")
                if amount is None or operation not in {"Add", "Subtract", "Multiply", "Divide", "Set"}:
                    unknown.add(attribute)
                else:
                    try:
                        calculated = {"Add": lambda: original + amount, "Subtract": lambda: original - amount,
                                      "Multiply": lambda: original * amount, "Divide": lambda: original / amount,
                                      "Set": lambda: amount}[operation]()
                        result[attribute] = game._round_native(float(calculated))
                    except (TypeError, ValueError, ZeroDivisionError):
                        unknown.add(attribute)
            pending.remove((attribute, action))
            progressed = True
        if not progressed:
            unknown.update(key for key, _ in pending)
            break
    for key in unknown:
        result.pop(key, None)
    return result, sorted(unknown)


def _effect(tip, source, abilities, auras, attrs, language, translate):
    template = translate(tip.get("Content") or {}, language)
    if not template:
        return None
    text = game.render_tooltip(template, abilities, auras, attrs, strict_static=True)
    dynamic = bool(re.search(r"\{[^{}]+\}", text))
    text = re.sub(r"\{[^{}]+\}", "〔动态数值〕", _plain(text))
    condition = tip.get("TooltipCondition")
    conditions = {"Chilled": "冰冻时", "Heated": "灼热时", "Enraged": "狂怒时"}
    condition_text = conditions.get(condition, condition or "") if condition not in (None, "None") else ""
    if tip.get("Prerequisites"):
        condition_text = (condition_text + " · " if condition_text else "") + "满足条件时生效"
    return {"text": text, "type": tip.get("TooltipType") or "Passive", "source": source,
            "condition": condition_text, "dynamic": dynamic}


def build_preview(raw: dict, quality: str, enchantment: str, language: str, translate, image: dict, version: str) -> dict:
    tiers = raw.get("Tiers") or {}
    available = [name for name in QUALITIES if name in tiers]
    if not available:
        raise LookupError("该卡牌没有可展示的品质数据")
    quality = quality or (raw.get("StartingTier") if raw.get("StartingTier") in available else available[0])
    if quality not in available:
        raise ValueError("该卡牌不存在所选品质")
    enchants = raw.get("Enchantments") or {}
    if enchantment and enchantment not in enchants:
        raise ValueError("该卡牌不支持所选附魔")
    enchant = enchants.get(enchantment) or {}
    attrs = game.build_tier_attrs(raw, quality)
    attrs.update(enchant.get("Attributes") or {})
    tier = tiers[quality]
    abilities = {**(raw.get("Abilities") or {}), **(enchant.get("Abilities") or {})}
    base_auras = raw.get("Auras") or {}
    active_ids = tier.get("AuraIds")
    auras = {key: value for key, value in base_auras.items() if active_ids is None or key in active_ids}
    auras.update(enchant.get("Auras") or {})
    attrs, unknown_attrs = _apply_self_auras(attrs, auras)
    # Resolve tooltip tokens against all definitions, but only apply active auras above.
    all_auras = {**base_auras, **(enchant.get("Auras") or {})}
    effects = []
    tooltips = (raw.get("Localization") or {}).get("Tooltips") or []
    ids = tier.get("TooltipIds")
    selected = tooltips if ids is None else [tooltips[i] for i in ids if isinstance(i, int) and 0 <= i < len(tooltips)]
    selected = [(tip, "base") for tip in selected] + [(tip, "enchantment") for tip in (enchant.get("Localization") or {}).get("Tooltips", [])]
    for tip, source in selected:
        # Missing attributes affected by contextual auras must not resolve to their default zero.
        blocked_abilities = dict(abilities)
        blocked_auras = dict(all_auras)
        if unknown_attrs:
            for definitions in (blocked_abilities, blocked_auras):
                for key, definition in list(definitions.items()):
                    action = definition.get("Action") or {}
                    attribute = game.ACTION_ATTR_MAP.get(action.get("$type"))
                    if attribute in unknown_attrs or any(f'"{name}"' in json.dumps(action) for name in unknown_attrs):
                        definitions[key] = {**definition, "Action": {"$type": "TReferenceValuePreviewContext"}}
        effect = _effect(tip, source, blocked_abilities, blocked_auras, attrs, language, translate)
        if effect:
            effects.append(effect)
    quest_index = 0
    for group in raw.get("Quests") or []:
        for entry in group.get("Entries") or []:
            quest_index += 1
            goal_tips = (entry.get("Localization") or {}).get("Tooltips") or []
            if not goal_tips:
                effects.append({"text": "此任务的具体目标请以游戏内说明为准。", "type": "Passive", "source": "quest",
                                "condition": f"任务 {quest_index} 目标", "dynamic": True})
            reward = entry.get("Reward") or {}
            reward_attrs = {**attrs, **game._get_quest_reward_attributes(reward, quality)}
            reward_abilities = {**abilities, **(reward.get("Abilities") or {})}
            reward_auras = {**all_auras, **(reward.get("Auras") or {})}
            for label, tips, effect_attrs in (
                ("目标", goal_tips, attrs),
                ("奖励", (reward.get("Localization") or {}).get("Tooltips") or [], reward_attrs),
            ):
                for tip in tips:
                    effect = _effect(tip, "quest", reward_abilities if label == "奖励" else abilities,
                                     reward_auras if label == "奖励" else all_auras,
                                     effect_attrs, language, translate)
                    if effect:
                        effect["condition"] = f"任务 {quest_index} {label}" + (" · 满足条件时生效" if entry.get("Prerequisites") else "")
                        effects.append(effect)
    loc = raw.get("Localization") or {}
    name_en = (loc.get("Title") or {}).get("Text") or raw.get("InternalName") or raw.get("Id")
    name = _plain(translate(loc.get("Title") or {"Text": name_en}, language))
    def quality_label(value):
        translated = translate({"Text": value}, language)
        return QUALITY_LABELS[value] if translated == value and language == "zh-CN" else translated
    tags = list(dict.fromkeys([*(raw.get("Tags") or []), *(enchant.get("Tags") or [])]))
    hidden_tags = list(dict.fromkeys([*(raw.get('HiddenTags') or []), *(enchant.get('HiddenTags') or [])]))
    attributes = []
    labels = {"CooldownMax": "冷却", "AmmoMax": "弹药", "Multicast": "多重施放", "DamageAmount": "伤害",
              "ShieldApplyAmount": "护盾", "HealAmount": "治疗", "BurnApplyAmount": "灼烧", "PoisonApplyAmount": "剧毒",
              "CritChance": "暴击率"}
    for key, label in labels.items():
        value = attrs.get(key)
        if value is None or value == 0 or (key == "Multicast" and value <= 1):
            continue
        attributes.append({"key": key, "label": label, "value": game.ms_to_s(value) + "秒" if key == "CooldownMax" else game.fmt_val(value) + ("%" if key == "CritChance" else "")})
    return {"cardId": raw.get("Id"), "name": name, "name_en": name_en, "type": raw.get("Type"),
            "size": raw.get("Size") or "Small", "img": image.get("artLarge") or image.get("art") or "",
            "quality": quality, "qualityLabel": quality_label(quality), "enchantment": enchantment,
            "availableQualities": [{"value": q, "label": quality_label(q)} for q in available],
            "availableEnchantments": [{"value": e, "label": translate({"Text": e}, language)} for e in enchants],
            "tags": [translate({"Key": tag, "Text": tag}, language) for tag in tags],
            "hiddenTags": [game.localize_card_tag(tag, lambda text: translate({'Key': text, 'Text': text}, language), language)
                           for tag in hidden_tags],
            "attributes": attributes, "effects": effects, "version": version, "cardVersion": str(raw.get("Version") or ""),
            "mode": "catalog", "hasDynamicValues": bool(unknown_attrs) or any(e["dynamic"] for e in effects)}


class CardPreviewRepository:
    def __init__(self, database=None, translations=None, images=None):
        self.database = Path(database) if database else _default_database()
        self.translations = Path(translations) if translations else _default_translation(self.database)
        self.images = Path(images) if images else ROOT / "plugins/bazaar_plugin/cache/card_images.json"
        self._lock = threading.RLock()
        self._stamp = ""
        self._cache = OrderedDict()
        self._localized = {}
        self._images = {}

    def _refresh(self):
        if not self.database.is_file() or self.database.stat().st_size == 0:
            raise OSError("卡牌数据暂不可用")
        stamp = "|".join(map(_fingerprint, (self.database, self.translations, self.images))) + PARSER_VERSION
        if stamp == self._stamp:
            return
        localized = {}
        if self.translations.is_file():
            if self.translations.suffix == ".json":
                localized = json.loads(self.translations.read_text(encoding="utf-8"))
            else:
                with closing(sqlite3.connect(self.translations.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
                    localized = dict(conn.execute("SELECT hash, text FROM translation"))
        images = json.loads(self.images.read_text(encoding="utf-8")).get("cards", {}) if self.images.is_file() else {}
        self._localized, self._images = localized, images
        self._cache.clear()
        self._stamp = stamp

    def _translate(self, content, language):
        original = content.get("Text") or ""
        if language != "zh-CN":
            return original
        # LocalizableText("Aquatic"), enchantment names and other shared labels use MD5(UTF-8).
        return self._localized.get(content.get("Key")) or self._localized.get(hashlib.md5(original.encode()).hexdigest(), original)

    def get(self, card_id, quality="", enchantment="", language="zh-CN"):
        if language not in ("zh-CN", "en-US"):
            raise ValueError("不支持该语言")
        with self._lock:
            self._refresh()
            key = (card_id, quality, enchantment, language)
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
            with closing(sqlite3.connect(self.database.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
                row = conn.execute("SELECT Data FROM cards WHERE Id = ?", (card_id,)).fetchone()
            if row is None:
                raise LookupError("未找到该卡牌")
            raw = json.loads(row[0])
            if raw.get("Type") not in ("Item", "Skill"):
                raise LookupError("该类型暂不支持卡牌预览")
            version = hashlib.sha256(self._stamp.encode()).hexdigest()[:16]
            result = build_preview(raw, quality, enchantment, language, self._translate, self._images.get(card_id) or {}, version)
            self._cache[key] = result
            if len(self._cache) > 512:
                self._cache.popitem(last=False)
            return result
