"""Composition interactions, separate from signed manual-search counts."""
import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from flask import Blueprint, Response, jsonify, request

ACTIONS = {
    'view': '进入阵容分析', 'sort': '切换排序', 'examples_open': '展开真实阵容',
    'run_open': '打开本局阵容', 'source_open': '查看原始对局',
    'share_open': '打开组合分享', 'share_link_copy': '成功复制组合链接',
    'share_generate': '开始生成分享图', 'share_generated': '分享图生成成功',
    'share_error': '分享图生成失败', 'share_cancel': '生成中关闭分享',
    'share_download': '点击下载分享图', 'share_image_copy': '成功复制分享图',
    'share_landing': '分享链接定位到组合',
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


def init_comp_analytics(path):
    with connect(path) as conn:
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS comp_event (
                id TEXT PRIMARY KEY, visitor TEXT NOT NULL, action TEXT NOT NULL,
                group_key TEXT NOT NULL, detail TEXT NOT NULL, duration_ms INTEGER,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_comp_event_time ON comp_event(created_at);
            CREATE INDEX IF NOT EXISTS idx_comp_event_visitor_time ON comp_event(visitor,created_at);
            CREATE TABLE IF NOT EXISTS comp_meta (key TEXT PRIMARY KEY,value TEXT NOT NULL);
        ''')
        conn.execute('INSERT OR IGNORE INTO comp_meta VALUES (?,?)', ('tracking_since', now_utc().isoformat()))


def record_event(path, payload, fingerprint):
    if not isinstance(payload, dict):
        raise ValueError('无效记录')
    event_id, action = payload.get('id'), payload.get('action')
    if not all(isinstance(v, str) and TOKEN.fullmatch(v) for v in (event_id, fingerprint)):
        raise ValueError('无效匿名标识')
    if not isinstance(action, str) or action not in ACTIONS:
        raise ValueError('无效操作')
    group = payload.get('group', '')
    if not isinstance(group, str) or len(group) > 512 or any(ord(c) < 32 for c in group):
        raise ValueError('无效组合')
    detail = payload.get('detail', '')
    allowed = {'sort': {'reliable', 'rate', 'appearance'},
               'share_error': {'prepare', 'art', 'render', 'encode'},
               'share_generated': {'complete', 'missing_art'}}.get(action, {''})
    if not isinstance(detail, str) or detail not in allowed:
        raise ValueError('无效明细')
    duration = payload.get('duration_ms')
    if duration is not None and (type(duration) is not int or not 0 <= duration <= 3600000):
        raise ValueError('无效耗时')
    if action not in {'share_generated', 'share_error', 'share_cancel'}:
        duration = None
    visitor = hashlib.sha256(fingerprint.encode()).hexdigest()
    now = now_utc()
    with connect(path) as conn:
        # Serialize the rate check with the insert; concurrent reports cannot bypass it.
        conn.execute('BEGIN IMMEDIATE')
        old = conn.execute('SELECT visitor FROM comp_event WHERE id=?', (event_id,)).fetchone()
        if old:
            if old['visitor'] != visitor:
                raise ValueError('记录编号已使用')
            return False
        count = conn.execute('SELECT COUNT(*) FROM comp_event WHERE visitor=? AND created_at>=?',
                             (visitor, (now-timedelta(minutes=1)).isoformat())).fetchone()[0]
        if count >= 180:
            raise OverflowError('记录过于频繁')
        conn.execute('INSERT INTO comp_event VALUES (?,?,?,?,?,?,?)',
                     (event_id, visitor, action, group, detail, duration, now.isoformat()))
        day = now.date().isoformat()
        previous = conn.execute("SELECT value FROM comp_meta WHERE key='cleanup_day'").fetchone()
        if not previous or previous['value'] != day:
            conn.execute('DELETE FROM comp_event WHERE created_at<?', ((now-timedelta(days=90)).isoformat(),))
            conn.execute("INSERT OR REPLACE INTO comp_meta VALUES ('cleanup_day',?)", (day,))
    return True


def comp_report(path, days, now=None):
    local = (now or datetime.now(timezone.utc)).astimezone(BJ)
    start = datetime.combine(local.date()-timedelta(days=days-1), datetime.min.time(), BJ)
    end = datetime.combine(local.date()+timedelta(days=1), datetime.min.time(), BJ)
    bounds = tuple(d.astimezone(timezone.utc).replace(tzinfo=None).isoformat() for d in (start, end))
    with connect(path) as conn:
        events = [dict(r) for r in conn.execute('''SELECT action,COUNT(*) count,COUNT(DISTINCT visitor) visitors,
            AVG(duration_ms) avg_ms FROM comp_event WHERE created_at>=? AND created_at<? GROUP BY action''', bounds)]
        queries = [dict(r) for r in conn.execute('''SELECT outcome,COUNT(*) count FROM search_event
            WHERE feature='comp_card' AND created_at>=? AND created_at<? GROUP BY outcome''', bounds)]
        query_uv = conn.execute('''SELECT COUNT(DISTINCT visitor) FROM search_event WHERE feature='comp_card'
            AND created_at>=? AND created_at<?''', bounds).fetchone()[0]
        sorts = [dict(r) for r in conn.execute('''SELECT detail,COUNT(*) count FROM comp_event WHERE action='sort'
            AND created_at>=? AND created_at<? GROUP BY detail ORDER BY count DESC''', bounds)]
        issues = [dict(r) for r in conn.execute('''SELECT action,detail,COUNT(*) count FROM comp_event
            WHERE action IN ('share_error','share_generated') AND created_at>=? AND created_at<?
            GROUP BY action,detail''', bounds)]
        requests = dict(conn.execute('''SELECT COUNT(*) count,AVG(duration_ms) avg_ms,
            SUM(CASE WHEN success=0 THEN 1 ELSE 0 END) errors FROM api_log WHERE endpoint='/api/comp/card'
            AND created_at>=? AND created_at<?''', bounds).fetchone())
        tracking = conn.execute("SELECT value FROM comp_meta WHERE key='tracking_since'").fetchone()[0]
        item_counts = {}
        for row in conn.execute('''SELECT params_json FROM search_event WHERE feature='comp_card'
                AND created_at>=? AND created_at<?''', bounds):
            try:
                size = sum(len(g) for g in json.loads(row[0])['groups'])
                if 1 <= size <= 5:
                    item_counts[size] = item_counts.get(size, 0)+1
            except (ValueError, KeyError, TypeError):
                pass
    outcomes = {r['outcome']: r['count'] for r in queries}
    count = sum(outcomes.values())
    return dict(days=days, timezone='Asia/Shanghai', tracking_since=tracking+'Z',
                queries=dict(count=count, visitors=query_uv, outcomes=outcomes,
                             result_rate=outcomes.get('success', 0)/count if count else None,
                             item_counts=item_counts),
                actions=[dict(label=label, **next((r for r in events if r['action']==key),
                             dict(action=key, count=0, visitors=0, avg_ms=None))) for key, label in ACTIONS.items()],
                sorts=sorts, issues=issues, requests=requests)


def create_comp_analytics_blueprint(stats_path, admin_authorized):
    bp = Blueprint('comp_analytics', __name__)

    @bp.post('/api/track/comp')
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

    @bp.get('/api/stats/comp')
    def report():
        if not admin_authorized():
            return Response('Unauthorized', 401, {'WWW-Authenticate': 'Basic realm="BazaarQiuBot Admin"', 'Cache-Control': 'no-store'})
        try:
            days = int(request.args.get('days', '7'))
            if not 1 <= days <= 90:
                raise ValueError()
            response = jsonify(comp_report(stats_path(), days))
            response.headers['Cache-Control'] = 'private, no-store'
            return response
        except ValueError:
            return jsonify(error='请选择 1～90 天'), 400
        except sqlite3.Error:
            return jsonify(error='统计暂不可用'), 503

    return bp
