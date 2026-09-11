"""
消息渲染 - 把 API 数据格式化成 QQ 群可读的纯文本
- QQ 群不渲染 markdown，所以全用 emoji 和缩进
- 支持中英双语显示(通过 translations 模块)
- Tooltip 实时翻译(通过 tooltip_translations 模块)
"""
from . import translations, tooltip_translations as tt_trans

# Tier 显示
TIER_ICON = {
    "Bronze": "🟫 青铜",
    "Silver": "⬜ 白银",
    "Gold": "🟨 黄金",
    "Diamond": "💎 钻石",
    "Legendary": "🌟 传奇",
}

# Size 汉化
SIZE_ZH = {
    "Small": "小",
    "Medium": "中",
    "Large": "大",
}

# 英雄名汉化
HERO_ZH = {
    "Pygmalien": "皮格马利翁",
    "Vanessa": "瓦内莎",
    "Dooley": "杜利",
    "Mak": "马克",
    "Jules": "朱尔斯",
    "Karnok": "卡诺克",
    "Stelle": "斯黛尔",
    "The Dragons": "双龙",
    "Hero8": "双龙",
    "Common": "通用",
}

# Tag 汉化
TAG_ZH = {
    "Weapon": "武器",
    "Shield": "护盾",
    "Heal": "治疗",
    "Damage": "伤害",
    "Burn": "燃烧",
    "Freeze": "冻结",
    "Poison": "毒素",
    "Slow": "减速",
    "Haste": "加速",
    "Crit": "暴击",
    "Ammo": "弹药",
    "Tool": "工具",
    "Food": "食物",
    "Toy": "玩具",
    "Apparel": "服装",
    "Property": "房产",
    "Vehicle": "载具",
    "Aquatic": "水系",
    "Dinosaur": "恐龙",
    "Dragon": "龙",
    "Drone": "无人机",
    "Flying": "飞行",
    "Friend": "伙伴",
    "Tech": "科技",
    "Relic": "遗物",
    "Reagent": "试剂",
    "Potion": "药水",
    "Quest": "任务",
    "Economy": "经济",
    "Income": "收入",
    "Gold": "黄金",
    "Loot": "战利品",
    "Experience": "经验",
    "Level": "等级",
    "Health": "生命值",
    "Regen": "回复",
    "Core": "核心",
    "Ray": "射线",
    "Ticket": "票券",
    "Value": "价值",
    "Joy": "喜悦",
    "Charge": "充能",
    "Cooldown": "冷却",
    "Unpurchasable": "不可购买",
    # Reference 标签(隐藏标签)
    "DamageReference": "伤害(关联)",
    "ShieldReference": "护盾(关联)",
    "HealReference": "治疗(关联)",
    "BurnReference": "燃烧(关联)",
    "FreezeReference": "冻结(关联)",
    "PoisonReference": "毒素(关联)",
    "SlowReference": "减速(关联)",
    "HasteReference": "加速(关联)",
    "CritReference": "暴击(关联)",
    "AmmoReference": "弹药(关联)",
    "FlyingReference": "飞行(关联)",
    "TechReference": "科技(关联)",
    "HealthReference": "生命值(关联)",
    "RegenReference": "回复(关联)",
    "CooldownReference": "冷却(关联)",
    "PotionReference": "药水(关联)",
    "AbsorbDestroy": "吸收摧毁",
    "AbsorbFreeze": "吸收冻结",
    "AbsorbSlow": "吸收减速",
}

# 附魔 → emoji
ENCHANT_ICON = {
    "Deadly": "💀",
    "Fiery": "🔥",
    "Golden": "💰",
    "Heavy": "🪨",
    "Icy": "❄️",
    "Obsidian": "⬛",
    "Radiant": "✨",
    "Restorative": "💚",
    "Shielded": "🛡️",
    "Shiny": "💎",
    "Toxic": "☠️",
    "Turbo": "⚡",
    "Mossy": "🌿",
}

