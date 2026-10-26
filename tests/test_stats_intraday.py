import queue
import sqlite3
from datetime import datetime, timezone

import pytest

from web_runs.analytics import FeatureEventWriter, init_analytics_db, intraday, log_feature_event


@pytest.fixture
def usage_db(tmp_path):
    db = tmp_path / 'stats.db'
    init_analytics_db(str(db))
    with sqlite3.connect(db) as conn:
        conn.executescript('''
            CREATE TABLE page_view (fingerprint TEXT, ip TEXT, created_at TEXT);
            CREATE INDEX idx_pv_time ON page_view(created_at);
            CREATE TABLE api_log (fingerprint TEXT, ip TEXT, created_at TEXT);
            CREATE INDEX idx_api_time ON api_log(created_at);
        ''')
    return db


def event(db, timestamp, fingerprint='same-user', page='runs', feature='runs_query', outcome='success', ip='1.2.*.*'):
    log_feature_event(str(db), feature, page, outcome, fingerprint, ip, timestamp)


def test_beijing_hour_boundaries_and_previous_day_same_time(usage_db):
    event(usage_db, '2026-10-12T16:00:00')  # Yesterday 00:00, retained for comparison.
    event(usage_db, '2026-10-13T06:34:59')  # Yesterday 14:34:59.
    event(usage_db, '2026-10-13T06:35:00')  # Outside yesterday's aligned cutoff.
    event(usage_db, '2026-10-13T15:59:59')  # Outside yesterday's aligned cutoff.
    event(usage_db, '2026-10-13T16:00:00')  # Today 00:00.
    event(usage_db, '2026-10-13T17:00:00')  # Today 01:00.
    event(usage_db, '2026-10-14T06:34:59')  # Today 14:34:59.
    event(usage_db, '2026-10-14T06:35:00')  # Exclusive upper bound.
    event(usage_db, '2026-10-14T16:00:00')  # Next day.
    result = intraday(str(usage_db), '2026-10-14', datetime(2026, 10, 14, 6, 35))
    current, previous = result['current']['all'], result['previous']['all']
    assert result['cutoff_time'] == '14:35:00'
    assert current['summary']['uses'] == 3
    assert previous['summary']['uses'] == 2
    assert current['hours'][0]['uses'] == 1
    assert current['hours'][1]['uses'] == 1
    assert current['hours'][2]['uses'] == 0
    assert current['hours'][14]['state'] == 'partial'
    assert current['hours'][15]['state'] == 'future'
    assert previous['hours'][14]['state'] == 'partial'
    assert previous['hours'][15]['uses'] == 0
    # An aware Beijing clock must produce exactly the same cutoff as UTC.
    aware = datetime.fromisoformat('2026-10-14T14:35:00+08:00')
    assert intraday(str(usage_db), '2026-10-14', aware) == result


def test_core_whitelist_outcomes_and_distinct_uv_across_hours_and_modules(usage_db):
    event(usage_db, '2026-10-13T16:00:00')
    event(usage_db, '2026-10-13T17:00:00', feature='comp_card_query', outcome='empty')
    event(usage_db, '2026-10-13T17:01:00', page='winrate', feature='winrate_query', outcome='error')
    event(usage_db, '2026-10-13T18:00:00', fingerprint='', ip='same-user')
    event(usage_db, '2026-10-13T18:01:00', fingerprint='', ip='same-user')
    event(usage_db, '2026-10-13T18:02:00', fingerprint='', ip='')
    event(usage_db, '2026-10-13T18:03:00', fingerprint='', ip='*.*.*.*')
    event(usage_db, '2026-10-13T18:04:00', page='routes', feature='routes_latest')
    event(usage_db, '2026-10-13T18:05:00', page='support', feature='support_page_view')
    result = intraday(str(usage_db), '2026-10-14', datetime(2026, 10, 14, 6, 35))
    all_scope = result['current']['all']
    assert all_scope['summary'] == {'uses': 7, 'uv': 2, 'successes': 5, 'empty_results': 1,
                                    'errors': 1, 'pv': 0, 'api_calls': 0}
    assert all_scope['hours'][1]['uses'] == 2
    assert all_scope['hours'][1]['uv'] == 1  # Same user in two modules.
    assert sum(hour['uv'] for hour in all_scope['hours']) == 3  # Cannot add hourly UV.
    assert result['current']['modules']['runs']['summary']['uv'] == 2
    assert result['current']['modules']['winrate']['summary']['uv'] == 1
    assert result['modules'] == ['runs', 'winrate']


