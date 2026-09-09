"""
大巴扎数据客户端
- howbazaar.gg: items / skills / merchants / monsterEncounterDays（百科，按 version hash 缓存）
- bazaar.mrmao.life: 玩家 season-vip-info（实时查询，60s 防刷）
"""
import asyncio
import gzip
import json
import re
import time
from pathlib import Path

import httpx

from .card_data_paths import get_gamedata_db_path

# GameData.db 本地数据源
try:
    from .gamedata_client import GameDataClient
    _GAMEDATA_AVAILABLE = True
except ImportError:
    try:
        from gamedata_client import GameDataClient
        _GAMEDATA_AVAILABLE = True
    except ImportError:
        _GAMEDATA_AVAILABLE = False

# ---- 端点 ----
HOWBAZAAR_BASE = "https://www.howbazaar.gg"
MRMAO_BASE = "https://bazaar.mrmao.life"
MRMAO_API_BASE = "https://bazaarapi.mrmao.life"

# 当前赛季 ID（comprehensive-info 必传）
CURRENT_SEASON_ID = 18
# 玩家 stat/history 接口使用独立赛季号，不能影响 runs 等其他链路。
PLAYER_STAT_SEASON_ID = 19
CURRENT_PHASE = "18.1"  # 当前赛季阶段，补丁后手动更新
RUNS_SEASON_ID = 18

# 4 个百科端点 → 本地缓存文件名
WIKI_ENDPOINTS = {
    "items": "/api/items",
    "skills": "/api/skills",
    "merchants": "/api/merchants",
    "encounters": "/api/monsterEncounterDays",
}

# 缓存目录
CACHE_DIR = Path(__file__).resolve().parent / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

VERSIONS_FILE = CACHE_DIR / "versions.json"

# 版本校验间隔（秒）：默认 6 小时
VERSION_CHECK_INTERVAL = 6 * 3600

# 玩家查询冷却（秒，同 username）
PLAYER_QUERY_TTL = 10

# HTTP 超时
HTTP_TIMEOUT = 15.0

UA = "Mozilla/5.0 (compatible; QiuBot-BazaarPlugin/1.0)"