ENCHANT_ZH = {
    "Deadly": "致命",
    "Fiery": "炽焰",
    "Golden": "黄金",
    "Heavy": "沉重",
    "Icy": "寒冰",
    "Obsidian": "黑曜石",
    "Radiant": "辉耀",
    "Restorative": "回复",
    "Shielded": "护盾",
    "Shiny": "闪亮",
    "Toxic": "毒素",
    "Turbo": "疾速",
    "Mossy": "长青",
}


def _bilingual_name(name_en: str) -> str:
    """返回名称：优先中文，无翻译时显示英文"""
    zh = translations.get_zh(name_en)
    if zh:
        return zh  # 只显示中文
    return name_en


def format_help() -> str:
    return (
        "🛒 大巴扎 (The Bazaar) 指令\n"
        "━━━━━━━━━━━━━━\n"
        "#bz me <用户名>      查玩家分数/排名/留言\n"
        "#bz stat <用户名>    查本赛季统计(走势+英雄)\n"
        "#bz history <用户名> 最近5天对局记录\n"
        "#bz db <名字>        查卡牌（支持中英文）\n"
        "#bz winrate <卡牌>   查卡牌/组合10胜率\n"
        "─ 群订阅(仅群聊)\n"
        "#bz watch <用户名>   订阅每日 10:00 播报\n"
        "#bz unwatch <用户名> 取消订阅\n"
        "#bz watchlist        本群订阅列表\n"
        "━━━━━━━━━━━━━━\n"
        "示例: #bz watch qifeiovo    #bz db 万剑之王"
    )


def format_player(username: str, data: dict) -> str:
    title = data.get("seasonTitleInfo") or {}
    vip = data.get("vipInfo") or {}
    rating = data.get("currentRating") or {}
    socials = data.get("socialLinks") or []

    title_name = title.get("titleName") or "—"
    message = title.get("messageDecoded") or title.get("message") or ""
    vip_lv = vip.get("vipLevel", 0)

    rating_val = rating.get("rating")
    pos = rating.get("position")
    ts = rating.get("timestamp") or ""

    lines = [f"🏆 {username} · {title_name}"]
    if message:
        lines.append(f"💬 {message}")

    score_line = []
    if rating_val is not None:
        score_line.append(f"分数 {rating_val}")
    if pos is not None:
        score_line.append(f"排名 #{pos}")
    if vip_lv:
        score_line.append(f"VIP Lv.{vip_lv}")
    if score_line:
        lines.append(" · ".join(score_line))
    if ts:
        lines.append(f"📅 {ts}")

    if socials:
        lines.append("")
        lines.append("🔗 社交:")
        for s in socials:
            pid = s.get("platformId") or "?"
            url = s.get("platformUrl") or ""
            lines.append(f"  · {pid}: {url}")
    return "\n".join(lines)


def _tier_lines(tiers: dict, translated_map: dict | None = None) -> list[str]:
    """有 tooltip 的 tier 才输出。translated_map 是预先翻译好的 {tier_text: zh_text}。"""
    lines = []
    for tier in ("Bronze", "Silver", "Gold", "Diamond", "Legendary"):
        info = tiers.get(tier) or {}
        tips = [t for t in (info.get("tooltips") or []) if t]
        if not tips:
            continue
        lines.append(f"[{TIER_ICON.get(tier, tier)}]")
        for t in tips:
            # 优先用翻译
            zh = (translated_map or {}).get(t) if translated_map else None
            # 如果没有翻译，尝试从缓存读取
            if not zh:
                zh = tt_trans.get_translation(t)
            # 如果还是没有，用术语表做简单替换
            if not zh:
                zh = _apply_glossary(t)
            display = zh if zh else t
            lines.append(f"  · {display}")
    return lines


