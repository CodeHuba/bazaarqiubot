"""Anonymous, bounded library interactions, separate from request/search counters."""
import hashlib
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from flask import Blueprint, Response, jsonify, request

ACTIONS = {
    'view': '打开图鉴', 'search': '搜索卡牌', 'filter': '调整筛选或排序',
    'details': '查看卡牌详情', 'load_more': '加载更多卡牌', 'builder_open': '展开构筑',
    'build_add': '加入物品或技能', 'build_move': '调整物品位置', 'build_update': '修改品质或附魔',
    'build_remove': '移除卡牌', 'build_clear': '清空构筑', 'build_undo': '撤销', 'build_redo': '重做',
    'draft_open': '打开草稿栏', 'draft_save': '成功保存草稿', 'draft_load': '载入草稿',
    'build_new': '新建构筑', 'export_start': '开始导出图片', 'export_success': '导出图片成功',
    'export_error': '导出图片失败',
}
BUILD_ACTIONS = {'build_add', 'build_move', 'build_update', 'build_remove', 'build_clear',
                 'build_undo', 'build_redo', 'draft_save', 'build_new'}
DETAILS = {
    'filter': {'hero', 'type', 'tag', 'size', 'quality', 'merchant', 'common', 'hidden', 'sort', 'reset'},
    'details': {'item', 'skill'}, 'build_add': {'item', 'skill'}, 'draft_save': {'manual', 'auto'},
    'export_success': {'complete', 'missing_art'},
}
TOKEN = re.compile(r'^[A-Za-z0-9_-]{8,128}$')
BJ = timezone(timedelta(hours=8))


