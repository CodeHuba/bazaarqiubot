"""First-touch visit attribution; stores no full referrers or visitor URLs."""
import hashlib
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from flask import Blueprint, Response, current_app, jsonify, request

TOKEN = re.compile(r'^[A-Za-z0-9_-]{8,128}$')
LABEL = re.compile(r'^[a-z0-9_-]{1,64}$')
PUBLIC_PATH = re.compile(r'^/(?:|runs|library/?|winrate|partner|topcard|trivia(?:/[1-9][0-9]*)?|builds(?:/(?:vanessa|dooley|mak|pygmalien|stelle|jules|karnok|dragons))?|feedback|support|zaza|static/privacy\.html)$')


@contextmanager
def connect(path):
    conn = sqlite3.connect(path, timeout=.2)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_acquisition(path):
    with connect(path) as conn:
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS acquisition_visit (
                id TEXT PRIMARY KEY, visitor TEXT NOT NULL, source TEXT NOT NULL,
                medium TEXT NOT NULL, campaign TEXT NOT NULL, content TEXT NOT NULL,
                landing TEXT NOT NULL, referrer_host TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_acquisition_visit_time ON acquisition_visit(created_at);
            CREATE TABLE IF NOT EXISTS acquisition_view (
                id TEXT PRIMARY KEY, visit_id TEXT NOT NULL, path TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_acquisition_view_time ON acquisition_view(created_at);
            CREATE INDEX IF NOT EXISTS idx_acquisition_view_visit ON acquisition_view(visit_id);
            CREATE TABLE IF NOT EXISTS acquisition_query (
                id TEXT PRIMARY KEY, visit_id TEXT NOT NULL, feature TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_acquisition_query_time ON acquisition_query(created_at);
            CREATE INDEX IF NOT EXISTS idx_acquisition_query_visit ON acquisition_query(visit_id);
            CREATE TABLE IF NOT EXISTS acquisition_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        ''')
        conn.execute('INSERT OR IGNORE INTO acquisition_meta VALUES (?,?)',
                     ('tracking_since', datetime.now(timezone.utc).replace(tzinfo=None).isoformat()))


def _label(value):
    value = value.lower() if isinstance(value, str) else ''
    return value if LABEL.fullmatch(value) else ''


def attribution(payload):
    raw = payload.get('referrer', '')
    try:
        parsed = urlsplit(raw if isinstance(raw, str) else '')
        host = (parsed.hostname or '').lower()
        if parsed.scheme not in ('http', 'https') or len(host) > 253:
            host = ''
    except ValueError:
        host = ''
    if host in ('bazaarqiubot.com', 'www.bazaarqiubot.com', request.host.split(':')[0]):
        host = ''
    source, medium = _label(payload.get('source')), _label(payload.get('medium'))
    if not source:
        for domain, name, kind in [('baidu.com', 'baidu', 'organic'), ('bing.com', 'bing', 'organic'),
                                   ('google.com', 'google', 'organic'), ('google.com.hk', 'google', 'organic'),
                                   ('google.com.tw', 'google', 'organic'), ('google.co.jp', 'google', 'organic'),
                                   ('google.co.uk', 'google', 'organic'), ('sogou.com', 'sogou', 'organic'),
                                   ('so.com', '360', 'organic'), ('bilibili.com', 'bilibili', 'referral'),
                                   ('b23.tv', 'bilibili', 'referral'), ('qq.com', 'qq', 'referral')]:
            if host == domain or host.endswith('.' + domain):
                source, medium = name, kind
                break
        if not source:
            source, medium = ('referral', 'referral') if host else ('direct', 'none')
    return source, medium or 'referral', _label(payload.get('campaign')), _label(payload.get('content')), host


def record_view(path, payload, fingerprint):
    if not isinstance(payload, dict) or not TOKEN.fullmatch(fingerprint or ''):
        raise ValueError('缺少匿名访问标识')
    visit, view, landing = payload.get('visit_id'), payload.get('view_id'), payload.get('path')
    if not isinstance(visit, str) or not TOKEN.fullmatch(visit) or not isinstance(view, str) or not TOKEN.fullmatch(view):
        raise ValueError('访问编号无效')
    if not isinstance(landing, str) or not PUBLIC_PATH.fullmatch(landing):
        raise ValueError('访问路径无效')
    source, medium, campaign, content, host = attribution(payload)
    visitor = hashlib.sha256(fingerprint.encode()).hexdigest()
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    with connect(path) as conn:
        conn.execute('INSERT OR IGNORE INTO acquisition_visit VALUES (?,?,?,?,?,?,?,?,?)',
                     (visit, visitor, source, medium, campaign, content, landing, host, now))
        owner = conn.execute('SELECT visitor FROM acquisition_visit WHERE id=?', (visit,)).fetchone()
        if owner['visitor'] != visitor:
            raise ValueError('访问编号与匿名标识不匹配')
        conn.execute('INSERT OR IGNORE INTO acquisition_view VALUES (?,?,?,?)', (view, visit, landing, now))
        # Bounded retention, once per UTC day, instead of a cleanup scan per visit.
        day = now[:10]
        previous = conn.execute("SELECT value FROM acquisition_meta WHERE key='cleanup_day'").fetchone()
        if not previous or previous['value'] != day:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).replace(tzinfo=None).isoformat()
            for table in ('acquisition_view', 'acquisition_query', 'acquisition_visit'):
                conn.execute(f'DELETE FROM {table} WHERE created_at<?', (cutoff,))
            conn.execute("INSERT OR REPLACE INTO acquisition_meta VALUES ('cleanup_day',?)", (day,))


def record_query(path, visit, fingerprint, action, feature):
    if not all(isinstance(v, str) and TOKEN.fullmatch(v) for v in (visit, fingerprint, action)):
        return
    if feature not in ('runs', 'comp_card', 'winrate', 'partner'):
        return
    visitor = hashlib.sha256(fingerprint.encode()).hexdigest()
    now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    with connect(path) as conn:
        conn.execute('''INSERT OR IGNORE INTO acquisition_query
            SELECT ?,id,?,? FROM acquisition_visit WHERE id=? AND visitor=?''',
                     (action, feature, now, visit, visitor))


def acquisition_report(path, days, now=None):
    now = now or datetime.now(timezone.utc)
    local = now.astimezone(timezone(timedelta(hours=8)))
    start = local.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days-1)
    bounds = (start.astimezone(timezone.utc).replace(tzinfo=None).isoformat(),
              now.astimezone(timezone.utc).replace(tzinfo=None).isoformat())
    with connect(path) as conn:
        # Aggregate first so page views and queries cannot multiply each other in a join.
        conn.execute('BEGIN')
        visits = [dict(r) for r in conn.execute('''SELECT v.*,
            (SELECT COUNT(*) FROM acquisition_view p WHERE p.visit_id=v.id AND p.created_at>=? AND p.created_at<?) AS pv,
            (SELECT COUNT(*) FROM acquisition_query q WHERE q.visit_id=v.id AND q.created_at>=? AND q.created_at<?) AS queries
            FROM acquisition_visit v WHERE v.id IN (
                SELECT visit_id FROM acquisition_view WHERE created_at>=? AND created_at<?
                UNION SELECT visit_id FROM acquisition_query WHERE created_at>=? AND created_at<?)''', bounds*4)]
        tracking = conn.execute("SELECT value FROM acquisition_meta WHERE key='tracking_since'").fetchone()
    def aggregate(rows):
        visitors = {r['visitor'] for r in rows}
        converted = {r['visitor'] for r in rows if r['queries']}
        return dict(uv=len(visitors), visits=len(rows), pv=sum(r['pv'] for r in rows),
                    queries=sum(r['queries'] for r in rows), query_uv=len(converted),
                    query_rate=len(converted)/len(visitors) if visitors else None)
    groups = {}
    for row in visits:
        key = tuple(row[k] for k in ('source', 'medium', 'campaign', 'content', 'landing'))
        groups.setdefault(key, []).append(row)
    rows = [dict(zip(('source', 'medium', 'campaign', 'content', 'landing'), key), **aggregate(value))
            for key, value in groups.items()]
    rows.sort(key=lambda r: (-r['uv'], -r['pv'], r['source']))
    return dict(days=days, timezone='Asia/Shanghai', tracking_since=tracking['value'] if tracking else None,
                generated_at=local.isoformat(), summary=aggregate(visits), rows=rows)


def create_acquisition_blueprint(stats_path, admin_authorized):
    bp = Blueprint('acquisition', __name__)

    @bp.post('/api/track/visit')
    def track_visit():
        if request.content_length and request.content_length > 4096:
            return jsonify(error='访问记录过大'), 413
        try:
            record_view(stats_path(), request.get_json(silent=True), request.headers.get('X-Fingerprint', ''))
            return jsonify(ok=True)
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        except sqlite3.Error:
            return jsonify(error='访问统计暂不可用'), 503

    @bp.get('/api/stats/acquisition')
    def stats():
        if not admin_authorized():
            return Response('Unauthorized', 401, {'WWW-Authenticate': 'Basic realm="BazaarQiuBot Admin"'})
        try:
            days = int(request.args.get('days', '7'))
            if not 1 <= days <= 90:
                raise ValueError()
            result = jsonify(acquisition_report(stats_path(), days))
            result.headers['Cache-Control'] = 'private, no-store'
            return result
        except ValueError:
            return jsonify(error='请选择 1～90 天'), 400
        except sqlite3.Error:
            return jsonify(error='访问统计暂不可用'), 503

    @bp.after_app_request
    def conversion(response):
        # The existing deduplicated, signed query receipt also covers browser-cache hits.
        if request.path == '/api/track/search' and response.status_code == 200:
            result = response.get_json(silent=True) or {}
            payload = request.get_json(silent=True) or {}
            if result.get('recorded') and payload.get('receipt'):
                try:
                    record_query(stats_path(), request.headers.get('X-Visit-Id', ''),
                                 request.headers.get('X-Fingerprint', ''), payload.get('id'), payload.get('feature'))
                except sqlite3.Error:
                    current_app.logger.warning('Acquisition query tracking unavailable')
        return response

    return bp
