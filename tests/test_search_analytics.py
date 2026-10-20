"""Search actions, verified outcomes and rankings must share one documented meaning."""
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'web_runs'))
import search_analytics as sa

NOW = datetime(2026, 10, 16, 5)
SECRET = 'only-a-test-key'


def resolve(name):
    return {'a':('a','甲'), '甲':('a','甲'), '别名':('a','甲'),
            'b':('b','乙'), '乙':('b','乙')}.get(name.casefold())


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(sa, '_now', lambda: NOW)
    path = tmp_path / 'search.db'
    sa.init_search_schema(path)
    return path


def action(db, n=0, feature='runs', params=None, data=None, status=200,
           visitor='visitor_0001', now=None, source='network', **extra):
    params = params if params is not None else {'cards':'a'}
    data = data if data is not None else {'total':4}
    receipt = sa.issue_receipt(feature,params,data,status,SECRET,resolve,18,'18.2')
    payload = dict(id=f'search_action_{n:06}',feature=feature,receipt=receipt,source=source,**extra)
    return sa.record_search(db,payload,visitor,SECRET,resolve,18,'18.2',now=now or NOW)


def test_aliases_order_filters_and_comparison_are_distinct():
    first = sa.normalize_query('runs',{'cards':'a+乙','hero':'瓦内莎'},resolve,18,'18.2')
    second = sa.normalize_query('runs',{'cards':'b+别名','hero':'Vanessa'},resolve,18,'18.2')
    assert first == second
    assert sa.condition_key('runs',first) == sa.condition_key('runs',second)
    enchanted = sa.normalize_query('runs',{'card_filters':json.dumps([{'name':'甲','enchantment':'Fiery'},{'name':'乙'}]),'hero':'Vanessa'},resolve,18,'18.2')
    assert sa.condition_key('runs',first) != sa.condition_key('runs',enchanted)
    assert '炽焰' in sa.condition_label('runs',enchanted)
    compare = sa.normalize_query('winrate',{'cards':'a,b','multi':'1'},resolve)
    joint = sa.normalize_query('winrate',{'cards':'a+b','multi':'1'},resolve)
    assert len(compare['groups']) == 2 and len(joint['groups']) == 1
    assert sa.condition_key('winrate',compare) != sa.condition_key('winrate',joint)
    assert sa.normalize_query('runs',{'hero':'Vanessa'})['groups'] == [[]]
    assert sa.normalize_query('runs',{'min_wins':'0'})['min_wins'] == 0


@pytest.mark.parametrize('count,groups,outcome',[(0,[],'empty'),(8,[],'insufficient'),(35,[{}],'success')])
def test_new_comp_receipt_binds_actual_items_scope_and_baseline(count,groups,outcome):
    data={'anchors':[{'cardId':'b','name':'乙'},{'cardId':'a','name':'甲'}],
          'scope':{'season':19,'phase':'19.2','rank':'all','hero':'Vanessa','days':7},
          'baseline':{'count':count},'groups':groups}
    token=sa.issue_receipt('comp_card',{'card_ids':'["a","b"]','all_phases':'true'},data,200,SECRET,resolve,18,'18.2')
    receipt=sa.read_receipt(token,SECRET)
    assert receipt['units']==[[outcome,count]]
    assert [c['key'] for c in receipt['params']['groups'][0]]==['a','b']
    assert receipt['params']['season']==19 and receipt['params']['phase']=='19.2'
    assert receipt['params']['days']==7 and not receipt['params']['all_phases']


def test_dedupe_identity_retries_and_changed_conditions(db):
    assert action(db,0)['recorded']
    assert action(db,1,params={'cards':'别名'},now=NOW+timedelta(seconds=9))['deduplicated']
    assert action(db,2,params={'cards':'a','day':'3'},now=NOW+timedelta(seconds=2))['recorded']
    assert action(db,3,visitor='visitor_0002',now=NOW+timedelta(seconds=3))['recorded']
    assert action(db,4,feature='partner',params={'card':'a'},data={'target_total':20,'by_appear':[{}]})['recorded']
    assert action(db,5,now=NOW+timedelta(seconds=10))['recorded']
    assert action(db,0,now=NOW+timedelta(seconds=30))['deduplicated']
    rows = sa.search_rankings(db,now=NOW+timedelta(minutes=1))['rows']
    assert len(rows)==1 and rows[0]['searches']==5 and rows[0]['visitors']==2
    assert rows[0]['features']=={'runs':4,'partner':1}


