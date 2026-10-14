"""Privacy-preserving product analytics for the BazaarQiuBot web app."""
import json
import queue
import sqlite3
import threading
from collections import Counter
from datetime import datetime, timedelta, timezone


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=0.1)
    # 分析写入宁可丢弃，也不能因锁竞争拖慢业务请求。
    conn.execute("PRAGMA busy_timeout=100")
    return conn


def init_analytics_db(db_path: str) -> None:
    conn = _connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""CREATE TABLE IF NOT EXISTS feature_event (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            feature TEXT NOT NULL,
            page TEXT NOT NULL DEFAULT '',
            outcome TEXT NOT NULL DEFAULT 'success',
            fingerprint TEXT NOT NULL DEFAULT '',
            ip TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_feature_event_time ON feature_event(created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_feature_event_feature_time ON feature_event(feature, created_at)")
        conn.execute("DELETE FROM feature_event WHERE created_at < datetime('now', '-90 days')")
        conn.commit()
    finally:
        conn.close()


def log_feature_event(db_path: str, feature: str, page: str, outcome: str,
                      fingerprint: str, ip: str, created_at: str | None = None) -> None:
    if not feature or len(feature) > 64 or len(page) > 64 or outcome not in {"success", "empty", "error"}:
        return
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO feature_event (feature, page, outcome, fingerprint, ip, created_at) VALUES (?,?,?,?,?,?)",
            (feature, page, outcome, fingerprint[:128], ip[:64],
             created_at or datetime.now(timezone.utc).replace(tzinfo=None).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def cleanup_old_events(db_path: str) -> None:
    conn = _connect(db_path)
    try:
        conn.execute("DELETE FROM feature_event WHERE created_at < datetime('now', '-90 days')")
        conn.commit()
    finally:
        conn.close()


class FeatureEventWriter:
    """有界异步写入器：业务请求只入队，队列满时丢弃统计。"""

    def __init__(self, db_path: str, maxsize: int = 2000):
        self.db_path = db_path
        self.queue = queue.Queue(maxsize=maxsize)
        self._thread = threading.Thread(target=self._run, daemon=True, name='feature-event-writer')
        self._thread.start()

    def submit(self, feature: str, page: str, outcome: str, fingerprint: str, ip: str) -> bool:
        try:
            created_at = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
            self.queue.put_nowait((feature, page, outcome, fingerprint, ip, created_at))
            return True
        except queue.Full:
            return False

    def _run(self) -> None:
        cleanup_after = 0.0
        while True:
            feature, page, outcome, fingerprint, ip, created_at = self.queue.get()
            try:
                log_feature_event(self.db_path, feature, page, outcome, fingerprint, ip, created_at)
                now = __import__('time').time()
                if now >= cleanup_after:
                    cleanup_old_events(self.db_path)
                    cleanup_after = now + 86400
            except Exception as exc:
                # 单条统计异常不能杀死消费者；不影响业务请求，后续事件继续消费。
                print(f'[analytics] 异步写入失败: {exc}', flush=True)
            finally:
                self.queue.task_done()


def aggregate_hot_card_queries(rows, canonicalize=None, limit: int = 10) -> list[dict]:
    """按单张卡聚合所有含卡牌条件的核心查询；同一次查询内重复卡只计一次。"""
    canonicalize = canonicalize or (lambda name: name)
    counts = Counter()
    for query_type, params_json, _result_count in rows:
        try:
            params = json.loads(params_json or '{}')
        except (TypeError, ValueError):
            continue
        if query_type in {'runs', 'winrate'}:
            values = params.get('cards') or []
        elif query_type in {'partner', 'comp_card'}:
            values = [params.get('card')]
        else:
            continue
        if isinstance(values, str):
            values = [values]
        cards = set()
        for value in values:
            for raw_name in str(value or '').split('+'):
                name = raw_name.strip()
                if not name:
                    continue
                canonical = str(canonicalize(name) or name).strip()
                if canonical:
                    cards.add(canonical)
        counts.update(cards)
    return [
        {'card': card, 'cnt': count}
        for card, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]
    ]


CORE_MODULE_FEATURES = {
    'runs_query', 'winrate_query', 'partner_query', 'topcard_query',
    'card_tier_query', 'comp_query', 'comp_card_query', 'hero_overview',
    'routes_query', 'feedback_submit', 'feedback_like', 'feedback_comment',
    'trivia_submit', 'trivia_vote', 'trivia_share', 'donation_qr_open',
}


_BEIJING = timezone(timedelta(hours=8))
_UV_IDENTITY = """CASE
    WHEN COALESCE(fingerprint, '') != '' THEN 'fp:' || fingerprint
    WHEN COALESCE(ip, '') NOT IN ('', '*.*.*.*') THEN 'ip:' || ip
    ELSE NULL END"""


def intraday(db_path: str, date: str | None = None, now: datetime | None = None) -> dict:
    """Hourly usage and prior-day comparison, clipped to the same local time.

    UTC ISO timestamps are range-filtered before bucketing so time indexes remain
    usable. UV is queried separately for each hour and entire period, including
    the all-module scope; it is never obtained by adding distinct counts.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    local_now = now.astimezone(_BEIJING)
    today = local_now.date()
    min_date = today - timedelta(days=89)
    if date is None:
        selected = today
    else:
        try:
            selected = datetime.strptime(date, '%Y-%m-%d').date()
        except (TypeError, ValueError) as exc:
            raise ValueError('日期格式应为 YYYY-MM-DD') from exc
        if selected.isoformat() != date:
            raise ValueError('日期格式应为 YYYY-MM-DD')
    if not min_date <= selected <= today:
        raise ValueError('请选择最近 90 天内的日期，不能选择未来日期')

    is_today = selected == today
    elapsed = (local_now - local_now.replace(hour=0, minute=0, second=0, microsecond=0)
               if is_today else timedelta(days=1))
    previous_date = selected - timedelta(days=1)
    module_names = set()
    conn = _connect(db_path)
    try:
        # One snapshot keeps source availability and all totals consistent.
        conn.execute('BEGIN')
        first_seen = {}
        for source, table in (('core', 'feature_event'), ('pv', 'page_view'), ('api', 'api_log')):
            raw = conn.execute(f'SELECT MIN(created_at) FROM {table}').fetchone()[0]
            first_seen[source] = (
                datetime.fromisoformat(raw).replace(tzinfo=timezone.utc).astimezone(_BEIJING).date()
                if raw else None
            )

        def period(day):
            start = datetime.combine(day, datetime.min.time(), tzinfo=_BEIJING)
            end = start + elapsed
            bounds = tuple(value.astimezone(timezone.utc).replace(tzinfo=None).isoformat()
                           for value in (start, end))
            available = {source: bool(first and day >= first and day >= min_date)
                         for source, first in first_seen.items()}

            def empty_scope():
                hours = []
                for hour in range(24):
                    state = ('future' if is_today and hour > local_now.hour else
                             'partial' if is_today and hour == local_now.hour else 'complete')
                    hours.append({'hour': hour, 'state': state, 'uses': 0, 'uv': 0,
                                  'successes': 0, 'empty_results': 0, 'errors': 0,
                                  'pv': 0, 'api_calls': 0})
                return {'hours': hours, 'summary': {'uses': 0, 'uv': 0, 'successes': 0,
                                                  'empty_results': 0, 'errors': 0,
                                                  'pv': 0, 'api_calls': 0}}

            result = {'date': day.isoformat(), 'available': available,
                      'all': empty_scope(), 'modules': {}}
            placeholders = ','.join('?' for _ in CORE_MODULE_FEATURES)
            where = f'created_at >= ? AND created_at < ? AND feature IN ({placeholders})'
            args = (*bounds, *sorted(CORE_MODULE_FEATURES))
            counts = f"""COUNT(*), COUNT(DISTINCT {_UV_IDENTITY}),
                SUM(outcome='success'), SUM(outcome='empty'), SUM(outcome='error')"""
            metrics = ('uses', 'uv', 'successes', 'empty_results', 'errors')
            for by_module in (False, True):
                module_expr = 'page' if by_module else "'all'"
                rows = conn.execute(f"""SELECT {module_expr},
                    CAST(STRFTIME('%H', created_at, '+8 hours') AS INTEGER), {counts}
                    FROM feature_event WHERE {where}
                    GROUP BY 1, 2 ORDER BY 1, 2""", args).fetchall()
                summaries = conn.execute(f"""SELECT {module_expr}, {counts}
                    FROM feature_event WHERE {where} GROUP BY 1""", args).fetchall()
                for module, hour, *values in rows:
                    if by_module:
                        module_names.add(module)
                        scope = result['modules'].setdefault(module, empty_scope())
                    else:
                        scope = result['all']
                    scope['hours'][hour].update(zip(metrics, values))
                for module, *values in summaries:
                    scope = result['modules'][module] if by_module else result['all']
                    scope['summary'].update(zip(metrics, values))
            for table, metric in (('page_view', 'pv'), ('api_log', 'api_calls')):
                rows = conn.execute(f"""SELECT
                    CAST(STRFTIME('%H', created_at, '+8 hours') AS INTEGER), COUNT(*)
                    FROM {table} WHERE created_at >= ? AND created_at < ?
                    GROUP BY 1 ORDER BY 1""", bounds).fetchall()
                for hour, count in rows:
                    result['all']['hours'][hour][metric] = count
                result['all']['summary'][metric] = sum(row[1] for row in rows)
            return result

        current = period(selected)
        previous = period(previous_date)
    finally:
        conn.close()
    return {
        'date': selected.isoformat(), 'previous_date': previous_date.isoformat(),
        'today': today.isoformat(), 'min_date': min_date.isoformat(),
        'timezone': 'Asia/Shanghai', 'is_today': is_today,
        'generated_at': local_now.isoformat(),
        'cutoff_time': local_now.strftime('%H:%M:%S') if is_today else '24:00:00',
        'modules': sorted(module_names), 'current': current, 'previous': previous,
    }


def overview(db_path: str, days: int, now: datetime | None = None) -> dict:
    days = max(1, min(int(days), 90))
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    today_bj = (now + timedelta(hours=8)).replace(hour=0, minute=0, second=0, microsecond=0)
    cutoff = (today_bj - timedelta(days=days - 1) - timedelta(hours=8)).isoformat()
    conn = _connect(db_path)
    try:
        rows = conn.execute("""
            SELECT feature, COUNT(*),
                   SUM(CASE WHEN outcome='success' THEN 1 ELSE 0 END),
                   SUM(CASE WHEN outcome='empty' THEN 1 ELSE 0 END),
                   SUM(CASE WHEN outcome='error' THEN 1 ELSE 0 END),
                   COUNT(DISTINCT COALESCE(NULLIF(fingerprint,''), ip))
            FROM feature_event WHERE created_at >= ?
            GROUP BY feature ORDER BY COUNT(*) DESC, feature
        """, (cutoff,)).fetchall()
        daily = conn.execute("""
            SELECT DATE(created_at, '+8 hours'), feature, COUNT(*)
            FROM feature_event WHERE created_at >= ?
            GROUP BY DATE(created_at, '+8 hours'), feature ORDER BY 1, 2
        """, (cutoff,)).fetchall()
        placeholders = ','.join('?' for _ in CORE_MODULE_FEATURES)
        modules = conn.execute(f"""
            SELECT page, COUNT(*),
                   COUNT(DISTINCT COALESCE(NULLIF(fingerprint,''), ip))
            FROM feature_event
            WHERE created_at >= ? AND feature IN ({placeholders})
            GROUP BY page ORDER BY COUNT(*) DESC, page
        """, (cutoff, *sorted(CORE_MODULE_FEATURES))).fetchall()
    finally:
        conn.close()
    return {
        "feature_total": sum(row[1] for row in rows),
        "feature_usage": [
            {"feature": row[0], "uses": row[1], "successes": row[2] or 0,
             "empty_results": row[3] or 0, "errors": row[4] or 0, "uv": row[5] or 0}
            for row in rows
        ],
        "feature_daily": [{"day": row[0], "feature": row[1], "count": row[2]} for row in daily],
        "module_usage": [{"module": row[0], "uses": row[1], "uv": row[2] or 0} for row in modules],
    }
