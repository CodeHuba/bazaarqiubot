import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from web_runs.analytics import FeatureEventWriter, init_analytics_db, log_feature_event, overview


def test_feature_event_overview_counts_uses_success_and_unique_visitors(tmp_path: Path):
    db_path = tmp_path / "stats.db"
    init_analytics_db(str(db_path))

    log_feature_event(str(db_path), "winrate_query", "winrate", "success", "fp-a", "1.2.*.*")
    log_feature_event(str(db_path), "winrate_query", "winrate", "empty", "fp-b", "1.2.*.*")
    log_feature_event(str(db_path), "comp_card_query", "runs", "success", "fp-a", "3.4.*.*")

    data = overview(str(db_path), days=7, now=datetime.now(timezone.utc).replace(tzinfo=None))
    winrate = next(row for row in data["feature_usage"] if row["feature"] == "winrate_query")

    assert winrate == {
        "feature": "winrate_query",
        "uses": 2,
        "successes": 1,
        "empty_results": 1,
        "errors": 0,
        "uv": 2,
    }
    assert data["feature_total"] == 3


def test_feature_event_overview_excludes_rows_before_beijing_calendar_cutoff(tmp_path: Path):
    db_path = tmp_path / "stats.db"
    init_analytics_db(str(db_path))
    now = datetime(2026, 9, 9, 4, 0, 0)  # 北京时间 12:00
    yesterday_utc = (now - timedelta(days=1)).isoformat()

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO feature_event (feature, page, outcome, fingerprint, ip, created_at) VALUES (?,?,?,?,?,?)",
        ("runs_query", "runs", "success", "fp-old", "1.2.*.*", yesterday_utc),
    )
    conn.commit()
    conn.close()

    data = overview(str(db_path), days=1, now=now)

    assert data["feature_total"] == 0
    assert data["feature_usage"] == []


def test_feature_event_database_uses_wal(tmp_path: Path):
    db_path = tmp_path / "stats.db"
    init_analytics_db(str(db_path))
    conn = sqlite3.connect(db_path)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    conn.close()


def test_feature_event_writer_writes_in_background(tmp_path: Path):
    db_path = tmp_path / "stats.db"
    init_analytics_db(str(db_path))
    writer = FeatureEventWriter(str(db_path), maxsize=4)

    assert writer.submit("trivia_share", "trivia", "success", "fp-a", "1.2.*.*")
    deadline = time.time() + 2
    count = 0
    while time.time() < deadline:
        conn = sqlite3.connect(db_path)
        count = conn.execute("SELECT COUNT(*) FROM feature_event").fetchone()[0]
        conn.close()
        if count == 1:
            break
        time.sleep(0.01)
    assert count == 1


def test_feature_event_writer_survives_non_sqlite_task_failure(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "stats.db"
    init_analytics_db(str(db_path))
    writer = FeatureEventWriter(str(db_path), maxsize=4)
    import web_runs.analytics as analytics
    original = analytics.log_feature_event
    calls = {'count': 0}

    def flaky(*args, **kwargs):
        calls['count'] += 1
        if calls['count'] == 1:
            raise RuntimeError('simulated writer failure')
        return original(*args, **kwargs)

    monkeypatch.setattr(analytics, 'log_feature_event', flaky)
    assert writer.submit('trivia_share', 'trivia', 'success', 'fp-a', '1.2.*.*')
    assert writer.submit('donation_qr_open', 'support', 'success', 'fp-b', '3.4.*.*')
    deadline = time.time() + 2
    count = 0
    while time.time() < deadline:
        conn = sqlite3.connect(db_path)
        count = conn.execute('SELECT COUNT(*) FROM feature_event').fetchone()[0]
        conn.close()
        if count == 1:
            break
        time.sleep(0.01)
    assert count == 1
    assert writer._thread.is_alive()
