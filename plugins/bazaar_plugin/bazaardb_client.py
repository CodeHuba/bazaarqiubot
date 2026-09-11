"""
bazaardb.gg API 客户端
curl_cffi 模拟 Chrome TLS 指纹绕过 Cloudflare，带限速+双层缓存
"""
import json, random, time
from pathlib import Path
from typing import Optional
from urllib.parse import quote

try:
    from curl_cffi import requests as cf_requests
    HAS_CURL_CFFI = True
except ImportError:
    import requests as cf_requests
    HAS_CURL_CFFI = False

CACHE_DIR = Path(__file__).resolve().parent / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

BASE = "https://bazaardb.gg"

_UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]
_IMPERSONATE_POOL = ["chrome131", "chrome124", "chrome120", "chrome110", "chrome104"]

_MIN_INTERVAL = 1.5
_last_req_time: float = 0.0
_mem_cache: dict[str, tuple[float, object]] = {}

CARD_CACHE_TTL    = 86400   # 1天
SEARCH_CACHE_TTL  = 600     # 10分钟
PROFILE_CACHE_TTL = 300     # 5分钟


def _get_session():
    if not HAS_CURL_CFFI:
        return cf_requests.Session()
    return cf_requests.Session(impersonate=random.choice(_IMPERSONATE_POOL))


def _headers(referer: str = BASE + "/run") -> dict:
    return {
        "Accept": "application/json, */*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": referer,
        "Origin": BASE,
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "User-Agent": random.choice(_UA_POOL),
    }


def _rate_limit():
    global _last_req_time
    wait = _MIN_INTERVAL - (time.time() - _last_req_time) + random.uniform(0.1, 0.9)
    if wait > 0:
        time.sleep(wait)
    _last_req_time = time.time()


def _mem_get(key: str, ttl: float) -> Optional[object]:
    if key in _mem_cache:
        ts, data = _mem_cache[key]
        if time.time() - ts < ttl:
            return data
    return None


def _mem_set(key: str, data: object):
    _mem_cache[key] = (time.time(), data)


def _disk_path(key: str) -> Path:
    safe = key.replace("/", "_").replace("?", "_").replace("&", "_").replace("=", "_")
    return CACHE_DIR / f"bazaardb_{safe}.json"


def _disk_get(key: str, ttl: float) -> Optional[object]:
    p = _disk_path(key)
    if p.exists():
        try:
            if time.time() - p.stat().st_mtime < ttl:
                return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return None


def _disk_set(key: str, data: object):
    try:
        _disk_path(key).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _sync_get(path: str, referer: str = BASE + "/run") -> Optional[object]:
    _rate_limit()
    session = _get_session()
    try:
        r = session.get(BASE + path, headers=_headers(referer), timeout=20)
        if r.status_code == 200 and "json" in r.headers.get("content-type", ""):
            return r.json()
        return None
    except Exception as e:
        print(f"[bazaardb] GET {path} 失败: {e}")
        return None
    finally:
        try:
            session.close()
        except Exception:
            pass


# ── 公开 API ──────────────────────────────────────────────────────────────────

def search_cards(query: str, kind: str = "item") -> list[dict]:
    """搜索卡牌。kind: 'item' | 'skill'"""
    key = f"search_{kind}_{query}"
    cached = _mem_get(key, SEARCH_CACHE_TTL)
    if cached is not None:
        return cached
    data = _sync_get(f"/api/search?q={quote(query)}&c={kind}")
    result = data if isinstance(data, list) else []
    _mem_set(key, result)
    return result


def _register_community_translation(card: dict):
    """从 card 注册社区翻译（bazaardb 返回的中文名）"""
    try:
        from . import translations as _trans
        en = card.get("_originalTitleText", "")
        zh = (card.get("Title") or {}).get("Text", "")
        if en and zh and _trans.has_chinese(zh):
            _trans.register_community(en, zh)
    except Exception:
        pass