def test_dedupe_uses_submit_time_even_when_responses_finish_out_of_order(db):
    assert action(db,1,now=NOW+timedelta(seconds=20),elapsed_ms=16000)['recorded']
    assert action(db,2,now=NOW+timedelta(seconds=25),elapsed_ms=25000)['deduplicated']


def test_receipts_cannot_be_forged_and_cache_hits_are_real_actions(db,monkeypatch):
    token = sa.issue_receipt('partner',{'card':'a'}, {'by_appear':[{}],'target_total':8},200,SECRET,resolve)
    payload = dict(id='signed_search_0001',feature='partner',receipt=token,source='browser_cache',params={'card':'b'},fallback='empty')
    assert sa.record_search(db,payload,'visitor_0001',SECRET,now=NOW)['recorded']
    payload['id'] = 'signed_search_0002'
    assert sa.record_search(db,payload,'visitor_0001',SECRET,now=NOW+timedelta(seconds=11))['recorded']
    row = sa.search_rankings(db,now=NOW+timedelta(seconds=12))['rows'][0]
    assert row['label']=='甲' and row['outcomes']=={'success':2}
    detail=sa.search_rankings(db,item=row['key'],now=NOW+timedelta(seconds=12))['detail']
    assert detail['sources']=={'browser_cache':2}
    assert 'visitor_0001' not in json.dumps(detail) and 'signed_search' not in json.dumps(detail)
    with pytest.raises(ValueError): sa.read_receipt(token+'x',SECRET)
    with pytest.raises(ValueError): sa.read_receipt(token,'wrong-key')
    monkeypatch.setattr(sa,'_now',lambda:NOW+timedelta(days=2))
    with pytest.raises(ValueError): sa.read_receipt(token,SECRET)


def test_response_alias_only_when_business_query_confirms_it(db):
    receipt = sa.issue_receipt('comp_card',{'card':'模糊输入'}, {'card':'甲','total_runs':20,'recommendations':[{}]},200,SECRET,resolve)
    body=sa.read_receipt(receipt,SECRET)
    assert body['params']['groups'][0][0]['key']=='a'
    receipt = sa.issue_receipt('winrate',{'cards':'模糊输入,b','multi':'1'}, {'results':[{'card_names':['甲'],'total':1},{'card_names':['乙'],'total':2}]},200,SECRET,resolve)
    assert [group[0]['key'] for group in sa.read_receipt(receipt,SECRET)['params']['groups']]==['a','b']


@pytest.mark.parametrize('feature,data,status,expected',[
    ('runs',{'total':0},200,'empty'),('runs',{'total':4},200,'success'),
    ('partner',{'target_total':8,'by_winrate':[],'by_appear':[{}]},200,'success'),
    ('partner',{'target_total':8,'by_winrate':[]},200,'insufficient'),
    ('partner',{'target_total':0},200,'empty'),('partner',{'not_found':True},200,'unrecognized'),
    ('comp_card',{'total_runs':8,'recommendations':[]},200,'insufficient'),
    ('comp_card',{'total_runs':8,'layers':[{}]},200,'success'),
    ('comp_card',{'error':'unknown'},404,'unrecognized'),('runs',{'error':'failed'},500,'error'),
    ('runs',{},400,'invalid'),('runs',{},429,'throttled'),
    ('winrate',{'total':0,'not_found':['a']},200,'unrecognized'),
])
def test_business_result_classification(feature,data,status,expected):
    assert sa.classify(feature,data,status)[0][0]==expected


