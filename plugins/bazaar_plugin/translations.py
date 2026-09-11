"""
英文 → 中文翻译表
- 官方翻译 (translations.json): 英文名 → 官方中文名（从 GameData.db + zh-CN.bytes 生成）
- hash映射 (zh-CN.bytes): tooltip Key → 中文（用于 tooltip 渲染）
"""
import hashlib
import json
import os
from pathlib import Path

CACHE_DIR        = Path(__file__).parent / "cache"
TRANS_OFFIC_FILE = CACHE_DIR / "translations.json"  # 官方翻译（英文名 -> 中文名）

_en_to_zh: dict[str, str] = {}
_zh_to_en: dict[str, str] = {}
_hash_to_zh: dict[str, str] = {}  # hash -> 中文（从 zh-CN.bytes 加载）
_tooltip_en_to_zh: dict[str, str] = {}  # 英文 tooltip 描述 -> 中文描述


def _load():
    global _en_to_zh, _zh_to_en, _hash_to_zh, _tooltip_en_to_zh
    # 1. 加载官方翻译
    if TRANS_OFFIC_FILE.exists():
        try:
            data = json.loads(TRANS_OFFIC_FILE.read_text(encoding="utf-8"))
            _en_to_zh.update(data)
            _zh_to_en.update({zh: en for en, zh in data.items()})
            print(f"[translations] 官方翻译: {len(data)} 条")
        except Exception as e:
            print(f"[translations] 官方翻译加载失败: {e}")

    # 2. 加载 hash -> 中文映射（用于 tooltip Key 查翻译）
    # Windows 游戏运行时路径；生产 Linux 可通过 ZH_TRANSLATIONS_DB 指定同步副本。
    configured_zh = os.getenv("ZH_TRANSLATIONS_DB", "").strip()
    candidates = []
    if configured_zh:
        candidates.append(Path(configured_zh).expanduser())
    candidates.extend([
        CACHE_DIR / "zh-CN.bytes",
        CACHE_DIR.parent.parent.parent / "AppData/LocalLow/Tempo Storm/The Bazaar/prod/cache/translations/zh-CN.bytes",
        Path("/mnt/c/Users/Administrator/AppData/LocalLow/Tempo Storm/The Bazaar/prod/cache/translations/zh-CN.bytes"),
    ])
    zh_bytes_file = next((path for path in candidates if path.is_file()), None)
    if zh_bytes_file is None:
        zh_json_file = CACHE_DIR / "zh-CN.json"
        if zh_json_file.exists():
            try:
                import json as _j
                data = _j.loads(zh_json_file.read_text(encoding="utf-8"))
                _hash_to_zh.update(data)
                print(f"[translations] hash映射: {len(data)} 条 (从 JSON)")
            except Exception as e:
                print(f"[translations] hash映射加载失败: {e}")
    else:
        try:
            import sqlite3
            conn = sqlite3.connect(str(zh_bytes_file))
            for row in conn.execute("SELECT hash, text FROM translation").fetchall():
                _hash_to_zh[row[0]] = row[1]
            conn.close()
            print(f"[translations] hash映射: {len(_hash_to_zh)} 条 (从 zh-CN.bytes)")
        except Exception as e:
            print(f"[translations] hash映射加载失败: {e}")

    # 3. 加载 tooltip 英文→中文映射
    tooltip_file = CACHE_DIR / "tooltip_en_to_zh.json"
    if tooltip_file.exists():
        try:
            import json as _j
            data = _j.loads(tooltip_file.read_text(encoding="utf-8"))
            _tooltip_en_to_zh.update(data)
            print(f"[translations] tooltip映射: {len(data)} 条")
        except Exception as e:
            print(f"[translations] tooltip映射加载失败: {e}")


_load()


def get_zh(name_en: str) -> str | None:
    """英文名 → 中文名"""
    if not name_en:
        return None
    return _en_to_zh.get(name_en) or _en_to_zh.get(name_en.lower())


def get_zh_both(name_en: str) -> tuple[str | None, str | None]:
    """兼容旧接口，返回 (None, 中文)"""
    return None, get_zh(name_en)


def get_en(name_zh: str) -> str | None:
    """中文名 → 英文名"""
    if not name_zh:
        return None
    return _zh_to_en.get(name_zh.strip())


def search_zh(query: str, limit: int = 10) -> list[str]:
    """中文模糊查询，返回英文名列表"""
    q = query.strip()
    if not q:
        return []
    # 完全匹配
    exact = _zh_to_en.get(q)
    if exact:
        return [exact]
    # 去空格规范化匹配
    q_nospace = q.replace(" ", "")
    exact_nospace = next(
        (en for zh, en in _zh_to_en.items() if zh.replace(" ", "") == q_nospace),
        None,
    )
    if exact_nospace:
        return [exact_nospace]
    # 子串匹配
    results = []
    for zh, en in _zh_to_en.items():
        if q_nospace in zh.replace(" ", ""):
            results.append((len(zh), en))
    results.sort()
    return [en for _, en in results[:limit]]


def has_chinese(s: str) -> bool:
    if not s:
        return False
    return any("\u4e00" <= c <= "\u9fff" for c in s)


def register_community(en_name: str, zh_name: str):
    """保留接口兼容性，不再写入（已弃用社区翻译）"""
    pass


def reload():
    _load()


def stats() -> dict:
    return {"official": len(_en_to_zh), "total": len(_en_to_zh)}


def get_zh_by_key(key: str) -> str | None:
    """通过 hash key 查中文翻译（用于 tooltip 的 Key 字段）"""
    if not key:
        return None
    return _hash_to_zh.get(key)


def get_zh_by_hash(key: str) -> str | None:
    """兼容旧调用名"""
    return get_zh_by_key(key)


def get_zh_by_text_key(key: str) -> str | None:
    """按客户端字符串 key 的 MD5 查询官方中文文本。"""
    if not key:
        return None
    return _hash_to_zh.get(hashlib.md5(key.encode("utf-8")).hexdigest())


def get_tooltip_zh(text_en: str) -> str | None:
    """通过英文 tooltip 文本查中文翻译"""
    if not text_en:
        return None
    return _tooltip_en_to_zh.get(text_en)


# 兼容旧代码仍引用 _comm_en_to_zh / _comm_zh_to_en 的场景
_comm_en_to_zh = _en_to_zh
_comm_zh_to_en = _zh_to_en
