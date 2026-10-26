import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from flask import Flask, jsonify

from web_runs.acquisition import acquisition_report, create_acquisition_blueprint, init_acquisition, record_query


@pytest.fixture
def client(tmp_path):
    db = tmp_path/'stats.db'
    init_acquisition(str(db))
    app = Flask(__name__)
    app.register_blueprint(create_acquisition_blueprint(lambda:str(db), lambda:False))
    @app.post('/api/track/search')
    def search():
        return jsonify(recorded=True)
    result = app.test_client()
    result.db = db
    return result


def visit(client, visitor='browser_one_0001', visit_id='visit_one_000001', view_id='page_one_000001', **kwargs):
    return client.post('/api/track/visit', json=dict(visit_id=visit_id, view_id=view_id, path='/builds', **kwargs),
                       headers={'X-Fingerprint':visitor})


def report(client):
    return acquisition_report(str(client.db), 7)


def test_first_touch_is_preserved_and_duplicate_views_are_idempotent(client):
    assert visit(client, source='bilibili', medium='video', campaign='weekly', content='intro').status_code == 200
    assert visit(client, source='qq', view_id='page_two_000001').status_code == 200
    assert visit(client, source='qq', view_id='page_two_000001').status_code == 200
    data = report(client)
    assert data['summary'] == dict(uv=1, visits=1, pv=2, queries=0, query_uv=0, query_rate=0)
    assert data['rows'][0]['source'] == 'bilibili'
    assert data['rows'][0]['campaign'] == 'weekly'
    assert 'browser_one_0001' not in json.dumps(data)


def test_channel_uv_cannot_be_added_and_multiple_queries_do_not_multiply_pv(client):
    visit(client, source='bilibili')
    visit(client, visit_id='visit_two_000001', view_id='page_two_000001', source='qq')
    for action in ('query_one_000001','query_two_000001'):
        record_query(str(client.db), 'visit_one_000001', 'browser_one_0001', action, 'runs')
    record_query(str(client.db), 'visit_one_000001', 'browser_one_0001', 'query_one_000001', 'runs')
    data = report(client)
    assert len(data['rows']) == 2 and sum(r['uv'] for r in data['rows']) == 2
    assert data['summary'] == dict(uv=1, visits=2, pv=2, queries=2, query_uv=1, query_rate=1)


@pytest.mark.parametrize('referrer,expected', [
    ('https://www.google.com/search?q=private','google'),
    ('https://www.google.com.hk/search?q=private','google'),
    ('https://www.bing.com/search?q=private','bing'),
    ('https://www.baidu.com/s?wd=private','baidu'),
    ('https://evilgoogle.com/search','referral'),
    ('https://bazaarqiubot.com/runs?run=private','direct'),
    ('javascript:alert(1)','direct'),
])
def test_referrer_classification_only_stores_host(client, referrer, expected):
    assert visit(client, referrer=referrer).status_code == 200
    assert report(client)['rows'][0]['source'] == expected
    with sqlite3.connect(client.db) as conn:
        raw = str(conn.execute('SELECT * FROM acquisition_visit').fetchall())
    assert 'private' not in raw and '?q=' not in raw and '/search' not in raw


def test_validation_and_visit_ownership(client):
    assert client.post('/api/track/visit', json=[]).status_code == 400
    assert client.post('/api/track/visit', data='x'*5000).status_code == 413
    assert visit(client).status_code == 200
    assert visit(client, visitor='different_device_01').status_code == 400
    for path in ('/stats','/unknown','/runs?run=secret','//evil.test','/trivia/0'):
        payload = dict(visit_id='other_visit_0001', view_id='other_page_00001', path=path)
        assert client.post('/api/track/visit', json=payload, headers={'X-Fingerprint':'browser_one_0001'}).status_code == 400
    record_query(str(client.db), 'visit_one_000001', 'different_device_01', 'query_one_000001', 'runs')
    assert report(client)['summary']['queries'] == 0


def test_conversion_only_after_signed_user_search_and_admin_report_is_protected(client):
    visit(client)
    headers={'X-Fingerprint':'browser_one_0001','X-Visit-Id':'visit_one_000001'}
    client.post('/api/track/search', json=dict(id='query_one_000001', feature='runs'), headers=headers)
    assert report(client)['summary']['queries'] == 0
    client.post('/api/track/search', json=dict(id='query_one_000001', feature='runs', receipt='already-verified-by-search-endpoint'), headers=headers)
    assert report(client)['summary']['queries'] == 1
    assert client.get('/api/stats/acquisition').status_code == 401


def test_report_beijing_midnight_and_queries_for_visits_started_before_period(client):
    visit(client)
    with sqlite3.connect(client.db) as conn:
        conn.execute("UPDATE acquisition_visit SET created_at='2026-10-17T15:00:00'")
        conn.execute("UPDATE acquisition_view SET created_at='2026-10-18T16:00:00'")
        conn.execute("INSERT INTO acquisition_query VALUES ('query_one_000001','visit_one_000001','runs','2026-10-18T16:00:01')")
    data = acquisition_report(str(client.db), 1, datetime(2026,10,19,0,0,tzinfo=timezone.utc))
    assert data['summary']['pv'] == 1 and data['summary']['queries'] == 1
    data = acquisition_report(str(client.db), 1, datetime(2026,10,18,15,59,tzinfo=timezone.utc))
    assert data['summary']['pv'] == 0 and data['summary']['queries'] == 0