def _apply_glossary(text: str) -> str:
    """用术语表做简单替换，处理常见英文句式"""
    import re
    result = text
    # 先替换多词短语（从长到短，避免被单词替换截断）
    sorted_terms = sorted(tt_trans.GLOSSARY.items(), key=lambda x: -len(x[0]))
    for en, zh in sorted_terms:
        result = result.replace(en, zh)
    # 额外处理时间单位变形
    result = re.sub(r'\bseconds?\b', '秒', result)
    result = re.sub(r'\bitems?\b', '个物品', result)
    # 处理动词变形（减速s → 减速）
    result = re.sub(r'(减速|冻结|燃烧|治疗|护盾|毒素|回复)s\b', r'\1', result)
    return result


def format_item(item: dict, with_all_enchants: bool = False, translated_tooltips: dict | None = None) -> str:
    # 未传入翻译时自动用 tt_trans 逐条翻译
    _auto_translate = translated_tooltips is None
    if translated_tooltips is None:
        translated_tooltips = {}
    name = item.get("name") or "?"
    size = SIZE_ZH.get(item.get("size") or "", item.get("size") or "?")
    starting = TIER_ICON.get(item.get("startingTier") or "", item.get("startingTier") or "?")
    heroes = [translations.get_zh_by_text_key(h) or HERO_ZH.get(h, h) for h in (item.get("heroes") or [])]
    tags = [TAG_ZH.get(t, t) for t in (item.get("tags") or [])]
    hidden = [TAG_ZH.get(t, t) for t in (item.get("hiddenTags") or [])]

    lines = [f"📦 {_bilingual_name(name)}"]
    meta = [f"尺寸: {size}", f"起始品质: {starting}"]
    if heroes:
        meta.append("英雄: " + ", ".join(heroes))
    lines.append(" | ".join(meta))
    if tags:
        lines.append("标签: " + ", ".join(tags))
    if hidden:
        lines.append("隐藏标签: " + ", ".join(hidden))

    lines.append("")
    lines.extend(_tier_lines(item.get("tiers") or {}, translated_tooltips))

    quests = item.get("quests") or []
    if quests:
        lines.append("")
        lines.append("🎯 任务:")
        for q in quests[:3]:
            txt = q.get("description") or q.get("name") or str(q)
            lines.append(f"  · {txt}")

    enchants = item.get("enchantments") or []
    if enchants:
        lines.append("")
        if with_all_enchants:
            lines.append("✨ 附魔 (全部):")
            for e in enchants:
                _add_enchant(lines, e, translated_tooltips)
        else:
            lines.append(f"✨ 附魔 (共 {len(enchants)} 种, 显示前 3):")
            for e in enchants[:3]:
                _add_enchant(lines, e, translated_tooltips)
            lines.append("  → #bz item <名字> +ench 查全部")

    encounters = item.get("combatEncounters") or []
    if encounters:
        lines.append("")
        names = [e.get("cardName") for e in encounters if e.get("cardName")]
        display = [_bilingual_name(n) for n in names[:5]]
        preview = ", ".join(display)
        more = f" 等 {len(names)} 个" if len(names) > 5 else ""
        lines.append(f"👹 出现于: {preview}{more}")

    return "\n".join(lines)


def _add_enchant(lines: list[str], e: dict, translated_tooltips: dict | None = None):
    typ = e.get("type") or "?"
    typ_zh = ENCHANT_ZH.get(typ, typ)
    tips = e.get("tooltips") or []
    if tips:
        t0 = translated_tooltips.get(tips[0], tips[0]) if translated_tooltips else tips[0]
        if t0 == tips[0]:
            t0 = tt_trans.get_translation(tips[0]) or _apply_glossary(tips[0])
        lines.append(f"  {typ_zh}: {t0}")
        for t in tips[1:]:
            t_zh = translated_tooltips.get(t, t) if translated_tooltips else t
            if t_zh == t:
                t_zh = tt_trans.get_translation(t) or _apply_glossary(t)
            lines.append(f"        {t_zh}")
    else:
        lines.append(f"  {typ_zh}")