@contextmanager
def connect(path):
    conn = sqlite3.connect(path, timeout=.5)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def init_library_analytics(path):
    with connect(path) as conn:
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS library_event (
                id TEXT PRIMARY KEY, visitor TEXT NOT NULL, session TEXT NOT NULL,
                action TEXT NOT NULL, detail TEXT NOT NULL, duration_ms INTEGER,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_library_time ON library_event(created_at);
            CREATE INDEX IF NOT EXISTS idx_library_visitor_time ON library_event(visitor,created_at);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_library_view_session
                ON library_event(visitor,session) WHERE action='view';
            CREATE TABLE IF NOT EXISTS library_meta (key TEXT PRIMARY KEY,value TEXT NOT NULL);
        ''')
        conn.execute('INSERT OR IGNORE INTO library_meta VALUES (?,?)', ('tracking_since', now_utc().isoformat()))


def record_event(path, payload, fingerprint):
    if not isinstance(payload, dict) or set(payload)-{'id', 'session', 'action', 'detail', 'duration_ms'}:
        raise ValueError('无效记录')
    event_id, session, action = (payload.get(k) for k in ('id', 'session', 'action'))
    if not all(isinstance(v, str) and TOKEN.fullmatch(v) for v in (event_id, session, fingerprint)):
        raise ValueError('无效匿名标识')
    if not isinstance(action, str) or action not in ACTIONS:
        raise ValueError('无效操作')
    detail = payload.get('detail', '')
    if not isinstance(detail, str) or detail not in DETAILS.get(action, {''}):
        raise ValueError('无效明细')
    duration = payload.get('duration_ms')
    if duration is not None and (type(duration) is not int or not 0 <= duration <= 3600000):
        raise ValueError('无效耗时')
    if action not in {'export_success', 'export_error'}:
        duration = None
    visitor = hashlib.sha256(fingerprint.encode()).hexdigest()
    now = now_utc()
    with connect(path) as conn:
        conn.execute('BEGIN IMMEDIATE')
        old = conn.execute('SELECT visitor FROM library_event WHERE id=?', (event_id,)).fetchone()
        if old:
            if old['visitor'] != visitor:
                raise ValueError('记录编号已使用')
            return False
        if action == 'view' and conn.execute("SELECT 1 FROM library_event WHERE visitor=? AND session=? AND action='view'", (visitor, session)).fetchone():
            return False
        count = conn.execute('SELECT COUNT(*) FROM library_event WHERE visitor=? AND created_at>=?',
                             (visitor, (now-timedelta(minutes=1)).isoformat())).fetchone()[0]
        if count >= 240:
            raise OverflowError('记录过于频繁')
        conn.execute('INSERT INTO library_event VALUES (?,?,?,?,?,?,?)',
                     (event_id, visitor, session, action, detail, duration, now.isoformat()))
        day = now.date().isoformat()
        previous = conn.execute("SELECT value FROM library_meta WHERE key='cleanup_day'").fetchone()
        if not previous or previous['value'] != day:
            conn.execute('DELETE FROM library_event WHERE created_at<?', ((now-timedelta(days=90)).isoformat(),))
            conn.execute("INSERT OR REPLACE INTO library_meta VALUES ('cleanup_day',?)", (day,))
    return True


def library_report(path, days, now=None):
    local = (now or datetime.now(timezone.utc)).astimezone(BJ)
    start = datetime.combine(local.date()-timedelta(days=days-1), datetime.min.time(), BJ)
    end = datetime.combine(local.date()+timedelta(days=1), datetime.min.time(), BJ)
    bounds = tuple(d.astimezone(timezone.utc).replace(tzinfo=None).isoformat() for d in (start, end))
    with connect(path) as conn:
        conn.execute('BEGIN')
        rows = [dict(r) for r in conn.execute('''SELECT action,COUNT(*) count,COUNT(DISTINCT visitor) visitors,
            AVG(duration_ms) avg_ms FROM library_event WHERE created_at>=? AND created_at<? GROUP BY action''', bounds)]
        visitors = {r[0] for r in conn.execute('SELECT DISTINCT visitor FROM library_event WHERE created_at>=? AND created_at<?', bounds)}
        placeholders = ','.join('?' for _ in BUILD_ACTIONS)
        builders = conn.execute(f'''SELECT COUNT(DISTINCT visitor) FROM library_event
            WHERE created_at>=? AND created_at<? AND action IN ({placeholders})''', (*bounds, *sorted(BUILD_ACTIONS))).fetchone()[0]
        site_visitors = None
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='acquisition_view'").fetchone():
            site = {r[0] for r in conn.execute('''SELECT DISTINCT v.visitor FROM acquisition_visit v
                JOIN acquisition_view p ON p.visit_id=v.id WHERE p.created_at>=? AND p.created_at<?''', bounds)}
            site_visitors = len(site | visitors)
        daily = [dict(r) for r in conn.execute(f'''SELECT DATE(created_at,'+8 hours') day,
            SUM(action='view') views,COUNT(DISTINCT visitor) visitors,
            COUNT(DISTINCT CASE WHEN action IN ({placeholders}) THEN visitor END) builders,
            COUNT(DISTINCT CASE WHEN action='export_success' THEN visitor END) exporters
            FROM library_event WHERE created_at>=? AND created_at<? GROUP BY 1 ORDER BY 1''', (*sorted(BUILD_ACTIONS), *bounds))]
        tracking = conn.execute("SELECT value FROM library_meta WHERE key='tracking_since'").fetchone()[0]
    actions = [dict(label=label, **next((r for r in rows if r['action']==action),
               dict(action=action, count=0, visitors=0, avg_ms=None))) for action, label in ACTIONS.items()]
    by_action = {r['action']: r for r in actions}
    uv = len(visitors)
    exports = by_action['export_success']['visitors']
    return dict(days=days, timezone='Asia/Shanghai', tracking_since=tracking+'Z', actions=actions, daily=daily,
                summary=dict(views=by_action['view']['count'], visitors=uv, builders=builders, exporters=exports,
                             site_visitors=site_visitors, penetration=uv/site_visitors if site_visitors else None,
                             build_rate=builders/uv if uv else None, export_rate=exports/uv if uv else None))


def create_library_analytics_blueprint(stats_path, admin_authorized):
    bp = Blueprint('library_analytics', __name__)

    @bp.post('/api/track/library')
    def track():
        if request.content_length and request.content_length > 2048:
            return jsonify(error='记录过大'), 413
        try:
            recorded = record_event(stats_path(), request.get_json(silent=True), request.headers.get('X-Fingerprint', ''))
            return jsonify(ok=True, recorded=recorded)
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        except OverflowError as exc:
            return jsonify(error=str(exc)), 429
        except sqlite3.Error:
            return jsonify(error='统计暂不可用'), 503

    @bp.get('/api/stats/library')
    def report():
        if not admin_authorized():
            return Response('Unauthorized', 401, {'WWW-Authenticate': 'Basic realm="BazaarQiuBot Admin"', 'Cache-Control': 'no-store'})
        try:
            days = int(request.args.get('days', '7'))
            if not 1 <= days <= 90:
                raise ValueError()
            response = jsonify(library_report(stats_path(), days))
            response.headers['Cache-Control'] = 'private, no-store'
            return response
        except ValueError:
            return jsonify(error='请选择 1～90 天'), 400
        except sqlite3.Error:
            return jsonify(error='统计暂不可用'), 503

    return bp
