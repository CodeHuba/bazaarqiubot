import json
import sqlite3

import pytest

from test_day_stats_api import _client, stats_db


@pytest.fixture
def client(monkeypatch,stats_db):
    client = _client(monkeypatch,stats_db)
    app = client.web_app
    monkeypatch.setattr(app,'_card_search_index',[{'en':'A','zh':'甲','cardId':'a'}])
    app._rate_limit.clear()
    # Keep this test's query-log writes inside its temporary database too.
    monkeypatch.setattr(app,'QUERY_STATS_DB',str(stats_db.parent/'query.db'))
    return client


def test_search_stats_require_auth_and_never_cache(client):
    raw=client.web_app.app.test_client()
    assert raw.get('/api/stats/search').status_code==401
    response=client.get('/api/stats/search')
    assert response.status_code==200
    assert response.get_json()['version']==2
    assert 'private' in response.headers['Cache-Control'] and 'no-store' in response.headers['Cache-Control']
    for query in ('board=no','days=91','day=-1','hero=bad','offset=-1'):
        assert client.get('/api/stats/search?'+query).status_code==400


def test_comp_analytics_is_registered_and_monitoring_separates_requests_from_manual_queries(client,monkeypatch):
    from flask import jsonify
    app=client.web_app
    raw=app.app.test_client()
    assert raw.get('/api/stats/comp').status_code==401
    monkeypatch.setitem(app.app.view_functions,'api_comp_card',lambda:jsonify(
        anchors=[{'cardId':'a','name':'甲'}],scope={'season':19,'phase':'19.2'},baseline={'count':8},groups=[]))
    headers={'X-Fingerprint':'comp_visitor_0001','X-Search-Action':'user'}
    response=raw.get('/api/comp/card?cards=["甲"]',headers=headers)
    assert response.status_code==200 and response.headers.get('X-Search-Receipt')
    tracked=raw.post('/api/track/search',json={'id':'comp_query_000001','feature':'comp_card','receipt':response.headers['X-Search-Receipt']},headers=headers)
    assert tracked.status_code==200 and tracked.json['recorded']
    raw.get('/api/comp/card?cards=["甲"]')  # Automatic load: a request, no search action.
    raw.post('/api/track/comp',json={'id':'comp_event_0001','action':'share_open','group':'Vanessa:a'},headers=headers)
    report=client.get('/api/stats/comp')
    assert report.status_code==200 and report.headers['Cache-Control']=='private, no-store'
    data=report.json
    assert data['queries']['count']==1 and data['queries']['outcomes']=={'insufficient':1}
    assert data['requests']['count']==2
    assert next(r for r in data['actions'] if r['action']=='share_open')['count']==1


def test_track_requires_anonymous_identity_valid_action_and_backend_proof(client):
    raw=client.web_app.app.test_client()
    payload=dict(id='manual_search_0001',feature='runs',params={'cards':'甲'},fallback='unverified')
    assert raw.post('/api/track/search',json=payload).status_code==400
    headers={'X-Fingerprint':'browser_test_0001'}
    assert raw.post('/api/track/search',json=payload,headers=headers).get_json()['recorded']
    assert raw.post('/api/track/search',json=payload,headers=headers).get_json()['deduplicated']
    payload.update(id='manual_search_0002',fallback='success')
    assert raw.post('/api/track/search',json=payload,headers=headers).status_code==400
    assert raw.post('/api/track/search',data='x'*40001,headers=headers).status_code==413
    data=client.get('/api/stats/search').get_json()
    assert data['rows'][0]['outcomes']=={'unverified':1}
    assert data['rows'][0]['empty_rate'] is None
    assert 'browser_test_0001' not in json.dumps(data)


@pytest.mark.parametrize('name,expected',[('别名','empty'),('不存在','unrecognized')])
def test_runs_receipt_distinguishes_unknown_from_no_matches_without_changing_response(client,monkeypatch,name,expected):
    app=client.web_app
    class Query:
        def __init__(self,**kwargs): pass
        def load(self): pass
        def query(self,**kwargs): return {'runs':[],'total':0,'pages':0,'page':1}
        def find_card_ids(self,name): return ['a'] if name=='别名' else []
        def card_display_info(self,card_id): return {'name':'甲'}
    monkeypatch.setattr(app,'RunsQuery',Query)
    raw=app.app.test_client()
    response=raw.get('/api/runs',query_string={'cards':name},headers={'X-Search-Action':'user'})
    assert response.status_code==200 and response.get_json()=={'runs':[],'total':0,'pages':0,'page':1}
    assert 'X-Search-Action' in response.headers['Vary']
    token=response.headers['X-Search-Receipt']
    tracked=raw.post('/api/track/search',json={'id':'manual_search_0001','feature':'runs','receipt':token},headers={'X-Fingerprint':'browser_test_0001'})
    assert tracked.status_code==200 and tracked.get_json()['recorded']
    row=client.get('/api/stats/search').get_json()['rows'][0]
    assert row['outcomes']=={expected:1}
    assert row['label']==('甲' if name=='别名' else '不存在')


def test_receipts_cover_four_entries_with_real_http_error_statuses(client,monkeypatch):
    from flask import jsonify
    app=client.web_app
    cases=[('api_runs','/api/runs?cards=甲','runs',{'total':8},200,'success'),
           ('api_comp_card','/api/comp/card?card=甲','comp_card',{'error':'未找到'},404,'unrecognized'),
           ('api_winrate','/api/winrate?cards=甲&multi=1','winrate',{'results':[{'total':0}]},200,'empty'),
           ('api_partner','/api/partner?card=甲','partner',{'target_total':2,'by_winrate':[]},200,'insufficient')]
    raw=app.app.test_client()
    for n,(endpoint,url,feature,data,status,outcome) in enumerate(cases):
        monkeypatch.setitem(app.app.view_functions,endpoint,lambda data=data,status=status:(jsonify(data),status))
        response=raw.get(url,headers={'X-Search-Action':'user'})
        assert response.status_code==status and response.get_json()==data
        tracked=raw.post('/api/track/search',json={'id':f'manual_search_{n:06}','feature':feature,'receipt':response.headers['X-Search-Receipt']},headers={'X-Fingerprint':'browser_test_0001'})
        assert tracked.get_json()['recorded']
        row=client.get('/api/stats/search?feature='+feature).get_json()['rows'][0]
        assert row['outcomes']=={outcome:1}
    result=client.get('/api/stats/search').get_json()
    assert result['rows'][0]['searches']==4 and result['rows'][0]['visitors']==1
    assert result['summary']['searches']==4


def test_analytics_failures_do_not_break_searches_or_create_false_success(client,monkeypatch):
    from flask import jsonify
    app=client.web_app
    monkeypatch.setitem(app.app.view_functions,'api_runs',lambda:jsonify({'total':1,'runs':[]}))
    def fail(*args,**kwargs): raise sqlite3.OperationalError('test failure')
    monkeypatch.setattr(app,'issue_receipt',fail)
    response=app.app.test_client().get('/api/runs?cards=甲',headers={'X-Search-Action':'user'})
    assert response.status_code==200 and response.get_json()['total']==1
    assert 'X-Search-Receipt' not in response.headers
    monkeypatch.setattr(app,'search_rankings',fail)
    assert client.get('/api/stats/search').status_code==503
