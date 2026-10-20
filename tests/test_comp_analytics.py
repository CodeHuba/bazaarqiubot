import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from flask import Flask

from web_runs import comp_analytics as comp
from web_runs.search_analytics import init_search_schema


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path/'stats.db')
    init_search_schema(path)
    comp.init_comp_analytics(path)
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE api_log(endpoint TEXT,success INTEGER,duration_ms INTEGER,created_at TEXT)')
    return path


def event(n=1, **kwargs):
    return dict(id=f'event-id-{n:08d}', action='view', **kwargs)


def test_idempotent_owner_hashed_identity_and_rate_limit(db):
    assert comp.record_event(db, event(), 'visitor-one')
    assert not comp.record_event(db, event(), 'visitor-one')
    with pytest.raises(ValueError):
        comp.record_event(db, event(), 'visitor-two')
    for n in range(2, 181):
        comp.record_event(db, event(n), 'visitor-one')
    with pytest.raises(OverflowError):
        comp.record_event(db, event(181), 'visitor-one')
    assert not comp.record_event(db, event(), 'visitor-one')
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT COUNT(*) FROM comp_event').fetchone()[0] == 180
        assert conn.execute('SELECT visitor FROM comp_event LIMIT 1').fetchone()[0] != 'visitor-one'


@pytest.mark.parametrize('changes', [dict(action='unknown'), dict(action=[]),dict(detail='raw-url'),
    dict(group='a'*513),dict(group='\n'),dict(duration_ms=True),dict(duration_ms=-1),dict(duration_ms=3600001),
    dict(id='bad'),dict(action='sort',detail='bad'),dict(action='share_error',detail='private stacktrace')])
def test_invalid_payload(db, changes):
    value=event();value.update(changes)
    with pytest.raises(ValueError):
        comp.record_event(db,value,'visitor-one')


def test_retention_once_daily(db, monkeypatch):
    now=datetime(2026,10,19,18)
    monkeypatch.setattr(comp,'now_utc',lambda:now)
    with sqlite3.connect(db) as conn:
        conn.execute('INSERT INTO comp_event VALUES (?,?,?,?,?,?,?)',
            ('old-event','visitor','view','','',None,(now-timedelta(days=91)).isoformat()))
    comp.record_event(db,event(),'visitor-one')
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT COUNT(*) FROM comp_event').fetchone()[0]==1


def test_report_uses_beijing_period_and_distinct_query_scope(db):
    now=datetime(2026,10,19,3,tzinfo=timezone.utc)
    inside='2026-10-18T16:00:00'
    outside='2026-10-18T15:59:59'
    future='2026-10-19T16:00:00'
    with sqlite3.connect(db) as conn:
        for n,stamp in enumerate((inside,inside,inside,outside,future)):
            conn.execute('INSERT INTO search_event VALUES (?,?,?,?,?,?,?,?,?,?)',
                (str(n),'same-visitor','comp_card','key',json.dumps({'groups':[[{},{}]]}),'[]',
                 'success' if n==0 else 'insufficient','network',1,stamp))
        conn.execute('INSERT INTO search_event VALUES (?,?,?,?,?,?,?,?,?,?)',
            ('run','other','runs','key','{}','[]','success','network',1,inside))
        for n,action in enumerate(('share_generate','share_generate','share_generated','sort','sort')):
            conn.execute('INSERT INTO comp_event VALUES (?,?,?,?,?,?,?)',
                (str(n),'visitor',action,'group','rate' if action=='sort' else 'complete' if action=='share_generated' else '',
                 2000 if action=='share_generated' else None,inside))
        conn.executemany('INSERT INTO api_log VALUES (?,?,?,?)',[
            ('/api/comp/card',1,100,inside),('/api/comp/card',0,300,inside),('/api/runs',1,999,inside),('/api/comp/card',1,999,outside)])
    report=comp.comp_report(db,1,now)
    assert report['queries']==dict(count=3,visitors=1,outcomes={'success':1,'insufficient':2},result_rate=1/3,item_counts={2:3})
    assert report['requests']==dict(count=2,avg_ms=200,errors=1)
    actions={r['action']:r for r in report['actions']}
    assert actions['share_generate']['count']==2
    assert actions['sort']['count']==2 and actions['sort']['visitors']==1
    assert actions['share_generated']['avg_ms']==2000
    assert report['sorts']==[dict(detail='rate',count=2)]


def test_http_auth_validation_and_empty_state(db):
    app=Flask(__name__)
    from flask import request
    app.register_blueprint(comp.create_comp_analytics_blueprint(lambda:db,lambda:request.headers.get('X-Admin')=='yes'))
    client=app.test_client()
    assert client.get('/api/stats/comp').status_code==401
    assert client.get('/api/stats/comp?days=0',headers={'X-Admin':'yes'}).status_code==400
    response=client.get('/api/stats/comp',headers={'X-Admin':'yes'})
    assert response.status_code==200 and response.headers['Cache-Control']=='private, no-store'
    assert response.json['queries']['result_rate'] is None
    assert response.json['requests']['avg_ms'] is None
    assert client.post('/api/track/comp',data='a'*2049).status_code==413
    assert client.post('/api/track/comp',json=[]).status_code==400
    assert client.post('/api/track/comp',json=event()).status_code==400
    for expected in (True,False):
        response=client.post('/api/track/comp',json=event(),headers={'X-Fingerprint':'visitor-one'})
        assert response.status_code==200 and response.json['recorded']==expected