def format_skill(skill: dict, translated_tooltips: dict | None = None) -> str:
    # 未传入翻译时自动用术语表
    _auto_translate = translated_tooltips is None
    if translated_tooltips is None:
        translated_tooltips = {}
    name = skill.get("name") or "?"
    size = SIZE_ZH.get(skill.get("size") or "", skill.get("size") or "?")
    starting = TIER_ICON.get(skill.get("startingTier") or "", skill.get("startingTier") or "?")
    heroes = [translations.get_zh_by_text_key(h) or HERO_ZH.get(h, h) for h in (skill.get("heroes") or [])]
    tags = [TAG_ZH.get(t, t) for t in (skill.get("tags") or [])]
    hidden = [TAG_ZH.get(t, t) for t in (skill.get("hiddenTags") or [])]

    lines = [f"📘 技能 · {_bilingual_name(name)}"]
    meta = [f"尺寸: {size}", f"起始品质: {starting}"]
    if heroes:
        meta.append("英雄: " + ", ".join(heroes))
    lines.append(" | ".join(meta))
    if tags:
        lines.append("标签: " + ", ".join(tags))
    if hidden:
        lines.append("隐藏标签: " + ", ".join(hidden))
    lines.append("")
    lines.extend(_tier_lines(skill.get("tiers") or {}, translated_tooltips))
    return "\n".join(lines)


def format_merchant(npc: dict) -> str:
    name = npc.get("name") or "?"
    desc = npc.get("description") or ""
    heroes = [translations.get_zh_by_text_key(h) or HERO_ZH.get(h, h) for h in (npc.get("heroes") or [])]
    filters = npc.get("filters") or {}

    lines = [f"🏪 商人 · {name}"]
    if heroes:
        lines.append("英雄: " + ", ".join(heroes))
    if desc:
        lines.append(f"简介: {desc}")
    tag_states = filters.get("tagStates") or {}
    if tag_states:
        on_tags = [TAG_ZH.get(k, k) for k, v in tag_states.items() if v == "on"]
        if on_tags:
            lines.append("出售: " + ", ".join(on_tags))
    return "\n".join(lines)


def format_day(day_data: dict) -> str:
    day = day_data.get("day")
    groups = day_data.get("groups") or []

    lines = [f"📅 Day {day} · 共 {len(groups)} 组 encounter"]
    lines.append("━━━━━━━━━━━━━━")
    for idx, group in enumerate(groups, 1):
        names = []
        hps = []
        for card in group:
            cn = card.get("cardName") or "?"
            hp = card.get("health")
            names.append(_bilingual_name(cn))
            if hp:
                hps.append(str(hp))
        line = f"{idx}. " + " + ".join(names)
        if hps:
            line += f"  (HP: {' / '.join(hps)})"
        lines.append(line)
    lines.append("")
    lines.append("详情用 #bz boss <名字>")
    return "\n".join(lines)


def format_boss(card: dict) -> str:
    name = card.get("cardName") or "?"
    hp = card.get("health")
    day = card.get("_day")
    items = card.get("items") or []
    skills = card.get("skills") or []

    lines = [f"👹 {_bilingual_name(name)}"]
    meta = []
    if hp:
        meta.append(f"HP {hp}")
    if day is not None:
        meta.append(f"Day {day}")
    if meta:
        lines.append(" · ".join(meta))

    if items:
        lines.append("")
        lines.append(f"📦 物品 ({len(items)}):")
        for it in items[:15]:
            card_obj = it.get("card") or {}
            nm = card_obj.get("name") or "?"
            tier = it.get("tierType") or it.get("tier") or ""
            tier_s = f" [{tier}]" if tier else ""
            ench = it.get("enchantmentType")
            ench_s = f" + {ENCHANT_ICON.get(ench, '')}{ENCHANT_ZH.get(ench, ench)}" if ench else ""
            lines.append(f"  · {_bilingual_name(nm)}{tier_s}{ench_s}")
        if len(items) > 15:
            lines.append(f"  ... 还有 {len(items) - 15} 个")

    if skills:
        lines.append("")
        lines.append(f"📘 技能 ({len(skills)}):")
        for sk in skills[:10]:
            card_obj = sk.get("card") or {}
            nm = card_obj.get("name") or "?"
            tier = sk.get("tierType") or sk.get("tier") or ""
            tier_s = f" [{tier}]" if tier else ""
            lines.append(f"  · {_bilingual_name(nm)}{tier_s}")
        if len(skills) > 10:
            lines.append(f"  ... 还有 {len(skills) - 10} 个")

    return "\n".join(lines)


