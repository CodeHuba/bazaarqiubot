"""
card_images.json 读取辅助函数
"""
import json
import time
import urllib.request
from pathlib import Path
from typing import Optional

_CACHE_FILE = Path(__file__).resolve().parent / 'cache' / 'card_images.json'
_CACHE = None
_URL_STATUS_CACHE: dict[str, tuple[float, bool]] = {}
_URL_STATUS_TTL = 3600


def _url_is_reachable(url: str) -> bool:
    """检查远程图片是否可用，并缓存结果避免每次查询都发网络请求。"""
    now = time.monotonic()
    cached = _URL_STATUS_CACHE.get(url)
    if cached and now - cached[0] < _URL_STATUS_TTL:
        return cached[1]

    ok = False
    try:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "QiuBot/1.0"},
            method="HEAD",
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            ok = 200 <= response.status < 400
    except Exception:
        # 部分 CDN 禁止 HEAD，但允许正常 GET；用流式 GET 做兼容性探测。
        try:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "QiuBot/1.0"},
                method="GET",
            )
            with urllib.request.urlopen(request, timeout=3) as response:
                ok = 200 <= response.status < 400
        except Exception:
            ok = False

    _URL_STATUS_CACHE[url] = (now, ok)
    return ok


def _load():
    global _CACHE
    if _CACHE is None:
        try:
            _CACHE = json.loads(_CACHE_FILE.read_text(encoding='utf-8'))
        except Exception:
            _CACHE = {'cards': {}}
    return _CACHE


def get_card_image(card_id: str = None, internal_name: str = None) -> Optional[dict]:
    """
    根据 card_id 或 internal_name 获取图片信息
    返回 {'art': '...', 'artLarge': '...', 'artBlur': '...', 'name': '...'}
    """
    cards = _load().get('cards', {})

    if card_id and card_id in cards:
        info = cards[card_id]
        return info if isinstance(info, dict) else None

    if internal_name:
        for info in cards.values():
            if isinstance(info, dict) and info.get('internalName') == internal_name:
                return info

    return None


def get_art_url(card_id: str = None, internal_name: str = None, size: str = 'art') -> Optional[str]:
    """
    获取图片 URL
    size: 'art'(256px) | 'artLarge'(400px) | 'artBlur'(base64占位)
    """
    info = get_card_image(card_id, internal_name)
    url = info.get(size, '') if info else ''
    if not url or not _url_is_reachable(url):
        return None
    return url
