"""
BazaarDB Runs 查询模块
支持按英雄、卡牌（中英文）、时间查询，带分页功能
"""

import json
import math
import sqlite3
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from pathlib import Path
from . import translations as _trans
from .data_client import CURRENT_SEASON_ID, CURRENT_PHASE, RUNS_SEASON_ID


ALIAS_FILE = "/opt/qiubot/data/bz_aliases.json"

# 英雄官方中文展示名。查询入口仍接受下方兼容别名。
HERO_EN_TO_ZH = {
    'Vanessa': '瓦内莎',
    'Dooley': '杜利',
    'Mak': '马克',
    'Pygmalien': '皮格马利翁',
    'Stelle': '斯黛尔',
    'Jules': '朱尔斯',
    'Karnok': '卡诺克',
    'The Dragons': '双龙',
}
HERO_ZH_TO_EN = {
    '瓦内莎': 'Vanessa', '凡妮莎': 'Vanessa', '瓦妮莎': 'Vanessa', '瓦内萨': 'Vanessa', '海盗': 'Vanessa',
    '杜利': 'Dooley', '工程师': 'Dooley',
    '马克': 'Mak', '法师': 'Mak',
    '皮格马利翁': 'Pygmalien', '皮格': 'Pygmalien', '猪': 'Pygmalien', '猪猪': 'Pygmalien',
    '斯黛尔': 'Stelle', '斯黛拉': 'Stelle', '斯黛儿': 'Stelle', '斯特尔': 'Stelle', '机甲': 'Stelle',
    '朱尔斯': 'Jules', '吸血鬼': 'Jules',
    '卡诺克': 'Karnok', '兽人': 'Karnok',
    '双龙': 'The Dragons', '龙': 'The Dragons',
}
HERO_EN_SET = {'vanessa', 'dooley', 'mak', 'pygmalien', 'stelle', 'jules', 'karnok', 'the dragons'}