def get_card(url_id: str) -> Optional[dict]:
    """按 urlId 获取卡牌完整数据，磁盘缓存1天。"""
    key = f"card_{url_id}"
    cached = _mem_get(key, CARD_CACHE_TTL)
    if cached is not None:
        _register_community_translation(cached)
        return cached
    cached = _disk_get(key, CARD_CACHE_TTL)
    if cached is not None:
        _mem_set(key, cached)
        _register_community_translation(cached)
        return cached
    data = _sync_get(f"/api/card/{url_id}")
    if isinstance(data, dict) and "Id" in data:
        _mem_set(key, data)
        _disk_set(key, data)
        _register_community_translation(data)
        return data
    return None


def card_link(en_name: str) -> Optional[dict]:
    """按精确英文名查链接，返回 {href, title}。"""
    key = f"cardlink_{en_name}"
    cached = _mem_get(key, CARD_CACHE_TTL)
    if cached is not None:
        return cached
    data = _sync_get(f"/api/card-link?id={quote(en_name)}")
    if isinstance(data, dict) and "href" in data:
        _mem_set(key, data)
        return data
    return None


def search_profile(query: str) -> list[dict]:
    """搜索玩家 profile，返回 [{id, nickname, verified}, ...]。"""
    key = f"profile_{query}"
    cached = _mem_get(key, PROFILE_CACHE_TTL)
    if cached is not None:
        return cached
    data = _sync_get(f"/api/profile-search?q={quote(query)}")
    result = data if isinstance(data, list) else []
    _mem_set(key, result)
    return result


# ── 高层查询（供插件/tool 调用）──────────────────────────────────────────────

def query_card_by_name(name: str) -> Optional[dict]:
    """
    按名字（中/英文）查卡牌，返回完整 card dict。
    流程: 先 card-link 精确匹配 → 搜 item → 搜 skill
    """
    try:
        from . import translations
        if translations.has_chinese(name):
            candidates = translations.search_zh(name, limit=3)
            if candidates:
                name = candidates[0]
    except Exception:
        pass

    # 精确链接
    link = card_link(name)
    if link:
        url_id = link["href"].split("/")[2]
        return get_card(url_id)

    # 模糊搜索
    for kind in ("item", "skill"):
        results = search_cards(name, kind=kind)
        if results:
            return get_card(results[0]["urlId"])

    return None





def _resolve_tooltip_text(tooltips: list, replacements: dict, tier_name: str) -> list:
    """解析一组 tooltip，替换占位符，过滤隐藏类型，返回文字列表。"""
    from . import translations as trans
    HIDDEN_TYPES = {"bzdbgg.HiddenSearchable"}
    result = []
    for tip in tooltips:
        if tip.get("TooltipType") in HIDDEN_TYPES:
            continue
        content_obj = tip.get("Content", {})
        text = content_obj.get("Text", "") or ""
        if not text:
            continue
        # bazaardb 返回的 Content 没有 Key，用 tooltip_en_to_zh 映射查中文
        content = trans.get_tooltip_zh(text) or text
        if not content:
            continue
        for placeholder, tier_vals in replacements.items():
            if isinstance(tier_vals, dict):
                val = tier_vals.get(tier_name) or tier_vals.get("Fixed")
            else:
                val = tier_vals
            if val is not None:
                content = content.replace(placeholder, str(val))
        result.append(content)
    return result