def format_candidates(candidates: list[dict], kind_label: str, name_field: str = "name") -> str:
    lines = [f"找到多个{kind_label},请加更具体的名字(前 {len(candidates)} 条):"]
    for c in candidates:
        nm = c.get(name_field) or "?"
        extra = []
        if c.get("startingTier"):
            extra.append(c["startingTier"])
        if c.get("size"):
            extra.append(c["size"])
        ex = f" [{' '.join(extra)}]" if extra else ""
        lines.append(f"  · {_bilingual_name(nm)}{ex}")
    return "\n".join(lines)


def format_search(query: str, items: list[dict], skills: list[dict]) -> str:
    lines = [f"🔍 搜索 '{query}' 命中 {len(items) + len(skills)} 条"]
    if items:
        lines.append("")
        lines.append(f"📦 物品 ({len(items)}):")
        for it in items[:8]:
            lines.append(f"  · {_bilingual_name(it.get('name'))}")
        if len(items) > 8:
            lines.append(f"  ... 还有 {len(items) - 8}")
    if skills:
        lines.append("")
        lines.append(f"📘 技能 ({len(skills)}):")
        for sk in skills[:8]:
            lines.append(f"  · {_bilingual_name(sk.get('name'))}")
        if len(skills) > 8:
            lines.append(f"  ... 还有 {len(skills) - 8}")
    if not items and not skills:
        lines.append("没找到匹配项,换个关键词?")
    return "\n".join(lines)


def format_status(s: dict) -> str:
    import time as _t
    last = s.get("last_check") or 0
    last_str = _t.strftime("%Y-%m-%d %H:%M:%S", _t.localtime(last)) if last else "未刷新"
    lines = ["🛒 BazaarPlugin 状态"]
    lines.append(f"items: {s.get('items')}  skills: {s.get('skills')}  merchants: {s.get('merchants')}  days: {s.get('encounter_days')}")
    versions = s.get("versions") or {}
    for k, v in versions.items():
        lines.append(f"  {k}: {v}")
    lines.append(f"上次校验: {last_str}")
    return "\n".join(lines)


