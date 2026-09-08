"""
BazaarDB Runs 查询 Web API
端口: 1027
"""
import sys
import os
import time
import json
import sqlite3 as _sqlite3
from datetime import datetime
from collections import defaultdict
from dotenv import load_dotenv
load_dotenv("/opt/qiubot/.env")
from functools import wraps
from flask import Flask, request, jsonify, send_from_directory, send_file, abort, Response

sys.path.insert(0, '/opt/qiubot')
import sys as _sys; _sys.path.insert(0, '/opt/qiubot/web_runs')
from ocr_worker import start_worker, enqueue_run

# 启动 OCR 后台线程
# start_worker()  # 已改为凌晨批量处理

from plugins.bazaar_plugin.runs_query import RunsQuery
from plugins.bazaar_plugin.data_client import RUNS_SEASON_ID, CURRENT_PHASE

INGEST_TOKEN = '2320869dd20357f336a056abc6b095ea615651ceadabf698'


def _mask_ip(ip):
    """IP脱敏：只保留前两段，如 115.238.*.*"""
    if not ip:
        return '*.*.*.*'
    p = str(ip).split('.')
    return f"{p[0]}.{p[1]}.*.*" if len(p) == 4 else ip

app = Flask(__name__, static_folder='static')

# ── card search 全局索引（启动时预热）──
_card_search_index = []

def _build_card_search_index():
    global _card_search_index
    try:
        import sys as _sys2
        _sys2.path.insert(0, '/opt/qiubot')
        from plugins.bazaar_plugin.runs_query import RunsQuery
        rq = RunsQuery()
        rq.load()
        seen = set()
        items = []
        for card_id, info in rq.card_mapping.items():
            if info.get('type', '') == 'other':
                continue
            en_orig = info.get('name', '')
            if not en_orig or en_orig in seen:
                continue
            seen.add(en_orig)
            zh_name = rq.en_to_zh.get(en_orig, '')
            if not zh_name:
                continue
            items.append({'zh': zh_name, 'en': en_orig})
        _card_search_index = items
        print(f'[card_search] 索引构建完成，共 {len(items)} 张卡牌', flush=True)
    except Exception as e:
        print(f'[card_search] 索引构建失败: {e}', flush=True)

import threading as _threading
_threading.Thread(target=_build_card_search_index, daemon=True).start()

# ── Redis winrate 缓存 ──
import redis as _redis
import pickle as _pickle

_redis_client = _redis.Redis(host='localhost', port=6379, db=0)
_WINRATE_CACHE_KEY = f'winrate_cache:{RUNS_SEASON_ID}:{CURRENT_PHASE}'
_winrate_cache_ready = False

def _build_winrate_cache():
    """启动时把当前赛段所有 runs 的 (frozenset(card_ids), stat_wins) 存入 Redis list。"""
    global _winrate_cache_ready
    try:
        import sqlite3 as _sl3, json as _j3
        # 已有缓存则跳过重建（进程重启不需要重建）
        existing = _redis_client.llen(_WINRATE_CACHE_KEY)
        if existing > 1000:
            _winrate_cache_ready = True
            print(f'[winrate_cache] 复用已有缓存，共 {existing} 条', flush=True)
            return
        conn = _sl3.connect('/opt/qiubot/data/bazaar_runs.db', check_same_thread=False)
        rows = conn.execute(
            'SELECT items_json, stat_wins FROM runs WHERE season=? AND phase=?',
            (RUNS_SEASON_ID, CURRENT_PHASE)
        ).fetchall()
        conn.close()
        pipe = _redis_client.pipeline(transaction=False)
        _redis_client.delete(_WINRATE_CACHE_KEY)
        for items_json, stat_wins in rows:
            try:
                items = _j3.loads(items_json)
                card_ids = frozenset(item['cardId'] for item in items if 'cardId' in item)
                pipe.rpush(_WINRATE_CACHE_KEY, _pickle.dumps((card_ids, int(stat_wins or 0))))
            except Exception:
                pass
        pipe.execute()
        _winrate_cache_ready = True
        print(f'[winrate_cache] 构建完成，共 {len(rows)} 条', flush=True)
    except Exception as e:
        print(f'[winrate_cache] 构建失败: {e}', flush=True)

def _winrate_cache_append(items_json: str, stat_wins: int):
    """ingest 新 run 后增量追加到缓存。"""
    try:
        import json as _j4
        items = _j4.loads(items_json)
        card_ids = frozenset(item['cardId'] for item in items if 'cardId' in item)
        _redis_client.rpush(_WINRATE_CACHE_KEY, _pickle.dumps((card_ids, int(stat_wins or 0))))
    except Exception:
        pass

def _winrate_from_cache(card_ids_sets: list, min_wins: int = 10) -> tuple:
    """从 Redis 缓存计算胜率，返回 (total, ten_win)。"""
    total = ten_win = 0
    raw_list = _redis_client.lrange(_WINRATE_CACHE_KEY, 0, -1)
    for raw in raw_list:
        try:
            card_ids, wins = _pickle.loads(raw)
            if all(card_ids & s for s in card_ids_sets):
                total += 1
                if wins >= min_wins:
                    ten_win += 1
        except Exception:
            pass
    return total, ten_win

_threading.Thread(target=_build_winrate_cache, daemon=True).start()



# 在 app.py 开头（imports 后）添加统一埋点中间件和 stats 表

import time
from flask import g