def test_historical_day_is_complete_and_pv_api_remain_separate(usage_db):
    event(usage_db, '2026-10-12T16:00:00')
    event(usage_db, '2026-10-13T15:59:59')
    event(usage_db, '2026-10-13T16:00:00')
    with sqlite3.connect(usage_db) as conn:
        for table in ('page_view', 'api_log'):
            conn.executemany(f'INSERT INTO {table} VALUES (?, ?, ?)', [
                ('fp', '', '2026-10-12T16:00:00'),
                ('fp', '', '2026-10-13T15:59:59'),
                ('fp', '', '2026-10-13T16:00:00'),
            ])
    result = intraday(str(usage_db), '2026-10-13', datetime(2026, 10, 14, 6, 35))
    assert result['is_today'] is False
    assert result['cutoff_time'] == '24:00:00'
    assert result['current']['all']['summary']['uses'] == 2
    assert result['current']['all']['summary']['pv'] == 2
    assert result['current']['all']['summary']['api_calls'] == 2
    assert result['current']['all']['hours'][23]['uses'] == 1
    assert all(hour['state'] == 'complete' for hour in result['current']['all']['hours'])
    assert result['previous']['available'] == {'core': False, 'pv': False, 'api': False}


def test_empty_history_is_not_reported_as_collected_zeroes(usage_db):
    event(usage_db, '2026-10-13T16:00:00')
    result = intraday(str(usage_db), '2026-10-13', datetime(2026, 10, 14, 6, 35))
    assert result['current']['available']['core'] is False
    assert len(result['current']['all']['hours']) == 24
    result = intraday(str(usage_db), None, datetime(2026, 10, 14, 6, 35))
    assert result['date'] == '2026-10-14'
    assert result['current']['available']['core'] is True
    assert result['current']['available']['pv'] is False


@pytest.mark.parametrize('date', ['', '2026-1-1', '2026-02-30', 'yesterday', '2026-10-15', '2026-07-16'])
def test_invalid_future_and_expired_dates_are_rejected(usage_db, date):
    with pytest.raises(ValueError):
        intraday(str(usage_db), date, datetime(2026, 10, 14, 6, 35))


def test_writer_captures_event_time_before_queue_delay(monkeypatch):
    import web_runs.analytics as analytics
    class FrozenClock:
        @staticmethod
        def now(tz):
            return datetime(2026, 10, 13, 16, 59, 59, tzinfo=timezone.utc)
    monkeypatch.setattr(analytics, 'datetime', FrozenClock)
    writer = FeatureEventWriter.__new__(FeatureEventWriter)
    writer.queue = queue.Queue(maxsize=1)
    assert writer.submit('runs_query', 'runs', 'success', 'fp', '')
    assert not writer.submit('runs_query', 'runs', 'success', 'fp', '')
    assert writer.queue.get_nowait()[-1] == '2026-10-13T16:59:59'


def test_intraday_api_requires_auth_validates_dates_and_caches(monkeypatch, tmp_path):
    # Use the existing app harness to avoid starting real collection/OCR services.
    from test_day_stats_api import _client
    client = _client(monkeypatch, tmp_path / 'day.db')
    web_app = client.web_app
    web_app._intraday_cache.clear()
    raw_client = web_app.app.test_client()
    denied = raw_client.get('/api/stats/intraday')
    assert denied.status_code == 401
    assert denied.cache_control.no_store
    bad = client.get('/api/stats/intraday?date=invalid')
    assert bad.status_code == 400
    assert 'trace' not in bad.json
    calls = []
    monkeypatch.setattr(web_app, 'feature_intraday', lambda *args: calls.append(args) or {'date': '2026-10-14'})
    for _ in range(2):
        response = client.get('/api/stats/intraday?date=2026-10-14')
        assert response.status_code == 200
        assert response.json == {'date': '2026-10-14'}
        assert response.cache_control.private and response.cache_control.no_store
    assert len(calls) == 1
    web_app._intraday_cache.clear()
    monkeypatch.setattr(web_app, 'feature_intraday', lambda *args: (_ for _ in ()).throw(sqlite3.OperationalError('secret path')))
    failed = client.get('/api/stats/intraday')
    assert failed.status_code == 500
    assert 'secret path' not in failed.get_data(as_text=True)