def format_player_stat(username: str, data: dict) -> str:
    """comprehensive-info 的统计输出。"""
    from datetime import datetime

    rh = data.get("ratingHistory") or []
    if not rh:
        return f"📊 {username} · 本赛季无记录"

    title = data.get("seasonTitleInfo") or {}
    ci = data.get("classInfo") or {}
    socials = data.get("socialLinks") or []

    title_name = title.get("titleName") or ""
    message = title.get("message") or ""

    # 当前/起点/峰值/谷底
    cur = rh[-1]
    start = rh[0]
    peak = max(rh, key=lambda r: r["rating"])
    best_pos = min(rh, key=lambda r: r["position"])

    cur_r = cur["rating"]
    cur_pos = cur["position"]
    start_r = start["rating"]
    peak_r = peak["rating"]
    peak_pos = peak["position"]
    best_p = best_pos["position"]

    # 涨跌
    def delta_since(hours):
        now_t = datetime.strptime(cur["timestamp"], "%Y-%m-%d %H:%M:%S")
        target_ts = now_t.timestamp() - hours * 3600
        prior = None
        for r in rh:
            rt = datetime.strptime(r["timestamp"], "%Y-%m-%d %H:%M:%S")
            if rt.timestamp() <= target_ts:
                prior = r
            else:
                break
        if not prior:
            return None
        return cur_r - prior["rating"]

    d24 = delta_since(24)
    d7d = delta_since(24 * 7)

    lines = [f"📊 {username} 本赛季统计"]
    if title_name:
        lines.append(f"称号: {title_name} · {message}")

    lines.append("")
    lines.append(f"当前: {cur_r} 分 · 排名 #{cur_pos}")
    lines.append(f"峰值: {peak_r} 分 (排名 #{peak_pos})")
    lines.append(f"最佳排名: #{best_p}")
    lines.append(f"总涨跌: {cur_r - start_r:+d} (起点 {start_r})")

    if d24 is not None or d7d is not None:
        lines.append("")
        if d24 is not None:
            lines.append(f"24h: {d24:+d}")
        if d7d is not None:
            lines.append(f"7d: {d7d:+d}")

    # 英雄统计
    main_cls = ci.get("mainClass") or "?"
    counts = ci.get("classCounts") or {}
    total = sum(counts.values())
    if total > 0:
        lines.append("")
        lines.append(f"对局: {total} 场 · 主玩 {_class_name(main_cls)}")
        # 显示玩过的英雄(非零)
        played = [(k, v) for k, v in counts.items() if v > 0]
        played.sort(key=lambda x: x[1], reverse=True)
        cls_line = []
        for k, v in played:
            pct = int(v * 100 / total)
            cls_line.append(f"{_class_name(k)} {v}场({pct}%)")
        lines.append("  " + " | ".join(cls_line))

    if socials:
        lines.append("")
        lines.append("🔗 " + " · ".join(s.get("platformId", "?") for s in socials[:3]))

    # 时间跨度
    start_ts = start.get("timestamp") or ""
    cur_ts = cur.get("timestamp") or ""
    if start_ts and cur_ts:
        lines.append("")
        lines.append(f"数据时间: {start_ts[:10]} ~ {cur_ts[:10]}")

    return "\n".join(lines)


def _class_name(code: str) -> str:
    """英雄代号 → 中文显示名。"""
    names = {
        "p": "皮格马利翁",
        "s": "斯黛尔",
        "d": "杜利",
        "v": "瓦内莎",
        "j": "朱尔斯",
        "m": "马克",
    }
    return names.get(code.lower(), code.upper())


def format_daily_diff(username: str, data: dict) -> str:
    """每日播报单个玩家块。只显示 24h 变化。"""
    from datetime import datetime

    rh = data.get("ratingHistory") or []
    if not rh:
        return f"{username}: 本赛季无记录"

    cur = rh[-1]
    cur_r = cur["rating"]
    cur_pos = cur["position"]

    # 24h 前
    def delta_since(hours):
        now_t = datetime.strptime(cur["timestamp"], "%Y-%m-%d %H:%M:%S")
        target_ts = now_t.timestamp() - hours * 3600
        prior = None
        for r in rh:
            rt = datetime.strptime(r["timestamp"], "%Y-%m-%d %H:%M:%S")
            if rt.timestamp() <= target_ts:
                prior = r
            else:
                break
        return prior

    p24 = delta_since(24)
    if not p24:
        return f"{username}: {cur_r} 分 · 排名 #{cur_pos} (24h 数据不足)"

    d_r = cur_r - p24["rating"]
    d_p = cur_pos - p24["position"]  # 负数=排名上升

    # 涨跌符号
    r_sign = "+" if d_r > 0 else ""
    p_arrow = "↑" if d_p < 0 else ("↓" if d_p > 0 else "→")

    lines = [f"{username}"]
    lines.append(f"  分数: {p24['rating']} → {cur_r} ({r_sign}{d_r})")
    lines.append(f"  排名: #{p24['position']} → #{cur_pos} ({p_arrow}{abs(d_p)})")

    return "\n".join(lines)