def test_compare_units_do_not_invent_joint_combinations_or_double_count_cards(db):
    action(db,1,feature='winrate',params={'cards':'a,a+b,b','multi':'1'},data={'results':[{'total':2},{'total':0},{'total':0}]})
    rows={row['label']:row for row in sa.search_rankings(db,now=NOW)['rows']}
    assert rows['甲']['searches']==rows['乙']['searches']==1
    assert rows['甲']['outcomes']=={'partial':1} and rows['乙']['outcomes']=={'empty':1}
    condition=sa.search_rankings(db,board='conditions',now=NOW)['rows'][0]
    assert condition['searches']==1 and condition['outcomes']=={'partial':1}
    assert sa.search_rankings(db,combos=True,now=NOW)['total']==2
    action(db,2,feature='winrate',params={'cards':'a,b','multi':'1'},data={'results':[{'total':1},{'total':0}]},now=NOW+timedelta(seconds=1))
    assert sa.search_rankings(db,board='conditions',combos=True,now=NOW+timedelta(seconds=2))['total']==1


def test_empty_watch_threshold_and_denominator_exclude_errors_and_unknowns(db):
    for n in range(4): action(db,n,data={'total':0},visitor=f'visitor_{n:04}')
    assert sa.search_rankings(db,board='empty',now=NOW)['total']==0
    action(db,5,visitor='visitor_0005',data={'total':1})
    action(db,6,visitor='visitor_0006',status=500,data={'error':'fail'})
    action(db,7,visitor='visitor_0007',status=404,data={'error':'unknown'})
    for n in (8,9):
        sa.record_search(db,dict(id=f'unverified_{n:06}',feature='runs',params={'cards':'a'},fallback='unverified'),f'visitor_{n:04}',SECRET,resolve,18,'18.2',now=NOW)
    row=sa.search_rankings(db,board='empty',now=NOW)['rows'][0]
    assert row['searches']==9 and row['completed']==5 and row['empty_rate']==80
    assert row['outcomes']=={'empty':4,'success':1,'error':1,'unrecognized':1,'unverified':2}
    assert action(db,20,status=400)['ignored'] and action(db,21,status=429)['ignored']


def test_pagination_filters_china_midnight_and_schema_idempotence(db):
    since=sa.search_rankings(db,now=NOW)['tracking_since']
    sa.init_search_schema(db)
    assert sa.search_rankings(db,now=NOW)['tracking_since']==since
    for n in range(23):
        action(db,n,params={'card_filters':[{'name':f'卡{n}','enchantment':'Fiery'}],'hero':'Vanessa','rank':'legendary','day':'3'},visitor=f'visitor_{n:04}')
    result=sa.search_rankings(db,limit=20,hero='Vanessa',rank='legendary',day='3',enchantment='Fiery',now=NOW)
    assert result['total']==23 and len(result['rows'])==20
    rest=sa.search_rankings(db,offset=20,limit=20,now=NOW)
    assert len(rest['rows'])==3 and not {r['key'] for r in result['rows']} & {r['key'] for r in rest['rows']}
    assert sa.search_rankings(db,hero='Mak',now=NOW)['total']==0
    assert sa.search_rankings(db,enchantment='none',now=NOW)['total']==0
    before_midnight=NOW.replace(hour=15,minute=59)-timedelta(days=1)
    after_midnight=NOW.replace(hour=16,minute=1)-timedelta(days=1)
    action(db,30,params={'cards':'a'},now=before_midnight)
    action(db,31,params={'cards':'b'},now=after_midnight)
    one_day=sa.search_rankings(db,days=1,now=NOW)
    assert '甲' not in [r['label'] for r in one_day['rows']] and '乙' in [r['label'] for r in one_day['rows']]
    assert one_day['comparison_available'] is False
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT visitor FROM search_event LIMIT 1').fetchone()[0]!='visitor_0000'


@pytest.mark.parametrize('kwargs',[{'board':'bad'},{'feature':'bad'},{'days':91},{'limit':0},{'offset':-1},{'day':'-2'},{'hero':'bad'},{'rank':'bad'}])
def test_invalid_admin_filters(db,kwargs):
    with pytest.raises(ValueError): sa.search_rankings(db,**kwargs)