class RunsQuery:
    def __init__(self, db_path: str = "/opt/qiubot/data/bazaar_runs.db",
                 mapping_path: str = "/opt/qiubot/data/card_id_mapping.json",
                 translations_path: str = None):
        self.db_path = db_path
        self.mapping_path = mapping_path
        if translations_path is None:
            translations_path = str(Path(__file__).parent / "cache" / "translations.json")
        self.translations_path = translations_path
        self.conn = None
        self.card_mapping = {}      # cardId -> {name, type}
        self.name_to_ids = {}       # 英文名(lower) -> [cardId]
        self.en_to_zh = {}          # 英文名 -> 中文名
        self.zh_to_en = {}          # 中文名 -> 英文名
        self.card_aliases = {}      # 别名 -> 官方中文名
        self.hero_aliases = {}      # 别名 -> 英雄名
        self.tex_map = {}           # 英文名 -> 图片文件名
        self.size_map = {}          # cardId -> size (Small/Medium/Large)
        self.card_heroes = {}       # cardId -> [hero list]

    def load(self):
        """加载数据库、映射表和翻译"""
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        try:
            with open(self.mapping_path, 'r', encoding='utf-8') as f:
                self.card_mapping = json.load(f) or {}
        except (FileNotFoundError, json.JSONDecodeError):
            self.card_mapping = {}
        for card_id, info in self.card_mapping.items():
            info = info or {}
            if not info:
                # 保留 key，但将 null 正规化为字典，后续 card_info 可安全使用。
                self.card_mapping[card_id] = info
            name = str(info.get('name') or '').lower()
            if not name:
                continue
            if name not in self.name_to_ids:
                self.name_to_ids[name] = []
            self.name_to_ids[name].append(card_id)

        # 统一使用卡图缓存补全名称映射，避免旧 card_id_mapping.json 缺失或过期导致无法查询。
        try:
            from . import card_image_helper as _cih_load
            for _cid, _info in (_cih_load._load().get('cards', {}) or {}).items():
                _info = _info or {}
                _en = _info.get('internalName') or ''
                if _en:
                    existing = self.card_mapping.get(_cid)
                    if not isinstance(existing, dict) or not existing.get('name'):
                        self.card_mapping[_cid] = {'name': _en, 'type': _info.get('type', '')}
                    ids = self.name_to_ids.setdefault(_en.lower().replace(' ', ''), [])
                    if _cid not in ids:
                        ids.append(_cid)
        except Exception:
            pass

        # 加载翻译
        trans_path = Path(self.translations_path)
        if trans_path.exists():
            data = json.loads(trans_path.read_text(encoding='utf-8'))
            self.en_to_zh = data
            self.zh_to_en = {v: k for k, v in data.items()}

        # 加载卡牌尺寸映射（从 GameData.db）
        gamedata_path = Path(__file__).parent / 'cache' / 'GameData.db'
        if gamedata_path.exists():
            try:
                import sqlite3 as _sl2, json as _j2
                _gc = _sl2.connect(str(gamedata_path))
                for (_d,) in _gc.execute('SELECT Data FROM cards').fetchall():
                    try:
                        _j = _j2.loads(_d)
                        _cid = _j.get('Id')
                        _sz = _j.get('Size', '')
                        if _cid and _sz:
                            self.size_map[_cid] = _sz
                    except Exception:
                        pass
                _gc.close()
            except Exception:
                pass

        # 加载 tex_map
        tex_path = Path(__file__).parent / 'cache' / 'item_tex_map.json'
        if tex_path.exists():
            self.tex_map = json.loads(tex_path.read_text(encoding='utf-8'))

        # 加载卡牌职业映射
        heroes_path = Path('/opt/qiubot/data/card_heroes_map.json')
        if heroes_path.exists():
            self.card_heroes = json.loads(heroes_path.read_text(encoding="utf-8"))

        # 加载自定义别名
        alias_path = Path(ALIAS_FILE)
        if alias_path.exists():
            try:
                raw = json.loads(alias_path.read_text(encoding="utf-8"))
                self.card_aliases = raw.get("cards", {})
                self.hero_aliases = raw.get("heroes", {})
            except Exception:
                pass

    def _card_image_info(self, card_id: str) -> dict:
        """从 card_images 缓存读取卡牌信息，映射表缺失时也能稳定返回结果。"""
        try:
            from . import card_image_helper as _cih
            return _cih.get_card_image(card_id=card_id) or {}
        except Exception:
            return {}

    def _safe_mapping_info(self, card_id: str) -> dict:
        info = self.card_mapping.get(card_id)
        return info if isinstance(info, dict) else {}

    def card_display_info(self, card_id: str, validate_image: bool = True) -> dict:
        """返回网页统一使用的卡牌展示信息，图片优先使用 #bz db 同源原图。"""
        from . import card_image_helper as _cih
        info = self._safe_mapping_info(card_id)
        image_info = self._card_image_info(card_id)
        name_en = info.get('name') or image_info.get('internalName') or card_id
        name_zh = image_info.get('name') or self.get_zh_name(name_en)
        if validate_image:
            image_url = (_cih.get_art_url(card_id=card_id, internal_name=name_en, size='artLarge')
                         or _cih.get_art_url(card_id=card_id, internal_name=name_en, size='art') or '')
        else:
            # 统计接口不得为每张卡串行探测 CDN；浏览器按原 URL 加载图片即可。
            image_url = image_info.get('artLarge') or image_info.get('art') or ''
        return {
            'cardId': card_id,
            'name': name_zh if name_zh != name_en else name_en,
            'name_en': name_en,
            'img': image_url,
            'size': self.size_map.get(card_id) or image_info.get('size') or 'Small',
        }

    def translate_name(self, name: str) -> str:
        """中文名转英文名，已经是英文则原样返回"""
        if name in self.card_aliases:
            name = self.card_aliases[name]
        if name in self.zh_to_en:
            return self.zh_to_en[name]
        # 模糊匹配中文
        for zh, en in self.zh_to_en.items():
            if name in zh or zh in name:
                return en
        # fallback：社区翻译
        comm = _trans.get_en(name)
        if comm:
            return comm
        results = _trans.search_zh(name, limit=1)
        if results:
            return results[0]
        return name

    def get_zh_name(self, en_name: str) -> str:
        """英文名转中文名，无翻译则返回英文"""
        return self.en_to_zh.get(en_name, en_name)

    def resolve_hero(self, name: str) -> Optional[str]:
        """解析英雄名（支持中英文）"""
        if name in self.hero_aliases:
            name = self.hero_aliases[name]
        if name.lower() in HERO_EN_SET:
            if name.lower() == 'pygmalien':
                return 'Pygmalien'
            if name.lower() == 'the dragons':
                return 'The Dragons'
            return name.capitalize()
        if name in HERO_ZH_TO_EN:
            return HERO_ZH_TO_EN[name]
        return None

    def find_card_ids(self, name: str) -> List[str]:
        """根据卡牌名（支持中英文、模糊匹配）找到 cardId 列表"""
        # 先尝试中文翻译
        en_name = self.translate_name(name)
        name_lower = en_name.lower().replace(' ', '')

        # 精确匹配
        for card_name, ids in self.name_to_ids.items():
            if name_lower == card_name.replace(' ', ''):
                return ids
        # 模糊匹配
        matches = []
        for card_name, ids in self.name_to_ids.items():
            if name_lower in card_name.replace(' ', '') or card_name.replace(' ', '') in name_lower:
                matches.extend(ids)
        return matches

    def query(self,
              hero: Optional[str] = None,
              cards: Optional[List[str]] = None,
              days: Optional[int] = None,
              min_wins: Optional[int] = None,
              page: int = 1,
              page_size: int = 5,
              rank_filter: str = "all") -> Dict:
        """
        查询 runs，返回 {runs, total, page, pages, query_desc}
        """
        if not self.conn:
            self.load()

        # 默认只展示当前补丁阶段的阵容，避免旧补丁数据混入。
        sql = "SELECT id, hero, username, created_at, items_json, skills_json, stat_wins, stat_losses, screenshot_url FROM runs WHERE season=? AND phase=?"
        params = [RUNS_SEASON_ID, CURRENT_PHASE]

        if hero:
            sql += " AND LOWER(hero) = LOWER(?)"
            params.append(hero)
        if days:
            cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
            sql += " AND created_at >= ?"
            params.append(cutoff)
        if min_wins:
            sql += " AND stat_wins >= ?"
            params.append(min_wins)
        if rank_filter == 'legendary':
            sql += " AND player_rank='Legendary'"

        sql += " ORDER BY created_at DESC"
        rows = self.conn.execute(sql, params).fetchall()

        # 卡牌筛选
        card_ids_sets = []
        if cards:
            for card_name in cards:
                ids = self.find_card_ids(card_name)
                if ids:
                    card_ids_sets.append(set(ids))
                else:
                    return {'runs': [], 'total': 0, 'page': page, 'pages': 0}

        # 过滤
        filtered = []
        for row in rows:
            run_id, hero_name, username, created_at, items_json, skills_json, wins, losses, screenshot = row
            items = json.loads(items_json) if items_json else []
            skills = json.loads(skills_json) if skills_json else []

            run_card_ids = set(it.get('cardId') for it in items if it.get('cardId'))
            run_card_ids.update(sk.get('cardId') for sk in skills if isinstance(sk, dict) and sk.get('cardId'))

            if card_ids_sets:
                if not all(run_card_ids & card_set for card_set in card_ids_sets):
                    continue

            item_names = []
            card_imgs = []
            card_details = []
            from . import card_image_helper as _cih
            for it in items:
                cid = it.get('cardId')
                if not cid:
                    continue
                info = self._safe_mapping_info(cid)
                image_info = self._card_image_info(cid)
                en = info.get('name') or image_info.get('internalName') or cid
                zh = image_info.get('name') or self.get_zh_name(en)
                display_name = zh if zh != en else en
                item_names.append(display_name)
                tex = self.tex_map.get(en, '')
                card_imgs.append(tex)
                card_details.append({
                    'cardId': cid,
                    'name': display_name,
                    'name_en': en,
                    'img': _cih.get_art_url(card_id=cid, internal_name=en, size='artLarge') or _cih.get_art_url(card_id=cid, internal_name=en, size='art') or '',
                    'size': self.size_map.get(cid) or image_info.get('size') or 'Small',
                })

            screenshot_full = ('https://usercontent.bzdb.network' + screenshot) if screenshot else ''
            filtered.append({
                'id': run_id,
                'hero': hero_name,
                'username': username,
                'created_at': created_at,
                'wins': wins or 0,
                'losses': losses or 0,
                'items': item_names,
                'cards': card_details,
                'card_imgs': card_imgs,
                'screenshot': screenshot_full,
                'url': f"https://bazaardb.gg/run/tracker/{run_id}"
            })

        total = len(filtered)
        pages = (total + page_size - 1) // page_size if total > 0 else 0
        page = max(1, min(page, pages)) if pages > 0 else 1

        start = (page - 1) * page_size
        end = start + page_size

        return {
            'runs': filtered[start:end],
            'total': total,
            'page': page,
            'pages': pages
        }

    def format_result(self, result: Dict, query_desc: str = "", raw_cmd: str = "") -> str:
        """格式化查询结果"""
        runs = result['runs']
        total = result['total']
        page = result['page']
        pages = result['pages']

        if not runs:
            return (
                f"未找到符合条件的 runs\n\n"
                f"💡 用法提示：\n"
                f"  #bz runs 海盗 — 按英雄查询\n"
                f"  #bz runs 火炮阵列 — 按卡牌查询\n"
                f"  #bz runs 海盗 赛博铁尺+火炮阵列 — 组合查询\n"
                f"  #bz runs 海盗 --days 3 — 限定时间\n"
                f"  #bz runs 海盗 -p2 — 翻页"
            )

        lines = [f"📋 共 {total} 条结果（第 {page}/{pages} 页）"]
        if query_desc:
            lines[0] += f"  [{query_desc}]"
        lines.append("")

        start_idx = (page - 1) * len(runs)  # approximate
        for i, run in enumerate(runs, start_idx + 1):
            day = run['wins'] + run['losses']
            items_preview = '、'.join(run['items'])

            player = run['username'] or '匿名'
            hero_zh = HERO_EN_TO_ZH.get(run['hero'], run['hero'])

            # 格式化时间
            time_str = ""
            if run.get("created_at"):
                try:
                    from datetime import datetime as _dt
                    dt = _dt.fromisoformat(run["created_at"].replace("Z", "+00:00"))
                    from datetime import timezone, timedelta as _td
                    dt_cn = dt.astimezone(timezone(_td(hours=8)))
                    time_str = dt_cn.strftime("%m-%d %H:%M")
                except Exception:
                    pass

            screenshot_url = ""
            if run.get("screenshot"):
                screenshot_url = run["screenshot"]

            if screenshot_url:
                lines.append(
                    f"{i}. [{hero_zh}] Day{day} {run['wins']}胜{run['losses']}负 | {player} | {time_str}\n"
                    f"[CQ:image,file={screenshot_url}]"
                )
            else:
                lines.append(
                    f"{i}. [{hero_zh}] Day{day} {run['wins']}胜{run['losses']}负 | {player} | {time_str}\n"
                    f"   {items_preview}\n"
                )

        # 翻页提示
        lines.append("")
        if pages > 1:
            if page < pages:
                # 构建翻页命令
                next_cmd = raw_cmd.rstrip() if raw_cmd else "#bz runs"
                # 移除已有的 -p 参数
                import re
                next_cmd = re.sub(r'\s*-p\d+', '', next_cmd)
                lines.append(f"▶ 下一页: {next_cmd} -p{page+1}")
            if page > 1:
                prev_cmd = raw_cmd.rstrip() if raw_cmd else "#bz runs"
                import re
                prev_cmd = re.sub(r'\s*-p\d+', '', prev_cmd)
                lines.append(f"◀ 上一页: {prev_cmd} -p{page-1}")

        # 用法提示
        lines.append("")
        lines.append("💡 #bz runs [英雄] [卡牌+卡牌] [--days N] [-pN]")

        return '\n'.join(lines)


    def winrate(self,
                cards: list,
                hero: str = None,
                days: int = None,
                min_wins_threshold: int = 10,
                all_phases: bool = False,
                rank_filter: str = "all") -> dict:
        """
        计算包含指定卡牌组合的 runs 中，达到 min_wins_threshold 胜的比率。
        返回 {total, ten_win, rate, card_names, not_found}
        """
        import json as _json
        from datetime import datetime as _dt, timedelta as _td

        if not self.conn:
            self.load()

        # 解析卡牌 -> cardId 集合
        card_ids_sets = []
        not_found = []
        card_names = []
        card_details = []
        for card_name in cards:
            ids = self.find_card_ids(card_name)
            if not ids:
                not_found.append(card_name)
            else:
                primary_id = ids[0]
                display = self.card_display_info(primary_id)
                card_ids_sets.append(set(ids))
                card_names.append(display['name'])
                card_details.append(display)

        if not card_ids_sets:
            return {
                'total': 0, 'ten_win': 0, 'rate': 0.0,
                'card_names': card_names, 'card_details': card_details, 'not_found': not_found,
            }

        # SQL 层用有索引的字段（hero/rank/days/phase）缩小结果集
        # 再用 card_ids_text 在 Python 层做精确过滤，避免全量 json.loads
        sql = "SELECT card_ids_text, stat_wins FROM runs WHERE season=?"
        params = [RUNS_SEASON_ID]
        if not all_phases:
            sql += " AND phase=?"
            params.append(CURRENT_PHASE)
        if hero:
            sql += " AND LOWER(hero) = LOWER(?)"
            params.append(hero)
        if days:
            cutoff = (_dt.utcnow() - _td(days=days)).isoformat()
            sql += " AND created_at >= ?"
            params.append(cutoff)
        if rank_filter == 'legendary':
            sql += " AND player_rank='Legendary'"
        rows = self.conn.execute(sql, params).fetchall()

        # 每组 cardId set 取代表 id 列表，用 str.find 在 card_ids_text 快速判断
        rep_ids = [next(iter(s)) for s in card_ids_sets]

        total = 0
        ten_win = 0
        for card_ids_text, wins in rows:
            if not card_ids_text:
                continue
            # 先用代表 id 快速过滤（UUID 唯一，误判率极低）
            if not all(rep in card_ids_text for rep in rep_ids):
                continue
            # 有多 id 的卡才做精确集合校验
            if any(len(s) > 1 for s in card_ids_sets):
                run_ids = set(card_ids_text.split())
                if not all(run_ids & s for s in card_ids_sets):
                    continue
            total += 1
            if (wins or 0) >= min_wins_threshold:
                ten_win += 1

        rate = ten_win / total if total > 0 else 0.0
        return {
            'total': total,
            'ten_win': ten_win,
            'rate': rate,
            'card_names': card_names,
            'card_details': card_details,
            'not_found': not_found,
        }


    def partner(self,
                card: str,
                days: int = None,
                min_count: int = 50,
                top_n: int = 3,
                wins_threshold: int = 10,
                all_phases: bool = False,
                rank_filter: str = "all") -> dict:
        """
        查询与某张卡搭配时胜率最高的前 top_n 张搭档卡。
        只统计出现次数 >= min_count 的搭档。
        返回 {card_name, partners: [{name, total, ten_win, rate}], not_found}
        """
        import json as _json
        from datetime import datetime as _dt, timedelta as _td
        from collections import defaultdict

        if not self.conn:
            self.load()

        # 解析目标卡
        ids = self.find_card_ids(card)
        if not ids:
            return {"card_name": card, "partners": [], "not_found": True}
        target_ids = set(ids)
        en_name = self.translate_name(card)
        card_name = self.get_zh_name(en_name)
        if card_name == en_name:
            card_name = card

        # 拉取所有 runs（按条件过滤阶段/时间）
        sql = "SELECT items_json, stat_wins FROM runs WHERE season=?"
        params = [RUNS_SEASON_ID]
        if not all_phases:
            sql += " AND phase=?"
            params.append(CURRENT_PHASE)
        if days:
            cutoff = (_dt.utcnow() - _td(days=days)).isoformat()
            sql += " AND created_at >= ?"
            params.append(cutoff)
        if rank_filter == 'legendary':
            sql += " AND player_rank='Legendary'"
        rows = self.conn.execute(sql, params).fetchall()

        # 统计每张搭档卡的出现次数和10胜次数
        total_map = defaultdict(int)
        win_map = defaultdict(int)

        for items_json, wins in rows:
            try:
                items = _json.loads(items_json)
                run_ids = {item["cardId"] for item in items if "cardId" in item}
            except Exception:
                continue
            # 该局必须包含目标卡
            if not (run_ids & target_ids):
                continue
            is_win = (wins or 0) >= wins_threshold
            # 统计搭档（排除目标卡自身）
            for cid in run_ids:
                if cid in target_ids:
                    continue
                total_map[cid] += 1
                if is_win:
                    win_map[cid] += 1

        # 过滤低样本，计算胜率，排序
        results = []
        for cid, total in total_map.items():
            if total < min_count:
                continue
            ten_win = win_map.get(cid, 0)
            rate = ten_win / total
            # 获取卡牌名
            display = self.card_display_info(cid)
            en = display['name_en']
            if not en:
                continue
            results.append({"name": display['name'], "total": total, "ten_win": ten_win, "rate": rate, "img": display['img'], "size": display['size'], "name_en": en, "cardId": cid})

        # 计算共现率（含目标卡的局里，搭档出现的比例）
        target_total = sum(1 for items_json, wins in rows
                          if (lambda ids: bool(ids & target_ids))(
                              {item['cardId'] for item in __import__('json').loads(items_json) if 'cardId' in item}))

        for r in results:
            cid_list = [cid for cid, info in self.card_mapping.items()
                        if (info.get('name','') == (self.en_to_zh.get(r['name'], r['name']) or r['name'])
                            or self.get_zh_name(info.get('name','')) == r['name'])]
            r['appear_rate'] = r['total'] / target_total if target_total > 0 else 0

        by_winrate = sorted(results, key=lambda x: (-x['rate'], -x['total']))[:top_n]
        by_appear = sorted(results, key=lambda x: (-x['appear_rate'], -x['total']))[:top_n]

        return {
            'card_name': card_name,
            'by_winrate': by_winrate,
            'by_appear': by_appear,
            'target_total': target_total,
            'not_found': False,
        }



    def topcard(self,
                hero: str,
                top_n: int = 5,
                days: int = None,
                min_count: int = 50,
                all_phases: bool = False,
                sort_by: str = "total",
                rank_filter: str = "all") -> dict:
        """
        查询某个职业下胜率最高的 top_n 张物品卡（只统计该职业专属卡）。
        统计该职业所有 runs 中每张卡的出现次数、胜场数（stat_wins >= 10）、胜率。
        返回 {hero, hero_zh, top: [{name_zh, name_en, total, ten_win, rate}], total_runs, days}
        """
        import json as _json
        from datetime import datetime as _dt, timedelta as _td
        import time as _time

        # 读取缓存
        global _topcard_cache, _topcard_cache_ttl
        _tc_key = (hero, top_n, days, all_phases, sort_by, rank_filter)
        if _tc_key in _topcard_cache:
            _cached, _exp = _topcard_cache[_tc_key]
            if _time.time() < _exp:
                return _cached

        if not self.conn:
            self.load()

        # 拉取该英雄所有 runs（按条件过滤阶段/时间）
        sql = "SELECT items_json, stat_wins FROM runs WHERE season=? AND LOWER(hero)=LOWER(?)"
        params = [RUNS_SEASON_ID, hero]
        if not all_phases:
            sql += " AND phase=?"
            params.append(CURRENT_PHASE)
        if days:
            cutoff = (_dt.utcnow() - _td(days=days)).isoformat()
            sql += " AND created_at >= ?"
            params.append(cutoff)
        if rank_filter == 'legendary':
            sql += " AND player_rank='Legendary'"
        rows = self.conn.execute(sql, params).fetchall()

        # 统计每张卡的出现次数和胜场数（只统计该职业专属卡）
        card_total: dict = {}   # cardId -> total count
        card_wins: dict = {}    # cardId -> 10win count

        # GameData.db 中双龙的职业名为 Hero8
        _hero_gamedata = {'The Dragons': 'Hero8'}.get(hero, hero)

        for items_json, wins in rows:
            try:
                items = _json.loads(items_json)
                run_ids = {item['cardId'] for item in items if 'cardId' in item}
            except Exception:
                continue
            is_win = (wins or 0) >= 10
            for cid in run_ids:
                # 过滤：只统计该职业专属卡
                card_heroes_list = self.card_heroes.get(cid, [])
                if _hero_gamedata not in card_heroes_list:
                    continue
                card_total[cid] = card_total.get(cid, 0) + 1
                if is_win:
                    card_wins[cid] = card_wins.get(cid, 0) + 1

        # 过滤低频卡，计算胜率
        from . import card_image_helper as _cih
        results = []
        for cid, total in card_total.items():
            if total < min_count:
                continue
            info = self._safe_mapping_info(cid)
            image_info = self._card_image_info(cid)
            name_en = info.get('name') or image_info.get('internalName') or cid
            name_zh = image_info.get('name') or self.get_zh_name(name_en)
            ten_win = card_wins.get(cid, 0)
            rate = ten_win / total if total > 0 else 0.0
            display = self.card_display_info(cid, validate_image=False)
            results.append({
                'cardId': cid,
                'name_zh': display['name'],
                'name_en': name_en,
                'total': total,
                'ten_win': ten_win,
                'rate': rate,
                'img': display['img'],
                'size': display['size'],
            })

        # 按指定方式排序
        if sort_by == 'rate':
            results.sort(key=lambda x: (-x['rate'], -x['total']))
        elif sort_by == 'ten_win':
            results.sort(key=lambda x: (-x['ten_win'], -x['rate']))
        else:  # total
            results.sort(key=lambda x: (-x['total'], -x['rate']))
        top = results[:top_n]

        hero_zh = HERO_EN_TO_ZH.get(hero, hero)

        _tc_result = {
            'hero': hero,
            'hero_zh': hero_zh,
            'top': top,
            'total_runs': len(rows),
            'days': days,
        }
        _topcard_cache[_tc_key] = (_tc_result, _time.time() + _topcard_cache_ttl)
        return _tc_result

    def card_tier_table(self, hero: str, days: int = None, rank_filter: str = "all") -> dict:
        """按当前赛段、职业专属基础卡计算出场为主的相对 T 表。"""
        cache_key = (RUNS_SEASON_ID, CURRENT_PHASE, hero, days, rank_filter)
        cached = _card_tier_cache.get(cache_key)
        if cached and __import__('time').time() < cached[1]:
            return cached[0]
        if not self.conn:
            self.load()
        sql = "SELECT items_json, stat_wins FROM runs WHERE season=? AND phase=? AND LOWER(hero)=LOWER(?)"
        params = [RUNS_SEASON_ID, CURRENT_PHASE, hero]
        if days:
            sql += " AND created_at >= ?"
            params.append((datetime.utcnow() - timedelta(days=days)).isoformat())
        if rank_filter == "legendary":
            sql += " AND player_rank='Legendary'"
        rows = self.conn.execute(sql, params).fetchall()
        gamedata_hero = {"The Dragons": "Hero8"}.get(hero, hero)
        exclusive_ids = {
            cid for cid, heroes in self.card_heroes.items()
            if isinstance(heroes, (list, tuple, set))
            and {str(hero) for hero in heroes} == {gamedata_hero}
            and str(self._safe_mapping_info(cid).get('type') or '').lower() == 'item'
        }
        appearances = {cid: 0 for cid in exclusive_ids}
        ten_wins = {cid: 0 for cid in exclusive_ids}
        for items_json, wins in rows:
            try:
                run_ids = {
                    item['cardId'] for item in json.loads(items_json)
                    if isinstance(item, dict) and item.get('cardId') in exclusive_ids
                }
            except (TypeError, ValueError, KeyError):
                continue
            for cid in run_ids:
                appearances[cid] += 1
                if (wins or 0) >= 10:
                    ten_wins[cid] += 1
        positive = sorted(value for value in appearances.values() if value > 0)
        threshold = max(10, positive[max(0, math.ceil(len(positive) * .2) - 1)]) if positive else 10
        cards = []
        # 零出场卡既不评级也不展示，无需生成展示信息（会触发图片路径处理）。
        for cid in exclusive_ids:
            count = appearances[cid]
            if count <= 0:
                continue
            cards.append({**self.card_display_info(cid, validate_image=False), 'appearance_count': count,
                          'appearance_rate': count / len(rows) if rows else 0.0,
                          'ten_win': ten_wins[cid], 'win_rate': ten_wins[cid] / count})
        rated = [card for card in cards if card['appearance_count'] >= threshold]
        # 未出现过的物品没有可供用户判断的数据，不在“数据不足”区展示。
        insufficient = [card for card in cards if 0 < card['appearance_count'] < threshold]
        self._score_tier_cards(rated)
        rated.sort(key=lambda c: (-c['score'], -c['appearance_rate'], -c['win_rate'], -c['appearance_count'], c['name']))
        insufficient.sort(key=lambda c: (-c['appearance_count'], -c['win_rate'], c['name']))
        specs = [('夯', .10), ('顶级', .20), ('人上人', .30), ('NPC', .25), ('拉完了', .15)]
        groups = [[] for _ in specs]
        boundaries = [math.ceil(len(rated) * cumulative) for cumulative in (.10, .30, .60, .85)]
        group = 0
        for index, card in enumerate(rated):
            while group < 4 and index >= boundaries[group] and (index == 0 or card['score'] != rated[index - 1]['score']):
                group += 1
            groups[group].append(card)
        result = {'hero': hero, 'season': RUNS_SEASON_ID, 'phase': CURRENT_PHASE, 'days': days,
                  'rank_filter': rank_filter, 'total_runs': len(rows), 'sample_threshold': threshold,
                  'formula': {'appearance_weight': .7, 'winrate_weight': .3}, 'tie_policy': 'higher_tier',
                  'tiers': [{'name': name, 'target_ratio': ratio, 'cards': group_cards}
                            for (name, ratio), group_cards in zip(specs, groups)],
                  'insufficient': insufficient}
        _card_tier_cache[cache_key] = (result, __import__('time').time() + _card_tier_cache_ttl)
        return result

    @staticmethod
    def _score_tier_cards(cards: list) -> None:
        total = len(cards)
        for field, target in (('appearance_rate', 'appearance_percentile'), ('win_rate', 'winrate_percentile')):
            values = sorted(card[field] for card in cards)
            for card in cards:
                positions = [index + 1 for index, value in enumerate(values) if value == card[field]]
                card[target] = sum(positions) / len(positions) / total if positions else 0.0
        for card in cards:
            card['score'] = round(card['appearance_percentile'] * .7 + card['winrate_percentile'] * .3, 6)

    def comp(self,
             hero: str,
             n: int = 3,
             top_k: int = 10,
             min_count: int = 50,
             min_config_count: int = 5,
             all_phases: bool = False,
             rank_filter: str = "all",
             required_card: str = None) -> dict:
        """四层嵌套阵容榜 L1(2张)→L2(3张)→L3(4张)→具体配置
        required_card: 若指定，只统计包含该 cardId 的 runs"""
        import json as _json
        import time
        from collections import defaultdict

        MIN_SUPPORT            = min_count
        GENERIC_THRESHOLD      = 0.30
        L2_OVERLAP             = 0.50
        WIN_WEIGHT             = 0.70
        APPEAR_WEIGHT          = 0.30
        TOP_L1 = TOP_L2 = TOP_L3 = 5
        TOP_CFG = 3

        global _comp_cache, _comp_cache_ttl
        cache_key = (hero, 'v4-current-phase', CURRENT_PHASE, rank_filter, required_card)
        if cache_key in _comp_cache:
            cached, exp = _comp_cache[cache_key]
            if time.time() < exp:
                return cached

        if not self.conn:
            self.load()

        # 阵容分析固定使用当前赛季、当前 phase；历史 phase 的平衡环境不具备参考性。
        sql = "SELECT id, items_json, stat_wins, screenshot_url, created_at FROM runs WHERE season=? AND LOWER(hero)=LOWER(?) AND phase=?"
        params = [RUNS_SEASON_ID, hero, CURRENT_PHASE]
        if rank_filter == 'legendary':
            sql += " AND player_rank='Legendary'"
        rows = self.conn.execute(sql, params).fetchall()

        # 第一步：完全相同卡组去重，累加出场/胜场
        deck_stats = defaultdict(lambda: {
            'count': 0, 'wins': 0, 'items': None, 'screenshot': '', 'run_id': '', 'created_at': ''
        })
        for run_id, items_json, stat_wins, screenshot_url, created_at in rows:
            try:
                items = _json.loads(items_json) if items_json else []
                if not items:
                    continue
                card_ids = tuple(sorted(item['cardId'] for item in items if 'cardId' in item))
                if len(card_ids) < 2:
                    continue
                deck_set = frozenset(card_ids)
                # 阵容查询：过滤掉不包含 required_card 的 runs
                if required_card and required_card not in deck_set:
                    continue
                is_win = int(stat_wins or 0) >= 10
                s = deck_stats[deck_set]
                s['count'] += 1
                if is_win:
                    s['wins'] += 1
                if s['items'] is None:
                    s['items'] = items
                    s['run_id'] = run_id or ''
                    s['created_at'] = created_at or ''
                scr = screenshot_url or ''
                if scr:
                    if is_win or not s['screenshot']:
                        s['screenshot'] = scr
                        s['run_id'] = run_id or s['run_id']
                        s['created_at'] = created_at or s['created_at']
            except Exception:
                continue

        total_runs = sum(s['count'] for s in deck_stats.values())
        if total_runs == 0:
            return {
                'hero': hero, 'layers': [], 'recommendations': [], 'total_runs': 0,
                'phase': CURRENT_PHASE, 'season': RUNS_SEASON_ID,
            }

        all_decks = list(deck_stats.keys())

        # 第二步：FP-Growth 挖掘频繁2-项集（带降级机制）
        FALLBACK_SUPPORTS = [MIN_SUPPORT, 20, 10]  # L1降级阈值，与L2/L3一致
        cands_l1 = []
        l1_min_support = MIN_SUPPORT  # 最终生效的L1支持度
        try:
            from mlxtend.preprocessing import TransactionEncoder
            from mlxtend.frequent_patterns import fpgrowth
            import pandas as pd
            transactions = [list(ds) for ds in all_decks]
            te = TransactionEncoder()
            te_arr = te.fit(transactions).transform(transactions)
            df = pd.DataFrame(te_arr, columns=te.columns_)
            for fallback_sup in FALLBACK_SUPPORTS:
                freq_df = fpgrowth(df, min_support=fallback_sup / total_runs, use_colnames=True)
                freq_df['length'] = freq_df['itemsets'].apply(len)
                cands = [frozenset(r['itemsets']) for _, r in freq_df[freq_df['length'] == 2].iterrows()]
                if cands:
                    cands_l1 = cands
                    l1_min_support = fallback_sup
                    break
        except Exception as e:
            return {
                'hero': hero, 'layers': [], 'recommendations': [], 'total_runs': total_runs,
                'phase': CURRENT_PHASE, 'season': RUNS_SEASON_ID, 'error': str(e),
            }

        if not cands_l1:
            return {
                'hero': hero, 'layers': [], 'recommendations': [], 'total_runs': total_runs,
                'phase': CURRENT_PHASE, 'season': RUNS_SEASON_ID,
            }

        # 排除通用卡。查询锚点必然出现在全部候选中，不能把它当作通用卡过滤掉。
        _rc_set = {required_card} if required_card else set()
        card_freq = defaultdict(int)
        for fs in cands_l1:
            for c in fs:
                card_freq[c] += 1
        # 按卡牌分析时，所有 runs 都含查询锚点；其余高频卡恰恰可能是体系核心，不能按通用卡剔除。
        # 非锚点阵容榜仍保留原有通用卡过滤规则。
        if required_card:
            generic = set()
        else:
            generic = {c for c, cnt in card_freq.items() if cnt / len(cands_l1) > GENERIC_THRESHOLD}
        cands_l1 = [fs for fs in cands_l1 if not ((fs - _rc_set) & generic)]
        if not cands_l1:
            return {
                'hero': hero, 'layers': [], 'recommendations': [], 'total_runs': total_runs,
                'phase': CURRENT_PHASE, 'season': RUNS_SEASON_ID,
            }

        def jaccard(a, b):
            return len(a & b) / len(a | b) if (a | b) else 0.0

        def deck_count(skel):
            return sum(deck_stats[ds]['count'] for ds in all_decks if skel <= ds)

        def compute_stats(skeleton, pool):
            matched = [ds for ds in pool if skeleton <= ds]
            if not matched:
                return None
            count = sum(deck_stats[ds]['count'] for ds in matched)
            wins  = sum(deck_stats[ds]['wins']  for ds in matched)
            return {
                'skeleton': skeleton,
                'count': count, 'wins': wins,
                'win_rate': wins / count if count else 0.0,
                'appear_rate': count / total_runs,
                'matched_decks': matched,
            }

        def score(st, max_appear):
            norm = st['appear_rate'] / max_appear if max_appear else 0.0
            return st['win_rate'] * WIN_WEIGHT + norm * APPEAR_WEIGHT

        from . import card_image_helper as _cih
        def card_info(cid, is_core=False, is_new=False):
            # card_id_mapping.json 可能缺失、过期，甚至存在 null 值；卡图缓存是更可靠的兜底来源。
            info = self.card_mapping.get(cid) or {}
            image_info = _cih.get_card_image(card_id=cid) or {}
            name_en = info.get('name') or image_info.get('internalName') or cid
            name_zh = image_info.get('name') or self.get_zh_name(name_en)
            art_url = _cih.get_art_url(card_id=cid, internal_name=name_en, size='artLarge') or _cih.get_art_url(card_id=cid, internal_name=name_en, size='art') or ''
            card_size = self.size_map.get(cid) or image_info.get('size') or 'Small'
            return {
                'cardId': cid,
                'name_zh': name_zh if name_zh != name_en else name_en,
                'name_en': name_en,
                'img': art_url,
                'size': card_size,
                'is_core': is_core,
                'is_new': is_new,
            }

        # L1 去重（严格零重叠，required_card 豁免重叠判断）
        _rc_set = {required_card} if required_card else set()
        cands_l1.sort(key=lambda x: -deck_count(x))
        l1_final = []
        for c in cands_l1:
            c_cmp = c - _rc_set
            if not any(len(c_cmp & (ex - _rc_set)) > 0 for ex in l1_final):
                l1_final.append(c)

        # 计算L1 stats（使用降级后的支持度）
        l1_stats = []
        for skel in l1_final:
            st = compute_stats(skel, all_decks)
            if st:
                if st['count'] >= l1_min_support:
                    l1_stats.append(st)

        if not l1_stats:
            return {
                'hero': hero, 'layers': [], 'recommendations': [], 'total_runs': total_runs,
                'phase': CURRENT_PHASE, 'season': RUNS_SEASON_ID,
            }

        max_l1 = max(s['appear_rate'] for s in l1_stats)
        for s in l1_stats:
            s['score'] = score(s, max_l1)
        l1_stats.sort(key=lambda s: -s['score'])
        l1_stats = l1_stats[:TOP_L1]

        # 对每个L1挖掘L2/L3
        FALLBACK_SUPPORTS = [MIN_SUPPORT, 20, 10]  # 降级阈值

        def mine_next(parent_skel, parent_pool, target_size):
            sub_trans = [list(ds) for ds in parent_pool]
            if len(sub_trans) < 10:
                return []
            te2 = TransactionEncoder()
            te2_arr = te2.fit(sub_trans).transform(sub_trans)
            df2 = pd.DataFrame(te2_arr, columns=te2.columns_)
            # 用最低降级阈值挖掘，后续按实际阈值过滤
            min_sup = min(FALLBACK_SUPPORTS) / len(sub_trans)
            freq2 = fpgrowth(df2, min_support=min_sup, use_colnames=True)
            freq2['length'] = freq2['itemsets'].apply(len)
            cands = [frozenset(r['itemsets']) for _, r in freq2[freq2['length'] == target_size].iterrows()
                     if parent_skel <= frozenset(r['itemsets'])]
            cands.sort(key=lambda x: -sum(deck_stats[ds]['count'] for ds in parent_pool if x <= ds))
            result = []
            for c in cands:
                flex_c = (c - parent_skel) - _rc_set
                if not any(jaccard(flex_c, (ex - parent_skel) - _rc_set) >= L2_OVERLAP for ex in result):
                    result.append(c)
            return result

        def get_effective_support(pool_size):
            """根据数据量选择有效阈值（降级）：取 pool_size 的 20%，但不低于 10"""
            return max(10, int(pool_size * 0.2))

        # 全局独占认领：L2/L3 骨架只能归属一个父节点
        claimed_l2 = set()  # 已被认领的 L2 骨架
        claimed_l3 = set()  # 已被认领的 L3 骨架

        def build_configs(l3_skel, l2_skel, decks):
            merged = []
            visited = set()
            for ds_a in decks:
                if ds_a in visited:
                    continue
                m_cnt = deck_stats[ds_a]['count']
                m_win = deck_stats[ds_a]['wins']
                m_rep = ds_a
                visited.add(ds_a)
                for ds_b in decks:
                    if ds_b in visited:
                        continue
                    if jaccard(ds_a - l3_skel, ds_b - l3_skel) >= 0.50:
                        m_cnt += deck_stats[ds_b]['count']
                        m_win += deck_stats[ds_b]['wins']
                        visited.add(ds_b)
                        if deck_stats[ds_b]['count'] > deck_stats[ds_a]['count']:
                            m_rep = ds_b
                merged.append({
                    'deck_set': m_rep, 'count': m_cnt, 'wins': m_win,
                    'win_rate': m_win / m_cnt if m_cnt else 0.0,
                    'appear_rate': m_cnt / total_runs,
                })
            # 降级：5 -> 3 -> 1
            for cfg_threshold in [min_config_count, 3, 1]:
                filtered = [m for m in merged if m['count'] >= cfg_threshold]
                if filtered:
                    merged = filtered
                    break
            else:
                return []
            max_cfg = max(m['appear_rate'] for m in merged)
            for m in merged:
                m['score'] = score(m, max_cfg)
            merged.sort(key=lambda m: -m['score'])
            configs = []
            for m in merged[:TOP_CFG]:
                ds = m['deck_set']
                items = deck_stats[ds]['items']
                cards = [card_info(it['cardId'], it['cardId'] in l3_skel, it['cardId'] in (l3_skel - l2_skel))
                         for it in items if 'cardId' in it]
                configs.append({
                    'cards': cards, 'count': m['count'], 'wins': m['wins'],
                    'rate': m['win_rate'], 'appearance_rate': m['appear_rate'],
                    'score': m['score'], 'screenshot': deck_stats[ds]['screenshot'],
                    'run_id': deck_stats[ds].get('run_id', ''),
                    'created_at': deck_stats[ds].get('created_at', ''),
                })
            return configs

        layers = []
        for l1_data in l1_stats:
            l1_skel = l1_data['skeleton']
            l1_matched = l1_data['matched_decks']

            l2_cands = mine_next(l1_skel, l1_matched, 3)

            # 先计算所有候选 L2 的 stats 和评分，再按评分排序认领
            l2_scored = []
            eff_sup_l2 = get_effective_support(sum(deck_stats[ds]['count'] for ds in l1_matched))
            for l2_skel in l2_cands:
                if l2_skel in claimed_l2:
                    continue
                st2 = compute_stats(l2_skel, l1_matched)
                if not st2 or st2['count'] < eff_sup_l2:
                    continue
                l2_scored.append((l2_skel, st2))

            if l2_scored:
                max_l2 = max(st['appear_rate'] for _, st in l2_scored)
                for l2_skel, st2 in l2_scored:
                    st2['score'] = score(st2, max_l2)
                l2_scored.sort(key=lambda x: -x[1]['score'])

            l2_list = []
            for l2_skel, st2 in l2_scored[:TOP_L2]:
                claimed_l2.add(l2_skel)

                l3_cands = mine_next(l2_skel, st2['matched_decks'], 4)

                # 先计算所有候选 L3 的 stats 和评分，再按评分排序认领
                l3_scored = []
                eff_sup_l3 = get_effective_support(st2['count'])
                for l3_skel in l3_cands:
                    if l3_skel in claimed_l3:
                        continue
                    st3 = compute_stats(l3_skel, st2['matched_decks'])
                    if not st3 or st3['count'] < eff_sup_l3:
                        continue
                    l3_scored.append((l3_skel, st3))

                if l3_scored:
                    max_l3 = max(st['appear_rate'] for _, st in l3_scored)
                    for l3_skel, st3 in l3_scored:
                        st3['score'] = score(st3, max_l3)
                    l3_scored.sort(key=lambda x: -x[1]['score'])

                l3_list = []
                for l3_skel, st3 in l3_scored[:TOP_L3]:
                    claimed_l3.add(l3_skel)  # 立即认领，不管configs是否为空
                    configs = build_configs(l3_skel, l2_skel, st3['matched_decks'])
                    l3_list.append({
                        'core_cards': [card_info(cid, True, cid in (l3_skel - l2_skel)) for cid in sorted(l3_skel)],
                        'count': st3['count'], 'wins': st3['wins'],
                        'rate': st3['win_rate'], 'appearance_rate': st3['appear_rate'],
                        'score': st3['score'], 'configs': configs,
                    })

                # L3 为空时，直接在 L2 层挂具体配置作为兜底
                if not l3_list:
                    fallback_configs = build_configs(l2_skel, l1_skel, st2['matched_decks'])
                else:
                    fallback_configs = []

                l2_list.append({
                    'core_cards': [card_info(cid, True, cid in (l2_skel - l1_skel)) for cid in sorted(l2_skel)],
                    'count': st2['count'], 'wins': st2['wins'],
                    'rate': st2['win_rate'], 'appearance_rate': st2['appear_rate'],
                    'score': st2['score'], 'l3_variants': l3_list,
                    'configs': fallback_configs,  # L3空时的兜底配置
                })

            # L2 全空的 L1 不展示
            if l2_list:
                layers.append({
                    'core_cards': [card_info(cid, True, False) for cid in sorted(l1_skel)],
                    'count': l1_data['count'], 'wins': l1_data['wins'],
                    'rate': l1_data['win_rate'], 'appearance_rate': l1_data['appear_rate'],
                    'score': l1_data['score'], 'l2_variants': l2_list,
                })


        hero_map = HERO_EN_TO_ZH

        # 将层级挖掘结果同时扁平为“可直接参考的完整阵容”。
        # L1/L2/L3 保留给体系解释，前台默认消费 recommendations，避免用户逐层展开才看到构筑。
        recommendations = []
        seen_configs = set()
        for l1 in layers:
            for l2 in l1.get('l2_variants', []):
                l3_groups = l2.get('l3_variants', [])
                config_groups = [(l3.get('configs', []), l3.get('core_cards', [])) for l3 in l3_groups]
                if not config_groups:  # L3 为空时，使用 L2 的兜底配置。
                    config_groups = [(l2.get('configs', []), l2.get('core_cards', []))]
                for configs, core_cards in config_groups:
                    for cfg in configs:
                        card_ids = tuple(sorted(card.get('cardId', '') for card in cfg.get('cards', [])))
                        if not card_ids or card_ids in seen_configs:
                            continue
                        seen_configs.add(card_ids)
                        _core_ids = {card.get('cardId') for card in core_cards if card.get('cardId')}
                        _cfg_cards = cfg.get('cards', [])
                        recommendations.append({
                            **cfg,
                            'hero': hero,
                            'hero_zh': hero_map.get(hero, hero),
                            'core_cards': core_cards,
                            'core_card_ids': sorted(_core_ids),
                            'variant_cards': [card for card in _cfg_cards if card.get('cardId') not in _core_ids],
                            'run_url': f"https://bazaardb.gg/run/tracker/{cfg.get('run_id')}" if cfg.get('run_id') else '',
                            # 分母是“当前 phase、该职业、含查询卡”的全部 runs。
                            'hero_appearance_rate': cfg.get('count', 0) / total_runs if total_runs else 0.0,
                        })

        result = {
            'hero': hero, 'hero_zh': hero_map.get(hero, hero),
            'layers': layers,
            'recommendations': recommendations,
            'total_runs': total_runs,
            'phase': CURRENT_PHASE,
            'season': RUNS_SEASON_ID,
        }
        _comp_cache[cache_key] = (result, time.time() + _comp_cache_ttl)
        return result