class BazaarDataClient:
    """单例风格使用：插件 on_load 时 await client.bootstrap()。"""

    def __init__(self):
        self.wiki: dict[str, list] = {}        # name -> data list
        self.versions: dict[str, str] = {}     # name -> version hash
        self._last_check: float = 0.0
        self._player_cache: dict[str, tuple[float, dict]] = {}
        self._lock = asyncio.Lock()
        # 名字 -> 实体 反查索引（小写）
        self._name_index: dict[str, dict] = {}  # "items:lugnut" -> entity

    # ===== 启动加载 =====
    async def bootstrap(self):
        """启动时调用：优先从 GameData.db 加载 items/skills，再刷新 merchants/encounters。"""
        # 1. 从 GameData.db 加载 items/skills（本地，最新）
        self._load_from_gamedata()
        # 2. 从本地缓存加载 merchants/encounters
        self._load_local_aux()
        # 3. 异步刷新 merchants/encounters
        try:
            await self.refresh(force=False)
        except Exception as e:
            print(f"[BazaarDataClient] 启动刷新失败（沿用本地缓存）: {e}")
        self._rebuild_index()

    def _load_from_gamedata(self):
        """从 GameData.db 加载 items/skills。"""
        if not _GAMEDATA_AVAILABLE:
            print("[BazaarDataClient] GameDataClient 不可用，跳过")
            return
        db_path = get_gamedata_db_path(CACHE_DIR / "GameData.db", require_exists=True)
        if db_path is None:
            print("[BazaarDataClient] GameData.db 不存在，跳过")
            return
        try:
            gdc = GameDataClient(db_path)
            gdc.load()
            self.wiki["items"] = gdc.items()
            self.wiki["skills"] = gdc.skills()
        except Exception as e:
            print(f"[BazaarDataClient] GameData.db 加载失败: {e}")

    def _load_local_aux(self):
        """加载 merchants/encounters 本地缓存。"""
        if VERSIONS_FILE.exists():
            try:
                self.versions = json.loads(VERSIONS_FILE.read_text(encoding="utf-8"))
            except Exception:
                self.versions = {}
        for name in ("merchants", "encounters"):
            p = CACHE_DIR / f"{name}.json.gz"
            if p.exists():
                try:
                    with gzip.open(p, "rt", encoding="utf-8") as f:
                        self.wiki[name] = json.load(f)
                except Exception as e:
                    print(f"[BazaarDataClient] 读取本地 {name} 失败: {e}")

    def _load_local(self):
        if VERSIONS_FILE.exists():
            try:
                self.versions = json.loads(VERSIONS_FILE.read_text(encoding="utf-8"))
            except Exception:
                self.versions = {}
        for name in WIKI_ENDPOINTS:
            p = CACHE_DIR / f"{name}.json.gz"
            if p.exists():
                try:
                    with gzip.open(p, "rt", encoding="utf-8") as f:
                        self.wiki[name] = json.load(f)
                except Exception as e:
                    print(f"[BazaarDataClient] 读取本地 {name} 失败: {e}")

    def _save_local(self, name: str, data: list, version: str):
        p = CACHE_DIR / f"{name}.json.gz"
        with gzip.open(p, "wt", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        self.versions[name] = version
        VERSIONS_FILE.write_text(
            json.dumps(self.versions, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    async def refresh(self, force: bool = False):
        """检查版本并刷新过期数据。"""
        async with self._lock:
            now = time.time()
            if not force and (now - self._last_check) < VERSION_CHECK_INTERVAL and self.wiki:
                return
            self._last_check = now

            # 拉首页 __data.json 拿到 items/skills 的最新 version
            # merchants/encounters 没有版本号外露 → 直接和本地缓存比对响应头里的 hash 字段
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, headers={"User-Agent": UA}) as cli:
                root_versions = await self._fetch_root_versions(cli)

                tasks = []
                for name, path in WIKI_ENDPOINTS.items():
                    # items/skills 已从 GameData.db 加载，跳过
                    if name in ("items", "skills") and self.wiki.get(name):
                        continue
                    expected = root_versions.get(name)
                    current = self.versions.get(name)
                    if force or not self.wiki.get(name) or (expected and expected != current):
                        tasks.append(self._refetch(cli, name, path))
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=False)

            self._rebuild_index()

    async def _fetch_root_versions(self, cli: httpx.AsyncClient) -> dict[str, str]:
        """从首页的 SvelteKit data 里拿 4 个 version 字符串。"""
        out: dict[str, str] = {}
        try:
            r = await cli.get(HOWBAZAAR_BASE + "/")
            html = r.text
            for key in ("items", "skills", "monsters", "merchants"):
                m = re.search(rf'"{key}Version"\s*:\s*"([0-9a-f]+)"', html)
                if m:
                    # monsters → encounters 命名映射
                    target = "encounters" if key == "monsters" else key
                    out[target] = m.group(1)
        except Exception as e:
            print(f"[BazaarDataClient] 获取根版本失败: {e}")
        return out

    async def _refetch(self, cli: httpx.AsyncClient, name: str, path: str):
        url = HOWBAZAAR_BASE + path
        r = await cli.get(url)
        r.raise_for_status()
        body = r.json()
        data = body.get("data") or []
        version = body.get("version") or "unknown"
        self.wiki[name] = data
        self._save_local(name, data, version)
        print(f"[BazaarDataClient] 已更新 {name}: {len(data)} 条, version={version}")

    def _rebuild_index(self):
        self._name_index.clear()
        for kind in ("items", "skills", "merchants"):
            for entity in self.wiki.get(kind, []):
                nm = (entity.get("name") or "").strip().lower()
                if nm:
                    self._name_index[f"{kind}:{nm}"] = entity

    # ===== 玩家查询 =====
    async def get_player(self, username: str) -> dict:
        """查 mrmao 玩家信息。带 10s 同名缓存。"""
        key = username.strip().lower()
        now = time.time()
        cached = self._player_cache.get(key)
        if cached and (now - cached[0]) < PLAYER_QUERY_TTL:
            return cached[1]

        url = f"{MRMAO_BASE}/api/user/season-vip-info"
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, headers={"User-Agent": UA}) as cli:
            r = await cli.get(url, params={"username": username.strip()})
            r.raise_for_status()
            body = r.json()
        if not body.get("success"):
            raise RuntimeError(body.get("message") or "查询失败")
        data = body.get("data") or {}
        self._player_cache[key] = (now, data)
        return data

    async def get_player_stat(self, username: str, season_id: int = PLAYER_STAT_SEASON_ID) -> dict:
        """查 mrmao 玩家本赛季统计（comprehensive-info）。同 get_player 复用 10s 缓存。"""
        key = f"stat:{season_id}:{username.strip().lower()}"
        now = time.time()
        cached = self._player_cache.get(key)
        if cached and (now - cached[0]) < PLAYER_QUERY_TTL:
            return cached[1]

        url = f"{MRMAO_API_BASE}/api/user/comprehensive-info"
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, headers={"User-Agent": UA}) as cli:
            r = await cli.get(url, params={"username": username.strip(), "seasonId": season_id})
            if r.status_code == 400:
                # 用户不存在或参数错
                raise RuntimeError("玩家不存在或赛季无数据")
            r.raise_for_status()
            body = r.json()
        if not body.get("success"):
            raise RuntimeError(body.get("message") or "查询失败")
        data = body.get("data") or {}
        self._player_cache[key] = (now, data)
        return data

    # ===== 简单访问器 =====
    def items(self) -> list:
        return self.wiki.get("items", [])

    def skills(self) -> list:
        return self.wiki.get("skills", [])

    def merchants(self) -> list:
        return self.wiki.get("merchants", [])

    def encounter_days(self) -> list:
        return self.wiki.get("encounters", [])

    def get_encounter_day(self, day) -> dict | None:
        for d in self.encounter_days():
            if str(d.get("day")) == str(day):
                return d
        return None

    def find_encounter_by_name(self, name: str) -> dict | None:
        """跨所有 day 找一个 encounter（按 cardName 精确匹配优先，再子串）。"""
        target = name.strip().lower()
        exact = None
        sub = None
        for day in self.encounter_days():
            for group in day.get("groups", []):
                for card in group:
                    cn = (card.get("cardName") or "").lower()
                    if cn == target:
                        return {**card, "_day": day.get("day")}
                    if not sub and target in cn:
                        sub = {**card, "_day": day.get("day")}
        return exact or sub

    def status(self) -> dict:
        return {
            "items": len(self.items()),
            "skills": len(self.skills()),
            "merchants": len(self.merchants()),
            "encounter_days": len(self.encounter_days()),
            "versions": dict(self.versions),
            "last_check": self._last_check,
        }