def format_card_brief(card: dict, zh_name: str = "", show_enchants: bool = False) -> str:
    """把 card dict 格式化为 QQ 消息文本。"""
    from . import translations as trans

    TIER_ZH = {"Bronze": "铜", "Silver": "银", "Gold": "金", "Diamond": "钻", "Legendary": "传说"}
    HERO_ZH = {"Common": "通用", "Vanessa": "海盗", "Dooley": "工程师",
               "Mak": "法师", "Pygmalien": "猪", "Stelle": "机甲",
               "Jules": "吸血鬼", "Karnok": "兽人", "The Dragons": "双龙"}
    TAG_ZH  = {"Weapon": "武器", "Relic": "遗物", "Tool": "工具", "Aquatic": "水系",
               "Food": "食物", "Property": "房产", "Friend": "同伴", "Vehicle": "载具",
               "Damage": "伤害", "Shield": "护盾", "Heal": "治疗", "Poison": "毒",
               "Burn": "灼烧", "Slow": "减速", "Freeze": "冻结", "Haste": "加速", "Instrument": "乐器"}
    ENC_ZH  = {"Golden": "黄金", "Heavy": "沉重", "Icy": "寒冰", "Turbo": "疾速",
               "Shielded": "护盾", "Restorative": "回复", "Toxic": "毒素",
               "Fiery": "炽焰", "Shiny": "闪亮", "Obsidian": "黑曜石", "Deadly": "致命",
               "Radiant": "辉耀", "Mossy": "长青"}

    title_raw = card.get("Title", {}).get("Text", "") or ""
    title_en  = card.get("_originalTitleText") or ""
    if title_en:
        title_zh = zh_name or trans.get_zh(title_en) or title_raw or title_en
    else:
        title_zh = zh_name or trans.get_zh(title_raw) or title_raw
        title_en = title_raw

    card_type = card.get("Type", "")
    size      = card.get("Size", "")
    heroes    = "、".join(
        trans.get_zh_by_text_key(h) or HERO_ZH.get(h, h) or h
        for h in card.get("Heroes", [])
    ) or "通用"
    base_tier = card.get("BaseTier", "")
    tags      = card.get("DisplayTags", [])

    out = []
    type_label = {"Item": "物品", "Skill": "技能"}.get(card_type, card_type)
    size_label = {"Small": "小", "Medium": "中", "Large": "大", "Small Large": "小/大"}.get(size, size)

    header = f"\U0001f4e6 {title_zh}"
    if title_en and title_en != title_zh:
        header += f"（{title_en}）"
    out.append(header)

    attrs   = card.get("BaseAttributes", {})
    cd_ms   = attrs.get("CooldownMax")
    cd_s    = f"  冷却:{cd_ms // 1000}s" if cd_ms else ""
    multi   = attrs.get("Multicast")
    multi_s = f"  多重x{multi}" if multi and multi > 1 else ""
    dmg     = attrs.get("DamageAmount")
    dmg_s   = f"  伤害:{dmg}" if dmg else ""
    hp      = attrs.get("ShieldAmount") or attrs.get("HealAmount")
    hp_s    = f"  护盾/回复:{hp}" if hp else ""
    buy     = attrs.get("BuyPrice")
    sell    = attrs.get("SellPrice")
    price_s = f"  价格:买{buy}/卖{sell}金" if (buy or sell) else ""
    out.append(f"类型:{type_label}  尺寸:{size_label}  品质:{TIER_ZH.get(base_tier, base_tier)}  英雄:{heroes}{cd_s}{multi_s}{dmg_s}{hp_s}{price_s}")
    if tags:
        out.append("标签: " + " ".join([
            trans.get_zh_by_text_key(t) or TAG_ZH.get(t, t) or t
            for t in tags
        ]))

    tiers_data   = card.get("Tiers", {})
    tooltips     = card.get("Tooltips", [])
    replacements = card.get("TooltipReplacements", {})
    tier_order   = [t for t in ("Bronze", "Silver", "Gold", "Diamond", "Legendary") if t in tiers_data]

    if tooltips and tier_order:
        tier_blocks = []
        for tn in tier_order:
            tl = _resolve_tooltip_text(tooltips, replacements, tn)
            if tl:
                tier_blocks.append((tn, tl))
        if tier_blocks:
            out.append("\u2500")
            for tn, tl in tier_blocks:
                out.append(f"[{TIER_ZH.get(tn, tn)}] " + " / ".join(tl))
    elif tooltips:
        tl = _resolve_tooltip_text(tooltips, replacements, "")
        if tl:
            out.append("\u2500")
            out.append(" / ".join(tl))

    enchants = card.get("Enchantments", {})
    if enchants:
        out.append("\u2500")
        out.append("附魔效果:")
        for enc_key, enc_data in enchants.items():
            enc_name = ENC_ZH.get(enc_key, enc_key)
            etips = enc_data.get("Localization", {}).get("Tooltips", [])
            ereps = enc_data.get("TooltipReplacements", {})
            el = _resolve_tooltip_text(etips, ereps, "Fixed")
            if el:
                out.append(f"  [{enc_name}] " + "；".join(el))
            else:
                out.append(f"  [{enc_name}]")

    return "\n".join(out)


format_card = format_card_brief