# comp 方法内存缓存 {(hero, n): (result, expire_time)}

    def hero_overview(self,
                     days: int = None,
                     sort_by: str = 'total',
                     rank_filter: str = 'all') -> dict:
        """职业整体概况：所有职业的出场数、胜场数、胜率横向对比"""
        import json as _json
        import time as _time
        from datetime import datetime as _dt, timedelta as _td

        global _hero_overview_cache, _hero_overview_cache_ttl
        _ho_key = (days, sort_by, rank_filter)
        if _ho_key in _hero_overview_cache:
            _cached, _exp = _hero_overview_cache[_ho_key]
            if _time.time() < _exp:
                return _cached

        if not self.conn:
            self.load()

        sql = "SELECT hero, stat_wins FROM runs WHERE season=? AND phase=?"
        params = [RUNS_SEASON_ID, CURRENT_PHASE]
        if rank_filter == 'legendary':
            sql += " AND player_rank='Legendary'"
        if days:
            cutoff = (_dt.utcnow() - _td(days=days)).isoformat()
            sql += " AND created_at >= ?"
            params.append(cutoff)
        rows = self.conn.execute(sql, params).fetchall()

        from collections import defaultdict
        hero_stats = defaultdict(lambda: {'total': 0, 'wins': 0})
        for hero, stat_wins in rows:
            if not hero:
                continue
            hero_stats[hero]['total'] += 1
            if (stat_wins or 0) >= 10:
                hero_stats[hero]['wins'] += 1

        results = []
        hero_map = HERO_EN_TO_ZH
        for hero, stats in hero_stats.items():
            total = stats['total']
            wins = stats['wins']
            rate = wins / total if total > 0 else 0.0
            results.append({
                'hero': hero,
                'hero_zh': hero_map.get(hero, hero),
                'total': total,
                'wins': wins,
                'rate': rate
            })

        if sort_by == 'rate':
            results.sort(key=lambda x: (-x['rate'], -x['total']))
        elif sort_by == 'wins':
            results.sort(key=lambda x: (-x['wins'], -x['rate']))
        else:
            results.sort(key=lambda x: (-x['total'], -x['rate']))

        _ho_result = {
            'heroes': results,
            'total_runs': len(rows),
            'days': days,
            'sort_by': sort_by,
            'rank_filter': rank_filter
        }

        _hero_overview_cache[_ho_key] = (_ho_result, _time.time() + _hero_overview_cache_ttl)
        return _ho_result

_comp_cache: dict = {}
_comp_cache_ttl = 3600  # 1小时

# topcard 方法内存缓存 {(hero, top_n, days, all_phases): (result, expire_time)}
_topcard_cache: dict = {}
_topcard_cache_ttl = 3600  # 1小时

# T 表缓存：ingest 成功时清空当前 phase 的全部组合，首个访问按条件重算。
_card_tier_cache: dict = {}
_card_tier_cache_ttl = 3600  # 1小时

_hero_overview_cache: dict = {}
_hero_overview_cache_ttl = 3600  # 1小时

_client = None

def get_client() -> RunsQuery:
    global _client
    if _client is None:
        _client = RunsQuery()
        _client.load()
    return _client