# 扩展 query_log 表，添加 fingerprint 和 duration
# 新增 stats_pv 表记录页面访问
def _init_stats_tables():
    import sqlite3 as _sl
    conn = _sl.connect('/opt/qiubot/data/stats.db')
    
    # API 调用日志（扩展）
    conn.execute('''CREATE TABLE IF NOT EXISTS api_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        endpoint TEXT,
        method TEXT,
        params_json TEXT,
        ip TEXT,
        fingerprint TEXT,
        result_count INTEGER,
        success INTEGER,
        duration_ms INTEGER,
        created_at TEXT
    )''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_api_endpoint ON api_log(endpoint)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_api_time ON api_log(created_at)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_api_fp ON api_log(fingerprint)')
    
    # 页面 PV
    conn.execute('''CREATE TABLE IF NOT EXISTS page_view (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tab TEXT,
        ip TEXT,
        fingerprint TEXT,
        created_at TEXT
    )''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_pv_tab ON page_view(tab)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_pv_time ON page_view(created_at)')
    
    # Feedback 行为
    conn.execute('''CREATE TABLE IF NOT EXISTS feedback_action (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        action TEXT,
        feedback_id INTEGER,
        ip TEXT,
        fingerprint TEXT,
        created_at TEXT
    )''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_fb_action ON feedback_action(action)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_fb_time ON feedback_action(created_at)')

    # 数据清理：保留最近 90 天的 api_log/page_view/feedback_action，query_log 保留 180 天
    conn.execute("DELETE FROM api_log WHERE created_at < datetime('now', '-90 days')")
    conn.execute("DELETE FROM page_view WHERE created_at < datetime('now', '-90 days')")
    conn.execute("DELETE FROM feedback_action WHERE created_at < datetime('now', '-90 days')")
    conn.execute("DELETE FROM query_log WHERE rowid IN (SELECT rowid FROM query_log WHERE created_at < datetime('now', '-180 days'))") if 'query_log' in [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()] else None

    # 赞赏者名单
    conn.execute('''CREATE TABLE IF NOT EXISTS supporters (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        display_name TEXT NOT NULL,
        visible INTEGER NOT NULL DEFAULT 1,
        sort_order INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    )''')

    # 冷知识问答
    conn.execute('''CREATE TABLE IF NOT EXISTS trivia (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        question TEXT NOT NULL,
        description TEXT,
        answer TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        author_name TEXT,
        author_contact TEXT,
        upvotes INTEGER NOT NULL DEFAULT 0,
        downvotes INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        published_at TEXT,
        updated_at TEXT
    )''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_trivia_status ON trivia(status)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_trivia_published_at ON trivia(published_at)')

    conn.execute('''CREATE TABLE IF NOT EXISTS trivia_votes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trivia_id INTEGER NOT NULL,
        fingerprint TEXT NOT NULL,
        vote_type TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(trivia_id, fingerprint)
    )''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_trivia_votes_trivia ON trivia_votes(trivia_id)')

    conn.execute('''CREATE TABLE IF NOT EXISTS announcements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        content TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'draft',
        created_at TEXT NOT NULL,
        updated_at TEXT,
        published_at TEXT
    )''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_announcements_status ON announcements(status)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_announcements_published_at ON announcements(published_at)')

    conn.commit()
    conn.close()

_init_stats_tables()

STATS_DB = '/opt/qiubot/data/stats.db'

@app.before_request
def before_request():
    g.start_time = time.time()
    g.fingerprint = request.headers.get('X-Fingerprint', '')

@app.after_request
def after_request(response):
    if hasattr(g, 'start_time'):
        duration_ms = int((time.time() - g.start_time) * 1000)
        endpoint = request.endpoint
        _user_api_paths = ('/api/runs', '/api/winrate', '/api/partner', '/api/heroes', '/api/suggestions', '/api/card_img', '/api/topcard', '/api/feedback')
        if endpoint and endpoint.startswith('api_') and request.path.startswith(_user_api_paths):
            _log_api_call(
                endpoint=request.path,
                method=request.method,
                params=dict(request.args) or dict(request.form) or {},
                ip=_mask_ip(request.headers.get('X-Forwarded-For', request.remote_addr)),
                fingerprint=g.fingerprint,
                result_count=getattr(g, 'result_count', 0),
                success=response.status_code < 400,
                duration_ms=duration_ms
            )
    # 静态资源缓存头
    if request.path.startswith('/static/'):
        ext = request.path.rsplit('.', 1)[-1].lower()
        if ext in ('png', 'jpg', 'jpeg', 'gif', 'webp', 'ico', 'woff', 'woff2'):
            response.cache_control.max_age = 86400 * 30  # 图片字体 30 天
            response.cache_control.public = True
        elif ext in ('css', 'js'):
            response.cache_control.max_age = 86400 * 7   # CSS/JS 7 天
            response.cache_control.public = True
        elif ext == 'html':
            response.cache_control.no_cache = True        # HTML 不缓存
    return response

def _log_api_call(endpoint, method, params, ip, fingerprint, result_count, success, duration_ms):
    try:
        import sqlite3 as _sl, json as _j
        from datetime import datetime
        conn = _sl.connect(STATS_DB)
        conn.execute(
            'INSERT INTO api_log (endpoint, method, params_json, ip, fingerprint, result_count, success, duration_ms, created_at) VALUES (?,?,?,?,?,?,?,?,?)',
            (endpoint, method, _j.dumps(params, ensure_ascii=False), ip, fingerprint, result_count, 1 if success else 0, duration_ms, datetime.utcnow().isoformat())
        )
        conn.commit()
        conn.close()
    except: pass

def _log_page_view(tab, ip, fingerprint):
    try:
        import sqlite3 as _sl
        from datetime import datetime
        conn = _sl.connect(STATS_DB)
        conn.execute('INSERT INTO page_view (tab, ip, fingerprint, created_at) VALUES (?,?,?,?)',
                     (tab, ip, fingerprint, datetime.utcnow().isoformat()))
        conn.commit()
        conn.close()
    except: pass

def _log_feedback_action(action, feedback_id, ip, fingerprint):
    try:
        import sqlite3 as _sl
        from datetime import datetime
        conn = _sl.connect(STATS_DB)
        conn.execute('INSERT INTO feedback_action (action, feedback_id, ip, fingerprint, created_at) VALUES (?,?,?,?,?)',
                     (action, feedback_id, ip, fingerprint, datetime.utcnow().isoformat()))
        conn.commit()
        conn.close()
    except: pass

# 新增埋点 API
@app.route('/api/track/pv', methods=['POST'])
def api_track_pv():
    data = request.get_json(silent=True) or {}
    tab = data.get('tab', '')
    if tab:
        _log_page_view(tab, _mask_ip(request.headers.get('X-Forwarded-For', request.remote_addr)), g.fingerprint)
    return jsonify({'ok': True})



QUERY_STATS_DB = '/opt/qiubot/data/query_stats.db'

def _init_stats_db():
    conn = _sqlite3.connect(QUERY_STATS_DB)
    conn.execute('''CREATE TABLE IF NOT EXISTS query_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        query_type TEXT,
        params_json TEXT,
        ip TEXT,
        result_count INTEGER,
        success INTEGER,
        created_at TEXT
    )''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_ql_type ON query_log(query_type)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_ql_time ON query_log(created_at)')
    conn.commit()
    conn.close()

def _log_query(query_type, params, ip, result_count, success):
    try:
        conn = _sqlite3.connect(QUERY_STATS_DB)
        conn.execute(
            'INSERT INTO query_log (query_type, params_json, ip, result_count, success, created_at) VALUES (?,?,?,?,?,?)',
            (query_type, json.dumps(params, ensure_ascii=False), ip,
             result_count, 1 if success else 0, datetime.utcnow().isoformat())
        )
        conn.commit()
        conn.close()
    except Exception:
        pass

_init_stats_db()

# ===== 限流：同一 IP 3秒内只能查一次 =====
_rate_limit = defaultdict(float)

def rate_limit(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        ip = _mask_ip(request.headers.get('X-Forwarded-For', request.remote_addr).split(',')[0].strip())
        now = time.time()
        last = _rate_limit.get(ip, 0)
        if now - last < 1:
            return jsonify({'error': '查询太频繁，请稍后再试'}), 429
        _rate_limit[ip] = now
        return f(*args, **kwargs)
    return wrapper


# ===== API 路由 =====

@app.route('/api/runs', methods=['GET'])
@rate_limit
def api_runs():
    hero = request.args.get('hero', '').strip() or None
    cards_raw = request.args.get('cards', '').strip()
    cards = [c.strip() for c in cards_raw.split('+') if c.strip()] or None
    days = request.args.get('days', type=int)
    min_wins = request.args.get('min_wins', default=10, type=int)
    page = request.args.get('page', default=1, type=int)
    rank_filter = request.args.get('rank', 'all')

    try:
        client = RunsQuery()
        client.load()
        if hero:
            resolved = client.resolve_hero(hero)
            if not resolved:
                return jsonify({'error': f'未识别的英雄: {hero}'}), 400
            hero = resolved
        result = client.query(hero=hero, cards=cards, days=days, min_wins=min_wins, page=page, rank_filter=rank_filter)
        ip = _mask_ip(request.headers.get('X-Forwarded-For', request.remote_addr).split(',')[0].strip())
        _log_query('runs', {'hero': hero, 'cards': cards, 'days': days, 'min_wins': min_wins, 'page': page, 'rank': rank_filter},
                   ip, result.get('total', 0), True)
        return jsonify(result)
    except Exception as e:
        ip = _mask_ip(request.headers.get('X-Forwarded-For', request.remote_addr).split(',')[0].strip())
        _log_query('runs', {'hero': hero, 'cards': cards_raw, 'days': days, 'min_wins': min_wins, 'page': page}, ip, 0, False)
        return jsonify({'error': str(e)}), 500


@app.route('/api/winrate', methods=['GET'])
@rate_limit
def api_winrate():
    cards_raw = request.args.get('cards', '').strip()
    hero_raw = request.args.get('hero', '').strip() or None
    days = request.args.get('days', type=int)
    rank_filter = request.args.get('rank', 'all')
    # multi=1 时 cards 为逗号分隔的多张卡，返回数组
    multi = request.args.get('multi', '0') == '1'

    if not cards_raw:
        return jsonify({'error': '请指定至少一张卡牌'}), 400

    try:
        client = RunsQuery()
        client.load()
        hero = client.resolve_hero(hero_raw) if hero_raw else None
        ip = _mask_ip(request.headers.get('X-Forwarded-For', request.remote_addr).split(',')[0].strip())

        # 走 Redis 缓存的条件：无 days/hero/rank 过滤，且缓存已就绪
        use_cache = (_winrate_cache_ready and not days and not hero and rank_filter == 'all')

        if multi:
            card_list = [c.strip() for c in cards_raw.split(',') if c.strip()]
            results = []
            for card in card_list:
                cards = [c.strip() for c in card.split('+') if c.strip()]
                if use_cache:
                    card_ids_sets = []
                    not_found = []
                    card_names = []
                    for cn in cards:
                        ids = client.find_card_ids(cn)
                        if not ids:
                            not_found.append(cn)
                        else:
                            card_ids_sets.append(set(ids))
                            en = client.translate_name(cn)
                            zh = client.get_zh_name(en)
                            card_names.append(zh if zh != en else cn)
                    if card_ids_sets:
                        total, ten_win = _winrate_from_cache(card_ids_sets)
                    else:
                        total, ten_win = 0, 0
                    rate = ten_win / total if total > 0 else 0.0
                    r = {'total': total, 'ten_win': ten_win, 'rate': rate,
                         'card_names': card_names, 'not_found': not_found, 'tag': card}
                else:
                    r = client.winrate(cards=cards, hero=hero, days=days, rank_filter=rank_filter)
                    r['tag'] = card
                results.append(r)
            _log_query('winrate', {'cards': card_list, 'hero': hero_raw, 'days': days, 'rank': rank_filter}, ip, len(results), True)
            return jsonify({'results': results, 'phase': CURRENT_PHASE})
        else:
            cards = [c.strip() for c in cards_raw.split('+') if c.strip()]
            if not cards:
                return jsonify({'error': '请指定至少一张卡牌'}), 400
            result = client.winrate(cards=cards, hero=hero, days=days, rank_filter=rank_filter)
            _log_query('winrate', {'cards': cards, 'hero': hero_raw, 'days': days, 'rank': rank_filter}, ip, result.get('total', 0), True)
            return jsonify(result)
    except Exception as e:
        ip = _mask_ip(request.headers.get('X-Forwarded-For', request.remote_addr).split(',')[0].strip())
        _log_query('winrate', {'cards': cards_raw, 'hero': hero_raw, 'days': days}, ip, 0, False)
        return jsonify({'error': str(e)}), 500


@app.route('/api/partner', methods=['GET'])
@rate_limit
def api_partner():
    card = request.args.get('card', '').strip()
    days = request.args.get('days', type=int)
    rank_filter = request.args.get('rank', 'all')

    if not card:
        return jsonify({'error': '请指定卡牌名称'}), 400

    try:
        client = RunsQuery()
        client.load()
        result = client.partner(card=card, days=days, rank_filter=rank_filter)
        ip = _mask_ip(request.headers.get('X-Forwarded-For', request.remote_addr).split(',')[0].strip())
        _log_query('partner', {'card': card, 'days': days, 'rank': rank_filter}, ip, len(result.get('by_winrate', [])), True)
        return jsonify(result)
    except Exception as e:
        ip = _mask_ip(request.headers.get('X-Forwarded-For', request.remote_addr).split(',')[0].strip())
        _log_query('partner', {'card': card, 'days': days}, ip, 0, False)
        return jsonify({'error': str(e)}), 500


@app.route('/api/heroes', methods=['GET'])
def api_heroes():
    from plugins.bazaar_plugin.runs_query import HERO_ZH_TO_EN
    heroes = [
        {'zh': zh, 'en': en}
        for zh, en in HERO_ZH_TO_EN.items()
        if zh not in {'海盗', '工程师', '法师', '猪', '机甲', '吸血鬼', '兽人'}  # 去重，只保留官方中文名
    ]
    # 补全标准名
    standard = [
        {'zh': '凡妮莎', 'en': 'Vanessa'},
        {'zh': '杜利',   'en': 'Dooley'},
        {'zh': '马克',   'en': 'Mak'},
        {'zh': '皮格',   'en': 'Pygmalien'},
        {'zh': '斯黛拉', 'en': 'Stelle'},
        {'zh': '朱尔斯', 'en': 'Jules'},
        {'zh': '卡诺克', 'en': 'Karnok'},
        {'zh': '双龙',   'en': 'The Dragons'},
    ]
    return jsonify(standard)





@app.route('/api/card_search')
def api_card_search():
    """模糊搜索卡牌名，从预热索引查询，返回中英文名。"""
    q = request.args.get('q', '').strip()
    if not q or len(q) > 40:
        return jsonify([])
    try:
        q_lower = q.lower().replace(' ', '')
        results = []
        for item in _card_search_index:
            zh_norm = item['zh'].replace(' ', '')
            en_norm = item['en'].lower().replace(' ', '')
            if q_lower in zh_norm or q_lower in en_norm:
                results.append(item)
            if len(results) >= 20:
                break

        def sort_key(r):
            zh_n = r['zh'].replace(' ', '')
            en_n = r['en'].lower().replace(' ', '')
            zh_prefix = zh_n.startswith(q_lower) or r['zh'].startswith(q)
            en_prefix = en_n.startswith(q_lower)
            return (0 if zh_prefix else (1 if en_prefix else 2), r['zh'])

        results.sort(key=sort_key)
        return jsonify(results[:10])
    except Exception as e:
        return jsonify([])


@app.route('/api/suggestions')
def api_suggestions():
    try:
        conn = _sqlite3.connect(QUERY_STATS_DB)
        rows = conn.execute(
            "SELECT params_json, query_type FROM query_log WHERE success=1 ORDER BY id DESC LIMIT 500"
        ).fetchall()
        conn.close()

        from collections import Counter
        hero_counter = Counter()
        card_counter = Counter()

        for params_json, qtype in rows:
            try:
                p = json.loads(params_json)
            except Exception:
                continue
            # 英雄
            hero = p.get('hero')
            if hero:
                hero_map = {'Vanessa':'凡妮莎','Dooley':'杜利','Mak':'马克',
                            'Pygmalien':'皮格','Stelle':'斯黛拉','Jules':'朱尔斯','Karnok':'卡诺克','The Dragons':'双龙'}
                hero_counter[hero_map.get(hero, hero)] += 1
            # 卡牌
            cards = p.get('cards') or []
            if isinstance(cards, str):
                cards = [cards]
            for c in cards:
                if c:
                    card_counter[c] += 1
            card = p.get('card')
            if card:
                card_counter[card] += 1

        return jsonify({
            'heroes': [{'name': k, 'count': v} for k, v in hero_counter.most_common(7)],
            'cards': [{'name': k, 'count': v} for k, v in card_counter.most_common(10)],
        })
    except Exception as e:
        return jsonify({'heroes': [], 'cards': []}), 200


@app.route('/api/card_img/<path:tex_name>')
def card_img(tex_name):
    img_dir = '/opt/qiubot/plugins/bazaar_plugin/cache/card_images'
    img_path = os.path.join(img_dir, tex_name + '.png')
    if not os.path.exists(img_path):
        abort(404)
    return send_file(img_path, mimetype='image/png')



@app.route('/api/ingest', methods=['POST'])
def api_ingest():
    """采集脚本专用：批量写入 runs 数据"""
    token = request.headers.get('X-Ingest-Token', '')
    if token != INGEST_TOKEN:
        return jsonify({'error': 'unauthorized'}), 401

    data = request.get_json(silent=True)
    if not data or not isinstance(data, list):
        return jsonify({'error': 'body must be a JSON array'}), 400

    db_path = '/opt/qiubot/data/bazaar_runs.db'
    try:
        conn = __import__('sqlite3').connect(db_path, check_same_thread=False, timeout=30)
        new_count = 0
        for run in data:
            wins = run.get('statWins') or 0
            if wins < 1:
                continue
            try:
                _items_json = __import__('json').dumps(run.get('items', []), ensure_ascii=False)
                try:
                    _card_ids_text = ' '.join(
                        it['cardId'] for it in run.get('items', []) if 'cardId' in it
                    )
                except Exception:
                    _card_ids_text = ''
                conn.execute("""INSERT OR IGNORE INTO runs
                    (id, hero, username, created_at, items_json, skills_json, combats_json,
                     stat_wins, stat_losses, player_rating, player_rating_after,
                     player_rank, player_rank_after, screenshot_url, raw_json, collected_at, season, phase, card_ids_text)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (run['id'], run.get('hero'), run.get('username'),
                     run.get('createdAt'),
                     _items_json,
                     __import__('json').dumps(run.get('skills', []), ensure_ascii=False),
                     __import__('json').dumps(run.get('combats', []), ensure_ascii=False),
                     run.get('statWins'), run.get('statLosses'),
                     run.get('playerRating'), run.get('playerRatingAfter'),
                     run.get('playerRank'), run.get('playerRankAfter'),
                     run.get('screenshotUrl'),
                     __import__('json').dumps(run, ensure_ascii=False),
                     __import__('datetime').datetime.now().isoformat(), RUNS_SEASON_ID, CURRENT_PHASE,
                     _card_ids_text))
                if conn.total_changes > 0:
                    new_count += 1
                    # 增量追加到 Redis winrate 缓存
                    _winrate_cache_append(
                        __import__('json').dumps(run.get('items', []), ensure_ascii=False),
                        run.get('statWins', 0)
                    )
                    # 新 run 加入 OCR 队列识别游戏用户名
                    # enqueue_run(run['id'], run.get('screenshotUrl', ''))  # 已改为凌晨批量处理
            except Exception as e:
                pass
        conn.commit()
        total = conn.execute('SELECT COUNT(*) FROM runs').fetchone()[0]
        conn.close()
        return jsonify({'ok': True, 'new': new_count, 'total': total})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/topcard', methods=['GET'])
def api_topcard():
    from plugins.bazaar_plugin.runs_query import RunsQuery
    hero = request.args.get('hero', '').strip()
    top_n = min(int(request.args.get('top', 5)), 30)
    days = request.args.get('days')
    if days:
        days = int(days)
    all_phases = request.args.get('all_phases') == 'true'
    sort_by = request.args.get('sort_by', 'total')  # total/rate/ten_win
    rank_filter = request.args.get('rank', 'legendary')  # legendary/all
    ip = _mask_ip(request.headers.get('X-Forwarded-For', request.remote_addr))
    try:
        query = RunsQuery()
        result = query.topcard(hero=hero, top_n=top_n, days=days, all_phases=all_phases, sort_by=sort_by, rank_filter=rank_filter)
        _log_query('topcard', {'hero': hero, 'top': top_n, 'days': days, 'all_phases': all_phases, 'sort_by': sort_by, 'rank': rank_filter}, ip, len(result.get('top', [])), True)
        return jsonify(result)
    except Exception as e:
        _log_query('topcard', {'hero': hero}, ip, 0, False)
        return jsonify({'error': str(e)}), 500




@app.route('/api/comp', methods=['GET'])
@rate_limit
def api_comp():
    from plugins.bazaar_plugin.runs_query import RunsQuery, HERO_ZH_TO_EN
    hero_raw = request.args.get('hero', '').strip()
    n = min(max(int(request.args.get('n', 3)), 2), 4)
    top_k = min(int(request.args.get('top', 10)), 20)
    all_phases = request.args.get('all_phases') == 'true'
    sort_by = request.args.get('sort_by', 'total')  # total/rate/ten_win
    rank_filter = request.args.get('rank', 'legendary')  # legendary/all
    ip = _mask_ip(request.headers.get('X-Forwarded-For', request.remote_addr))
    if not hero_raw:
        return jsonify({'error': '请指定职业'}), 400
    try:
        query = RunsQuery()
        hero = query.resolve_hero(hero_raw)
        if not hero:
            return jsonify({'error': f'未知职业: {hero_raw}'}), 400
        result = query.comp(hero=hero, n=n, top_k=top_k, all_phases=all_phases)
        _log_query('comp', {'hero': hero_raw, 'n': n}, ip, len(result.get('groups', [])), True)
        return jsonify(result)
    except Exception as e:
        _log_query('comp', {'hero': hero_raw}, ip, 0, False)
        return jsonify({'error': str(e)}), 500


@app.route('/api/comp/card', methods=['GET'])
@rate_limit
def api_comp_card():
    from plugins.bazaar_plugin.runs_query import RunsQuery, HERO_ZH_TO_EN
    card_raw = request.args.get('card', '').strip()
    hero_raw = request.args.get('hero', '').strip()
    all_phases = request.args.get('all_phases') == 'true'
    rank_filter = request.args.get('rank', 'all')
    ip = _mask_ip(request.headers.get('X-Forwarded-For', request.remote_addr))
    if not card_raw:
        return jsonify({'error': '请指定卡牌名'}), 400
    try:
        query = RunsQuery()
        query.load()
        # 从 card_images.json 查 cardId（支持中英文、去空格精确匹配）
        import json as _json
        _ci = _json.load(open('/opt/qiubot/plugins/bazaar_plugin/cache/card_images.json', encoding='utf-8'))
        _cards = _ci.get('cards', {})
        _name_to_ids = {}
        _zh_to_ids = {}
        for _cid, _info in _cards.items():
            _en = _info.get('internalName', '')
            _zh = _info.get('name', '')
            if _en:
                _name_to_ids.setdefault(_en.lower().replace(' ', ''), []).append(_cid)
            if _zh and _zh != _en:
                _zh_to_ids.setdefault(_zh.lower().replace(' ', ''), []).append(_cid)
        _q = card_raw.lower().replace(' ', '')
        _found = _zh_to_ids.get(_q) or _name_to_ids.get(_q) or []
        if not _found:
            return jsonify({'error': f'未找到卡牌: {card_raw}'}), 404
        required_card = _found[0]
        card_display = _cards.get(required_card, {}).get('name') or card_raw
        # 解析职业（可选）
        hero = None
        if hero_raw:
            hero = query.resolve_hero(hero_raw)
            if not hero:
                return jsonify({'error': f'未知职业: {hero_raw}'}), 400
        # 全职业时遍历所有职业合并结果
        HEROES = ['Vanessa', 'Dooley', 'Mak', 'Pygmalien', 'Stelle', 'Jules', 'Karnok', 'The Dragons']
        heroes_to_query = [hero] if hero else HEROES
        all_layers = []
        all_recommendations = []
        total_runs = 0
        for h in heroes_to_query:
            r = query.comp(
                hero=h,
                all_phases=all_phases,
                rank_filter=rank_filter,
                required_card=required_card,
                min_count=10,
            )
            for layer in r.get('layers', []):
                layer['hero'] = h
            for recommendation in r.get('recommendations', []):
                recommendation['hero'] = h
                recommendation['hero_zh'] = r.get('hero_zh', h)
            all_layers.extend(r.get('layers', []))
            all_recommendations.extend(r.get('recommendations', []))
            total_runs += r.get('total_runs', 0)
        # 按 score 排序，取 top10
        all_layers.sort(key=lambda x: -x.get('score', 0))
        # 统计口径：全职业时所有百分比以当前 phase 内“含查询卡”的 runs 总数为分母。
        for recommendation in all_recommendations:
            recommendation['global_appearance_rate'] = recommendation.get('count', 0) / total_runs if total_runs else 0.0
            recommendation['sample_warning'] = recommendation.get('count', 0) < 10
        all_recommendations.sort(key=lambda x: -x.get('score', 0))
        result = {
            'card': card_display,
            'card_id': required_card,
            'hero': hero_raw or 'all',
            'layers': all_layers[:10],
            'recommendations': all_recommendations[:20],
            'total_runs': total_runs,
            'phase': CURRENT_PHASE,
            'season': RUNS_SEASON_ID,
            'scope': '当前赛季、当前 phase；仅统计包含查询卡的 runs',
        }
        _log_query('comp_card', {'card': card_raw, 'hero': hero_raw}, ip, len(all_recommendations), True)
        return jsonify(result)
    except Exception as e:
        import traceback; traceback.print_exc()
        _log_query('comp_card', {'card': card_raw}, ip, 0, False)
        return jsonify({'error': str(e)}), 500

# ===== Feedback API =====
FEEDBACK_DB = '/opt/qiubot/data/feedback.db'
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), 'static', 'uploads')
os.makedirs(UPLOAD_DIR, exist_ok=True)
ALLOWED_EXT = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

def _fb_conn():
    import sqlite3 as _sl
    c = _sl.connect(FEEDBACK_DB)
    c.row_factory = _sl.Row
    return c

def _allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXT


@app.route('/api/hero_overview', methods=['GET'])
def api_hero_overview():
    from plugins.bazaar_plugin.runs_query import RunsQuery
    days = request.args.get('days')
    if days:
        days = int(days)
    sort_by = request.args.get('sort_by', 'total')  # total/wins/rate
    rank_filter = request.args.get('rank', 'legendary')  # legendary/all
    ip = _mask_ip(request.headers.get('X-Forwarded-For', request.remote_addr))
    try:
        query = RunsQuery()
        result = query.hero_overview(days=days, sort_by=sort_by, rank_filter=rank_filter)
        _log_query('hero_overview', {'days': days, 'sort_by': sort_by, 'rank': rank_filter}, ip, len(result.get('heroes', [])), True)
        return jsonify(result)
    except Exception as e:
        _log_query('hero_overview', {}, ip, 0, False)
        return jsonify({'error': str(e)}), 500


@app.route('/api/feedback', methods=['GET'])
def api_feedback_list():
    page = int(request.args.get('page', 1))
    per = 20
    offset = (page - 1) * per
    try:
        conn = _fb_conn()
        rows = conn.execute(
            'SELECT * FROM feedback ORDER BY likes DESC, created_at DESC LIMIT ? OFFSET ?',
            (per, offset)
        ).fetchall()
        total = conn.execute('SELECT COUNT(*) FROM feedback').fetchone()[0]
        conn.close()
        return jsonify({
            'items': [dict(r) for r in rows],
            'total': total,
            'page': page,
            'pages': (total + per - 1) // per
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/feedback', methods=['POST'])
def api_feedback_post():
    import uuid, werkzeug.utils
    content = request.form.get('content', '').strip()
    contact = request.form.get('contact', '').strip()
    if not content:
        return jsonify({'error': '内容不能为空'}), 400
    if len(content) > 2000:
        return jsonify({'error': '内容不能超过2000字'}), 400

    image_path = None
    file = request.files.get('image')
    if file and file.filename and _allowed_file(file.filename):
        ext = file.filename.rsplit('.', 1)[1].lower()
        fname = str(uuid.uuid4()) + '.' + ext
        file.save(os.path.join(UPLOAD_DIR, fname))
        image_path = '/static/uploads/' + fname

    try:
        conn = _fb_conn()
        cur = conn.execute(
            'INSERT INTO feedback (content, image_path, contact) VALUES (?, ?, ?)',
            (content, image_path, contact or None)
        )
        fid = cur.lastrowid
        conn.commit()
        row = conn.execute('SELECT * FROM feedback WHERE id=?', (fid,)).fetchone()
        conn.close()
        _log_feedback_action('submit', fid, _mask_ip(request.headers.get('X-Forwarded-For', request.remote_addr)), g.fingerprint)
        return jsonify(dict(row)), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/feedback/<int:fid>/like', methods=['POST'])
def api_feedback_like(fid):
    try:
        conn = _fb_conn()
        conn.execute('UPDATE feedback SET likes = likes + 1 WHERE id = ?', (fid,))
        conn.commit()
        likes = conn.execute('SELECT likes FROM feedback WHERE id = ?', (fid,)).fetchone()
        conn.close()
        if not likes:
            return jsonify({'error': 'not found'}), 404
        _log_feedback_action('like', fid, _mask_ip(request.headers.get('X-Forwarded-For', request.remote_addr)), g.fingerprint)
        return jsonify({'likes': likes[0]})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/feedback/<int:fid>/comments', methods=['GET'])
def api_comments_get(fid):
    try:
        conn = _fb_conn()
        rows = conn.execute(
            'SELECT * FROM comments WHERE feedback_id = ? ORDER BY created_at ASC', (fid,)
        ).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/feedback/<int:fid>/comments', methods=['POST'])
def api_comments_post(fid):
    data = request.get_json(silent=True) or {}
    content = (data.get('content') or '').strip()
    parent_id = data.get('parent_id')
    if not content:
        return jsonify({'error': '评论不能为空'}), 400
    if len(content) > 500:
        return jsonify({'error': '评论不能超过500字'}), 400
    try:
        conn = _fb_conn()
        exists = conn.execute('SELECT id FROM feedback WHERE id=?', (fid,)).fetchone()
        if not exists:
            conn.close()
            return jsonify({'error': 'not found'}), 404
        cur = conn.execute(
            'INSERT INTO comments (feedback_id, parent_id, content) VALUES (?, ?, ?)',
            (fid, parent_id or None, content)
        )
        cid = cur.lastrowid
        conn.commit()
        row = conn.execute('SELECT * FROM comments WHERE id=?', (cid,)).fetchone()
        conn.close()
        return jsonify(dict(row)), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/stats/overview', methods=['GET'])
@app.route('/api/stats/overview', methods=['GET'])
def api_stats_overview():
    """数据看板总览"""
    import sqlite3 as _sl
    days = int(request.args.get('days', 7))
    try:
        conn = _sl.connect(STATS_DB)
        # cutoff 按北京时间自然日计算（UTC+8），避免滚动窗口导致跨天数据不一致
        import datetime as _dt2
        _now_bj = datetime.utcnow() + _dt2.timedelta(hours=8)
        _today_bj = _now_bj.replace(hour=0, minute=0, second=0, microsecond=0)
        _cutoff_bj = _today_bj - _dt2.timedelta(days=days - 1)
        # 转回 UTC 存入 cutoff
        cutoff = (_cutoff_bj - _dt2.timedelta(hours=8)).isoformat()

        # API 总调用量
        total_calls = conn.execute('SELECT COUNT(*) FROM api_log WHERE created_at >= ?', (cutoff,)).fetchone()[0]
        # UV（独立 fingerprint）
        uv = conn.execute('SELECT COUNT(DISTINCT fingerprint) FROM api_log WHERE created_at >= ? AND fingerprint != ""', (cutoff,)).fetchone()[0]
        # UV by IP（未带 fingerprint 的）
        uv_ip = conn.execute('SELECT COUNT(DISTINCT ip) FROM api_log WHERE created_at >= ? AND fingerprint = ""', (cutoff,)).fetchone()[0]
        total_uv = uv + uv_ip

        # PV by tab
        tab_pv = [{'tab': r[0], 'cnt': r[1]} for r in conn.execute(
            'SELECT tab, COUNT(*) as cnt FROM page_view WHERE created_at >= ? GROUP BY tab ORDER BY cnt DESC', (cutoff,)
        ).fetchall()]

        # API 调用分布
        api_dist = [{'endpoint': r[0], 'cnt': r[1], 'avg_ms': r[2], 'errors': r[3]} for r in conn.execute(
            'SELECT endpoint, COUNT(*) as cnt, AVG(duration_ms) as avg_ms, SUM(CASE WHEN success=0 THEN 1 ELSE 0 END) as errors FROM api_log WHERE created_at >= ? GROUP BY endpoint ORDER BY cnt DESC', (cutoff,)
        ).fetchall()]

        # 每日 API 调用趋势（按天，北京时间 UTC+8）
        daily = [{'day': r[0], 'cnt': r[1], 'uv': r[2]} for r in conn.execute(
            "SELECT DATE(created_at, '+8 hours') as day, COUNT(*) as cnt, COUNT(DISTINCT COALESCE(NULLIF(fingerprint,''), ip)) as uv FROM api_log WHERE created_at >= ? GROUP BY day ORDER BY day", (cutoff,)
        ).fetchall()]

        # Feedback 行为统计
        fb_actions = {r[0]: r[1] for r in conn.execute(
            'SELECT action, COUNT(*) as cnt FROM feedback_action WHERE created_at >= ? GROUP BY action', (cutoff,)
        ).fetchall()}

        conn.close()

        # 热门查询卡牌（从旧库）
        # winrate 写入的是 params.cards（数组），partner 写入的是 params.card（单值），
        # 两种 query_type 的 key 不同，需分别取值再合并统计，否则 partner 记录会被
        # json_extract('$.cards') 取成 NULL，聚合出一条计数最高的空分组，显示为 "-"
        old_conn = _sl.connect('/opt/qiubot/data/query_stats.db')
        hot_cards = [{'cards': r[0], 'cnt': r[1]} for r in old_conn.execute(
            """SELECT TRIM(REPLACE(REPLACE(REPLACE(
                 COALESCE(json_extract(params_json,'$.cards'), json_extract(params_json,'$.card')),
               '[',''), ']',''), '"','')) as cards, COUNT(*) as cnt
               FROM query_log WHERE created_at >= ? AND query_type IN ('winrate','partner') AND success=1
               AND cards IS NOT NULL
               GROUP BY cards ORDER BY cnt DESC LIMIT 10""", (cutoff,)
        ).fetchall()]
        old_conn.close()

        return jsonify({
            'period_days': days,
            'total_calls': total_calls,
            'total_uv': total_uv,
            'tab_pv': tab_pv,
            'api_dist': api_dist,
            'daily': daily,
            'fb_actions': fb_actions,
            'hot_cards': hot_cards,
        })
    except Exception as e:
        import traceback
        return jsonify({'error': str(e), 'trace': traceback.format_exc()}), 500

@app.route('/')
@app.route('/runs')
def runs_page():
    return send_from_directory('static', 'runs.html')

@app.route('/winrate')
def winrate_page():
    return send_from_directory('static', 'winrate.html')

@app.route('/partner')
def partner_page():
    return send_from_directory('static', 'partner.html')

@app.route('/topcard')
def topcard_page():
    return send_from_directory('static', 'topcard.html')

@app.route('/feedback')
def feedback_page():
    return send_from_directory('static', 'feedback.html')

@app.route('/support')
def support():
    return send_from_directory('static', 'support.html')

@app.route('/trivia')
def trivia():
    return send_from_directory('static', 'trivia.html')

# ===== Supporters API =====

@app.route('/api/supporters', methods=['GET'])
def get_supporters():
    """公开接口：返回 visible=1 的支持者列表"""
    import sqlite3 as _sl
    conn = _sl.connect('/opt/qiubot/data/stats.db')
    rows = conn.execute(
        'SELECT id, display_name, sort_order FROM supporters WHERE visible=1 ORDER BY sort_order ASC, created_at ASC'
    ).fetchall()
    conn.close()
    return jsonify({'supporters': [{'id': r[0], 'displayName': r[1], 'order': r[2]} for r in rows]})

@app.route('/api/admin/supporters', methods=['GET'])
def admin_list_supporters():
    """管理接口：返回全部支持者（含隐藏）"""
    auth = request.authorization
    if not auth or auth.password != os.getenv('STATS_PASSWORD', ''):
        return Response('Unauthorized', 401, {'WWW-Authenticate': 'Basic realm="BazaarQiuBot Admin"'})
    import sqlite3 as _sl
    conn = _sl.connect('/opt/qiubot/data/stats.db')
    rows = conn.execute(
        'SELECT id, display_name, visible, sort_order, created_at FROM supporters ORDER BY sort_order ASC, created_at ASC'
    ).fetchall()
    conn.close()
    return jsonify({'supporters': [
        {'id': r[0], 'displayName': r[1], 'visible': bool(r[2]), 'order': r[3], 'createdAt': r[4]}
        for r in rows
    ]})

@app.route('/api/admin/supporters', methods=['POST'])
def admin_add_supporter():
    """新增支持者"""
    auth = request.authorization
    if not auth or auth.password != os.getenv('STATS_PASSWORD', ''):
        return Response('Unauthorized', 401, {'WWW-Authenticate': 'Basic realm="BazaarQiuBot Admin"'})
    data = request.get_json() or {}
    name = (data.get('displayName') or '').strip()
    if not name:
        return jsonify({'error': 'displayName 不能为空'}), 400
    visible = 1 if data.get('visible', True) else 0
    order = int(data.get('order', 0))
    import sqlite3 as _sl
    from datetime import datetime
    conn = _sl.connect('/opt/qiubot/data/stats.db')
    cur = conn.execute(
        'INSERT INTO supporters (display_name, visible, sort_order, created_at) VALUES (?,?,?,?)',
        (name, visible, order, datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    )
    new_id = cur.lastrowid
    conn.commit()
    conn.close()
    return jsonify({'id': new_id, 'displayName': name, 'visible': bool(visible), 'order': order}), 201

@app.route('/api/admin/supporters/<int:sid>', methods=['PUT'])
def admin_update_supporter(sid):
    """编辑支持者"""
    auth = request.authorization
    if not auth or auth.password != os.getenv('STATS_PASSWORD', ''):
        return Response('Unauthorized', 401, {'WWW-Authenticate': 'Basic realm="BazaarQiuBot Admin"'})
    data = request.get_json() or {}
    import sqlite3 as _sl
    conn = _sl.connect('/opt/qiubot/data/stats.db')
    row = conn.execute('SELECT id FROM supporters WHERE id=?', (sid,)).fetchone()
    if not row:
        conn.close()
        return jsonify({'error': '不存在'}), 404
    name = (data.get('displayName') or '').strip()
    if not name:
        conn.close()
        return jsonify({'error': 'displayName 不能为空'}), 400
    visible = 1 if data.get('visible', True) else 0
    order = int(data.get('order', 0))
    conn.execute(
        'UPDATE supporters SET display_name=?, visible=?, sort_order=? WHERE id=?',
        (name, visible, order, sid)
    )
    conn.commit()
    conn.close()
    return jsonify({'id': sid, 'displayName': name, 'visible': bool(visible), 'order': order})

@app.route('/api/admin/supporters/<int:sid>', methods=['DELETE'])
def admin_delete_supporter(sid):
    """删除支持者"""
    auth = request.authorization
    if not auth or auth.password != os.getenv('STATS_PASSWORD', ''):
        return Response('Unauthorized', 401, {'WWW-Authenticate': 'Basic realm="BazaarQiuBot Admin"'})
    import sqlite3 as _sl
    conn = _sl.connect('/opt/qiubot/data/stats.db')
    conn.execute('DELETE FROM supporters WHERE id=?', (sid,))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

# ===== Trivia API =====

@app.route('/api/trivia', methods=['GET'])
def get_trivia():
    """公开接口：返回已发布的冷知识列表"""
    import sqlite3 as _sl
    conn = _sl.connect('/opt/qiubot/data/stats.db')
    # 排序：置顶 > 热度(upvotes-downvotes) > 时间倒序
    rows = conn.execute('''
        SELECT id, question, description, answer, upvotes, downvotes, published_at, updated_at
        FROM trivia 
        WHERE status IN ('published', 'pinned')
        ORDER BY 
            CASE status WHEN 'pinned' THEN 0 ELSE 1 END,
            (upvotes - downvotes) DESC,
            published_at DESC
    ''').fetchall()
    conn.close()
    return jsonify({'trivia': [
        {
            'id': r[0], 'question': r[1], 'description': r[2], 'answer': r[3],
            'upvotes': r[4], 'downvotes': r[5], 'publishedAt': r[6], 'updatedAt': r[7]
        }
        for r in rows
    ]})

@app.route('/api/trivia', methods=['POST'])
def submit_trivia():
    """用户提交问题"""
    data = request.get_json() or {}
    question = (data.get('question') or '').strip()
    if not question:
        return jsonify({'error': '问题不能为空'}), 400
    description = (data.get('description') or '').strip()
    author_name = (data.get('authorName') or '').strip()
    author_contact = (data.get('authorContact') or '').strip()
    
    import sqlite3 as _sl
    from datetime import datetime
    conn = _sl.connect('/opt/qiubot/data/stats.db')
    cur = conn.execute(
        'INSERT INTO trivia (question, description, author_name, author_contact, status, created_at) VALUES (?,?,?,?,?,?)',
        (question, description, author_name, author_contact, 'pending', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    )
    new_id = cur.lastrowid
    conn.commit()
    conn.close()
    return jsonify({'id': new_id, 'message': '提交成功，等待审核'}), 201

@app.route('/api/trivia/<int:tid>/vote', methods=['POST'])
def vote_trivia(tid):
    """点赞/点踩"""
    data = request.get_json() or {}
    vote_type = data.get('voteType')
    if vote_type not in ('upvote', 'downvote'):
        return jsonify({'error': '无效的投票类型'}), 400
    
    fingerprint = data.get('fingerprint', '')
    if not fingerprint:
        return jsonify({'error': '缺少指纹'}), 400
    
    import sqlite3 as _sl
    from datetime import datetime
    conn = _sl.connect('/opt/qiubot/data/stats.db')
    
    # 检查是否已投票
    existing = conn.execute(
        'SELECT vote_type FROM trivia_votes WHERE trivia_id=? AND fingerprint=?',
        (tid, fingerprint)
    ).fetchone()
    
    if existing:
        old_type = existing[0]
        if old_type == vote_type:
            conn.close()
            return jsonify({'message': '已投过此票'}), 200
        # 改票：撤销旧票，投新票
        conn.execute('DELETE FROM trivia_votes WHERE trivia_id=? AND fingerprint=?', (tid, fingerprint))
        if old_type == 'upvote':
            conn.execute('UPDATE trivia SET upvotes = upvotes - 1 WHERE id=?', (tid,))
        else:
            conn.execute('UPDATE trivia SET downvotes = downvotes - 1 WHERE id=?', (tid,))
    
    # 记录新投票
    conn.execute(
        'INSERT INTO trivia_votes (trivia_id, fingerprint, vote_type, created_at) VALUES (?,?,?,?)',
        (tid, fingerprint, vote_type, datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    )
    if vote_type == 'upvote':
        conn.execute('UPDATE trivia SET upvotes = upvotes + 1 WHERE id=?', (tid,))
    else:
        conn.execute('UPDATE trivia SET downvotes = downvotes + 1 WHERE id=?', (tid,))
    
    conn.commit()
    # 返回最新计数
    row = conn.execute('SELECT upvotes, downvotes FROM trivia WHERE id=?', (tid,)).fetchone()
    conn.close()
    return jsonify({'upvotes': row[0] if row else 0, 'downvotes': row[1] if row else 0})

@app.route('/api/admin/trivia', methods=['GET'])
def admin_list_trivia():
    """管理接口：返回全部冷知识（含待审核）"""
    auth = request.authorization
    if not auth or auth.password != os.getenv('STATS_PASSWORD', ''):
        return Response('Unauthorized', 401, {'WWW-Authenticate': 'Basic realm="BazaarQiuBot Admin"'})
    import sqlite3 as _sl
    conn = _sl.connect('/opt/qiubot/data/stats.db')
    rows = conn.execute('''
        SELECT id, question, description, answer, status, author_name, author_contact, 
               upvotes, downvotes, created_at, published_at, updated_at
        FROM trivia 
        ORDER BY 
            CASE status WHEN 'pinned' THEN 0 WHEN 'published' THEN 1 ELSE 2 END,
            created_at DESC
    ''').fetchall()
    conn.close()
    return jsonify({'trivia': [
        {
            'id': r[0], 'question': r[1], 'description': r[2], 'answer': r[3],
            'status': r[4], 'authorName': r[5], 'authorContact': r[6],
            'upvotes': r[7], 'downvotes': r[8],
            'createdAt': r[9], 'publishedAt': r[10], 'updatedAt': r[11]
        }
        for r in rows
    ]})

@app.route('/api/admin/trivia/<int:tid>', methods=['PUT'])
def admin_update_trivia(tid):
    """管理员编辑/审核/置顶"""
    auth = request.authorization
    if not auth or auth.password != os.getenv('STATS_PASSWORD', ''):
        return Response('Unauthorized', 401, {'WWW-Authenticate': 'Basic realm="BazaarQiuBot Admin"'})
    data = request.get_json() or {}
    
    import sqlite3 as _sl
    from datetime import datetime
    conn = _sl.connect('/opt/qiubot/data/stats.db')
    row = conn.execute('SELECT id, status FROM trivia WHERE id=?', (tid,)).fetchone()
    if not row:
        conn.close()
        return jsonify({'error': '不存在'}), 404
    
    old_status = row[1]
    question = data.get('question')
    description = data.get('description')
    answer = data.get('answer')
    status = data.get('status')
    
    updates = []
    params = []
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    if question is not None:
        updates.append('question=?')
        params.append(question.strip())
    if description is not None:
        updates.append('description=?')
        params.append(description.strip())
    if answer is not None:
        updates.append('answer=?')
        params.append(answer.strip())
        updates.append('updated_at=?')
        params.append(now)
    if status and status in ('pending', 'published', 'pinned'):
        updates.append('status=?')
        params.append(status)
        # 首次发布时记录时间
        if old_status == 'pending' and status in ('published', 'pinned'):
            updates.append('published_at=?')
            params.append(now)
    
    if updates:
        params.append(tid)
        conn.execute(f'UPDATE trivia SET {", ".join(updates)} WHERE id=?', tuple(params))
        conn.commit()
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/admin/trivia/<int:tid>', methods=['DELETE'])
def admin_delete_trivia(tid):
    """删除冷知识"""
    auth = request.authorization
    if not auth or auth.password != os.getenv('STATS_PASSWORD', ''):
        return Response('Unauthorized', 401, {'WWW-Authenticate': 'Basic realm="BazaarQiuBot Admin"'})
    import sqlite3 as _sl
    conn = _sl.connect('/opt/qiubot/data/stats.db')
    conn.execute('DELETE FROM trivia WHERE id=?', (tid,))
    conn.execute('DELETE FROM trivia_votes WHERE trivia_id=?', (tid,))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

# ===== Announcement API =====

@app.route('/api/announcement/latest', methods=['GET'])
def get_latest_announcement():
    """公开接口：返回最新已发布公告"""
    import sqlite3 as _sl
    conn = _sl.connect('/opt/qiubot/data/stats.db')
    row = conn.execute(
        "SELECT id, title, content, published_at FROM announcements WHERE status='published' ORDER BY published_at DESC LIMIT 1"
    ).fetchone()
    conn.close()
    if not row:
        return jsonify({'announcement': None})
    return jsonify({'announcement': {
        'id': row[0], 'title': row[1], 'content': row[2], 'publishedAt': row[3]
    }})

@app.route('/api/admin/announcements', methods=['GET'])
def admin_list_announcements():
    """管理接口：返回所有公告"""
    auth = request.authorization
    if not auth or auth.password != os.getenv('STATS_PASSWORD', ''):
        return Response('Unauthorized', 401, {'WWW-Authenticate': 'Basic realm="BazaarQiuBot Admin"'})
    import sqlite3 as _sl
    conn = _sl.connect('/opt/qiubot/data/stats.db')
    rows = conn.execute(
        'SELECT id, title, content, status, created_at, published_at FROM announcements ORDER BY created_at DESC'
    ).fetchall()
    conn.close()
    return jsonify({'announcements': [
        {'id': r[0], 'title': r[1], 'content': r[2], 'status': r[3], 'createdAt': r[4], 'publishedAt': r[5]}
        for r in rows
    ]})

@app.route('/api/admin/announcements', methods=['POST'])
def admin_create_announcement():
    """管理接口：新增公告"""
    auth = request.authorization
    if not auth or auth.password != os.getenv('STATS_PASSWORD', ''):
        return Response('Unauthorized', 401, {'WWW-Authenticate': 'Basic realm="BazaarQiuBot Admin"'})
    import sqlite3 as _sl
    data = request.get_json() or {}
    title = (data.get('title') or '').strip()
    content = (data.get('content') or '').strip()
    if not title or not content:
        return jsonify({'error': 'title 和 content 不能为空'}), 400
    status = data.get('status', 'draft')
    if status not in ('draft', 'published'):
        status = 'draft'
    now = __import__('datetime').datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    published_at = now if status == 'published' else None
    conn = _sl.connect('/opt/qiubot/data/stats.db')
    cur = conn.execute(
        'INSERT INTO announcements (title, content, status, created_at, published_at) VALUES (?,?,?,?,?)',
        (title, content, status, now, published_at)
    )
    ann_id = cur.lastrowid
    conn.commit()
    conn.close()
    return jsonify({'id': ann_id, 'title': title, 'content': content, 'status': status, 'createdAt': now, 'publishedAt': published_at})

@app.route('/api/admin/announcements/<int:aid>', methods=['PUT'])
def admin_update_announcement(aid):
    """管理接口：更新公告"""
    auth = request.authorization
    if not auth or auth.password != os.getenv('STATS_PASSWORD', ''):
        return Response('Unauthorized', 401, {'WWW-Authenticate': 'Basic realm="BazaarQiuBot Admin"'})
    import sqlite3 as _sl
    data = request.get_json() or {}
    conn = _sl.connect('/opt/qiubot/data/stats.db')
    row = conn.execute('SELECT id, title, content, status, published_at FROM announcements WHERE id=?', (aid,)).fetchone()
    if not row:
        conn.close()
        return jsonify({'error': '公告不存在'}), 404
    title = (data.get('title') or row[1]).strip()
    content = (data.get('content') or row[2]).strip()
    status = data.get('status', row[3])
    if status not in ('draft', 'published'):
        status = row[3]
    now = __import__('datetime').datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    published_at = row[4]
    if status == 'published' and not published_at:
        published_at = now
    elif status == 'draft':
        published_at = None
    conn.execute(
        'UPDATE announcements SET title=?, content=?, status=?, updated_at=?, published_at=? WHERE id=?',
        (title, content, status, now, published_at, aid)
    )
    conn.commit()
    conn.close()
    return jsonify({'id': aid, 'title': title, 'content': content, 'status': status, 'updatedAt': now, 'publishedAt': published_at})

@app.route('/api/admin/announcements/<int:aid>', methods=['DELETE'])
def admin_delete_announcement(aid):
    """管理接口：删除公告"""
    auth = request.authorization
    if not auth or auth.password != os.getenv('STATS_PASSWORD', ''):
        return Response('Unauthorized', 401, {'WWW-Authenticate': 'Basic realm="BazaarQiuBot Admin"'})
    import sqlite3 as _sl
    conn = _sl.connect('/opt/qiubot/data/stats.db')
    conn.execute('DELETE FROM announcements WHERE id=?', (aid,))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

# ===== Admin Stats =====

@app.route('/admin/stats')# ===== Admin Stats =====

@app.route('/admin/stats')
def stats_dashboard():
    auth = request.authorization
    if not auth or auth.password != os.getenv('STATS_PASSWORD', ''):
        return Response(
            'Unauthorized',
            401,
            {'WWW-Authenticate': 'Basic realm="BazaarQiuBot Admin"'}
        )
    return send_from_directory('static', 'stats.html')



@app.errorhandler(404)
def not_found(e):
    if request.path.startswith('/api/'):
        return jsonify({'error': 'API endpoint not found'}), 404
    return '<!DOCTYPE html><html><head><meta charset="UTF-8"><title>404 - BazaarQiuBot</title><style>body{font-family:sans-serif;background:#0a0e1a;color:#e5e7eb;text-align:center;padding:100px 20px}h1{color:#f59e0b;font-size:72px;margin:0}p{font-size:18px;margin:20px 0}a{color:#60a5fa;text-decoration:none}</style></head><body><h1>404</h1><p>页面未找到</p><a href="/">返回首页</a></body></html>', 404

@app.errorhandler(500)
def internal_error(e):
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Internal server error'}), 500
    return '<!DOCTYPE html><html><head><meta charset="UTF-8"><title>500 - BazaarQiuBot</title><style>body{font-family:sans-serif;background:#0a0e1a;color:#e5e7eb;text-align:center;padding:100px 20px}h1{color:#ef4444;font-size:72px;margin:0}p{font-size:18px;margin:20px 0}a{color:#60a5fa;text-decoration:none}</style></head><body><h1>500</h1><p>服务器内部错误，请稍后重试</p><a href="/">返回首页</a></body></html>', 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=1027, debug=False)
