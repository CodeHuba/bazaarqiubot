import hashlib
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from flask import Flask, request
from web_runs import library_analytics as lib


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / 'stats.db')
    lib.init_library_analytics(path)
    return path


def event(action='view', n=1, **kwargs):
    return dict(id=f'event-id-{n:04d}', session='session-123456', action=action, **kwargs)


def test_idempotency_and_session_view(db):
    assert lib.record_event(db, event(), 'visitor-one')
    assert not lib.record_event(db, event(), 'visitor-one')
    assert not lib.record_event(db, event(n=2), 'visitor-one')
    assert lib.record_event(db, event('build_move', 3), 'visitor-one')
    with pytest.raises(ValueError):
        lib.record_event(db, event(), 'visitor-two')
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT COUNT(*) FROM library_event').fetchone()[0] == 2
        assert conn.execute('SELECT visitor FROM library_event LIMIT 1').fetchone()[0] == hashlib.sha256(b'visitor-one').hexdigest()


@pytest.mark.parametrize('changes', [dict(action='bad'), dict(action=[]), dict(session='x'),
    dict(id='x'), dict(detail='search private text'), dict(action='filter', detail='bad'),
    dict(draft={'private': 'data'}), dict(duration_ms=True), dict(duration_ms=-1)])
def test_validation(db, changes):
    payload = event()
    payload.update(changes)
    with pytest.raises(ValueError):
        lib.record_event(db, payload, 'visitor-one')


def test_rate_and_retention(db, monkeypatch):
    current = datetime(2026, 10, 27, 1)
    monkeypatch.setattr(lib, 'now_utc', lambda: current)
    with sqlite3.connect(db) as conn:
        conn.execute('INSERT INTO library_event VALUES (?,?,?,?,?,?,?)',
            ('old-event', 'old-visitor', 'old-session', 'view', '', None, (current-timedelta(days=91)).isoformat()))
    for n in range(240):
        assert lib.record_event(db, event('build_move', n), 'visitor-one')
    with pytest.raises(OverflowError):
        lib.record_event(db, event('build_move', 241), 'visitor-one')
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT COUNT(*) FROM library_event').fetchone()[0] == 240


def test_report_distinct_users_rates_and_beijing_day(db, monkeypatch):
    current = datetime(2026, 10, 27, 2)
    monkeypatch.setattr(lib, 'now_utc', lambda: current)
    for n, action in enumerate(['view', 'build_add', 'build_move', 'export_success']):
        detail = {'build_add': 'item', 'export_success': 'complete'}.get(action, '')
        lib.record_event(db, event(action, n, detail=detail, duration_ms=500), 'visitor-one')
    lib.record_event(db, event('view', 4), 'visitor-two')
    lib.record_event(db, event('search', 5), 'visitor-two')
    with sqlite3.connect(db) as conn:
        conn.executescript('CREATE TABLE acquisition_visit(id TEXT,visitor TEXT);CREATE TABLE acquisition_view(visit_id TEXT,created_at TEXT);')
        for n, visitor in enumerate(['visitor-one', 'visitor-two', 'visitor-three']):
            conn.execute('INSERT INTO acquisition_visit VALUES (?,?)', (str(n), hashlib.sha256(visitor.encode()).hexdigest()))
            conn.execute('INSERT INTO acquisition_view VALUES (?,?)', (str(n), current.isoformat()))
        # Outside the selected Beijing day, including the next day's boundary.
        for n, stamp in enumerate(['2026-10-26T15:59:59', '2026-10-27T16:00:00']):
            conn.execute('INSERT INTO library_event VALUES (?,?,?,?,?,?,?)',
                (f'outside-{n}', 'outside', f'outside-session-{n}', 'view', '', None, stamp))
    report = lib.library_report(db, 1, current.replace(tzinfo=timezone.utc))
    s = report['summary']
    assert s == dict(views=2, visitors=2, builders=1, exporters=1, site_visitors=3, penetration=2/3, build_rate=.5, export_rate=.5)
    assert report['daily'] == [dict(day='2026-10-27', views=2, visitors=2, builders=1, exporters=1)]
    actions = {r['action']: r for r in report['actions']}
    assert actions['export_success']['avg_ms'] == 500
    assert actions['build_move']['avg_ms'] is None


def test_http_auth_empty_state_and_payload_size(db):
    app = Flask(__name__)
    app.register_blueprint(lib.create_library_analytics_blueprint(lambda: db, lambda: request.headers.get('X-Admin') == 'yes'))
    client = app.test_client()
    assert client.get('/api/stats/library').status_code == 401
    for days in ['0', '91', 'bad']:
        assert client.get('/api/stats/library?days='+days, headers={'X-Admin': 'yes'}).status_code == 400
    response = client.get('/api/stats/library', headers={'X-Admin': 'yes'})
    assert response.status_code == 200 and 'no-store' in response.headers['Cache-Control']
    assert response.json['summary']['build_rate'] is None
    assert client.post('/api/track/library', json=event(), headers={'X-Fingerprint': 'visitor-one'}).json['recorded']
    assert not client.post('/api/track/library', json=event(), headers={'X-Fingerprint': 'visitor-one'}).json['recorded']
    assert client.post('/api/track/library', data='a'*2049).status_code == 413