# ===== Boss / 怪物格式化 =====
def format_monster(monster: dict) -> str:
    """格式化 Boss/怪物信息"""
    name = monster.get("name", "Unknown")
    health = monster.get("health", 0)
    level = monster.get("level", 0)
    prestige = monster.get("prestige", 0)
    items = monster.get("items", [])

    lines = [f"🐉 {name}"]
    lines.append(f"等级: {level} | 血量: {health} | 威望: {prestige}")
    
    if items:
        lines.append(f"\n手牌 ({len(items)} 张):")
        for i, item in enumerate(items, 1):
            item_name_en = item.get("name", "?")
            # 尝试翻译物品名
            item_name_zh = translations.get_zh(item_name_en) or item_name_en
            tier = TIER_ICON.get(item.get("tier", ""), item.get("tier", ""))
            ench = item.get("enchantment")
            ench_zh = ENCHANT_ZH.get(ench, ench) if ench else None
            ench_str = f" [{ench_zh}]" if ench_zh else ""
            lines.append(f"  {i}. {tier} {item_name_zh}{ench_str}")
    else:
        lines.append("\n手牌: 无")
    
    return "\n".join(lines)


# ===== 附魔台格式化 =====
def format_pedestal(pedestal: dict) -> str:
    """格式化附魔台信息"""
    name = pedestal.get("name", "Unknown")
    internal_name = pedestal.get("internal_name", "")
    tier = TIER_ICON.get(pedestal.get("tier", ""), pedestal.get("tier", ""))
    heroes = [translations.get_zh_by_text_key(h) or HERO_ZH.get(h, h) for h in pedestal.get("heroes", [])]
    desc = pedestal.get("description", "")
    
    lines = [f"✨ {name} ({internal_name})"]
    lines.append(f"品质: {tier} | 英雄: {', '.join(heroes) if heroes else '通用'}")
    
    if desc:
        lines.append(f"\n效果:\n  {desc}")
    
    return "\n".join(lines)


# ===== 战斗遭遇格式化 =====
def format_combat(combat: dict, monster: dict | None = None) -> str:
    """格式化战斗遭遇信息"""
    name = combat.get("name", "Unknown")
    internal_name = combat.get("internal_name", "")
    tier = TIER_ICON.get(combat.get("tier", ""), combat.get("tier", ""))
    level = combat.get("level", 0)
    gold = combat.get("gold", 0)
    xp = combat.get("xp", 0)
    sandstorm = "是" if combat.get("sandstorm") else "否"
    
    lines = [f"⚔️ {name} ({internal_name})"]
    lines.append(f"品质: {tier} | 等级: {level} | 沙尘暴: {sandstorm}")
    lines.append(f"奖励: 💰 {gold} 金币, 📈 {xp} 经验")
    
    if monster:
        lines.append(f"\n对手: {monster.get('name', '?')} (HP {monster.get('health', 0)})")
    
    return "\n".join(lines)


def format_winrate(result: dict, hero: str = None, days: int = None, rank_filter: str = "all") -> str:
    """格式化 #bz winrate 查询结果"""
    not_found = result.get('not_found', [])
    card_names = result.get('card_names', [])
    total = result.get('total', 0)
    ten_win = result.get('ten_win', 0)
    rate = result.get('rate', 0.0)

    if not_found:
        return f"❌ 找不到卡牌: {', '.join(not_found)}"

    if not card_names:
        return "❌ 未找到匹配的卡牌"

    card_str = " + ".join(card_names)
    lines = [f"📊 {card_str} 胜率统计"]

    conds = []
    if hero:
        conds.append(f"英雄: {hero}")
    if days:
        conds.append(f"最近 {days} 天")
    if rank_filter == "legendary":
        conds.append("段位: 传奇")
    if conds:
        lines.append("筛选: " + " | ".join(conds))

    lines.append("━━━━━━━━━━━━━━")

    if total == 0:
        lines.append("暂无数据（样本不足）")
        return "\n".join(lines)

    bar_filled = int(rate * 10)
    bar = "█" * bar_filled + "░" * (10 - bar_filled)
    lines.append(f"10胜率: {rate:.1%}  [{bar}]")
    lines.append(f"10胜局: {ten_win} / {total} 局")

    return "\n".join(lines)
