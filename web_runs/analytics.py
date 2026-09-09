"""Privacy-preserving product analytics for the BazaarQiuBot web app."""
import queue
import sqlite3
import threading
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
            self.queue.put_nowait((feature, page, outcome, fingerprint, ip))
            return True
        except queue.Full:
            return False

    def _run(self) -> None:
        cleanup_after = 0.0
        while True:
            feature, page, outcome, fingerprint, ip = self.queue.get()
            try:
                log_feature_event(self.db_path, feature, page, outcome, fingerprint, ip)
                now = __import__('time').time()
                if now >= cleanup_after:
                    cleanup_old_events(self.db_path)
                    cleanup_after = now + 86400
            except Exception as exc:
                # 单条统计异常不能杀死消费者；不影响业务请求，后续事件继续消费。
                print(f'[analytics] 异步写入失败: {exc}', flush=True)
            finally:
                self.queue.task_done()


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
    }
