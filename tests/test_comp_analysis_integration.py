import gzip
import importlib.util
import json
from pathlib import Path

import pytest

from plugins.bazaar_plugin.data_client import CURRENT_PHASE

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('comp_staging',ROOT/'tools/comp_preview/staging.py')
staging=importlib.util.module_from_spec(spec)
spec.loader.exec_module(staging)


@pytest.fixture
def client(tmp_path, monkeypatch):
    from plugins.bazaar_plugin import comp_analysis_store as store
    monkeypatch.setattr(store, '_REFRESH_MIN_INTERVAL', 0)
    cards={cid:{'name':cid,'internalName':cid,'type':kind} for cid,kind in
           [('q','Item'),('a','Item'),('b','Item'),('s','Skill'),('unplayed','Item')]}
    cards['q']['heroes']=['Vanessa']
    cards['a']['heroes']=['Common']
    rows=[dict(id=str(i),hero='Vanessa',created_at='2026-10-18T12:00:00Z',
               wins=10 if i<40 else 5,losses=2,rank='Legendary',screenshot='',
               items=[{'cardId':'q','tier':'Gold'},{'cardId':'a' if i<40 else 'b'}],
               skills=[{'cardId':'s','tier':'Silver'}]) for i in range(60)]
    payload=dict(runs=rows,cards=cards,season=19,phase=CURRENT_PHASE,captured_at='2026-10-19T00:00:00Z',source='test')
    snapshot=tmp_path/'snapshot.json.gz'
    snapshot.write_bytes(gzip.compress(json.dumps(payload).encode()))
    app=staging.create_app(snapshot)
    app.testing=True
    return app.test_client()


def test_actual_comp_handler_supports_multi_item_ids_and_exact_names(client):
    response=client.get('/comp-preview/api/comp/card',query_string={'cards':'["q","a"]','card_ids':'["q","a"]'})
    assert response.status_code==200
    data=response.get_json()
    assert data['baseline']['count']==40
    assert data['baseline']['rate']==1
    assert data['baseline']['average_wins']==10
    assert {c['cardId'] for c in data['anchors']}=={'q','a'}
    assert len(data['examples'])==1
    assert data['examples'][0]['similar_ten_wins']==40
    from plugins.bazaar_plugin import comp_analysis_store as store
    db_path=client.application.view_functions['api_comp_card'].__globals__['RUNS_DB_PATH']
    assert store._engines[(str(Path(db_path).resolve()),19,CURRENT_PHASE)].engine.catalog['q']['heroes']==['Vanessa']
    assert client.get('/comp-preview/api/comp/card?card=q').get_json()['baseline']['count']==60


def test_input_rejection_and_known_item_without_appearances(client):
    for args in ({'card':'s'},{'card':'q','days':'1.5'},{'card':'q','rank':'wrong'}, {'card_ids':'["wrong"]'}, {'cards':'{bad'}):
        assert client.get('/comp-preview/api/comp/card',query_string=args).status_code in (400,404)
    response=client.get('/comp-preview/api/comp/card?card=unplayed')
    assert response.status_code==200
    assert response.get_json()['baseline']['count']==0


def test_actual_list_and_detail_handlers_keep_quality_and_skills(client):
    response=client.get('/comp-preview/api/runs',query_string={'card_filters':'[{"name":"q"}]','min_wins':'10'})
    assert response.status_code==200
    assert response.get_json()['total']==40
    detail=client.get('/comp-preview/api/runs/1').get_json()['run']
    assert detail['cards'][0]['tier']=='Gold'
    assert detail['skills'][0]['tier']=='Silver'
    assert detail['losses']==2


def test_item_only_autocomplete_and_isolated_asset_paths(client):
    assert client.get('/comp-preview/api/card_search?q=s&type=item').get_json()==[]
    page=client.get('/comp-preview/runs')
    assert b'/comp-preview/static/comp-analysis.js' in page.data
    script=client.get('/comp-preview/static/comp-analysis.js').get_data(as_text=True)
    assert "'/comp-preview/api/comp/card?'" in script
    assert "'/comp-preview/runs?'" in script
    assert page.headers['X-Robots-Tag'].startswith('noindex')
    assert client.post('/comp-preview/api/ingest',json=[]).status_code in (404,405)


def test_search_analytics_retains_all_items_and_days():
    analytics_spec=importlib.util.spec_from_file_location('comp_search_analytics',ROOT/'web_runs/search_analytics.py')
    analytics=importlib.util.module_from_spec(analytics_spec)
    analytics_spec.loader.exec_module(analytics)
    data=analytics.normalize_query('comp_card',{'cards':'["q","a"]','days':'7'})
    assert len(data['groups'][0])==2
    assert data['days']==7


def test_published_local_art_metadata_preserves_analysis_metrics(client,tmp_path,monkeypatch):
    from plugins.bazaar_plugin import comp_analysis_store
    packaged=tmp_path/'catalog.json'
    packaged.write_text(json.dumps({'q':{'name':'q','artLarge':'/static/comp-art/verified.webp','size':'Large','type':'Item'}}),encoding='utf-8')
    monkeypatch.setattr(comp_analysis_store,'_ASSET_CATALOG',packaged)
    comp_analysis_store.invalidate(hard=True)
    data=client.get('/comp-preview/api/comp/card?card=q').get_json()
    assert data['baseline']['count']==60
    assert data['baseline']['ten_wins']==40
    assert data['anchors'][0]['img']=='/static/comp-art/verified.webp'
    assert data['anchors'][0]['size']=='Large'
    assert data['groups'][0]['examples'][0]['items'][0]['tier']=='Gold'


def test_live_adapter_keeps_snapshot_instance_metadata_without_unused_raw_payloads(client):
    from plugins.bazaar_plugin.comp_analysis_store import _instances
    item={'cardId':'q','tier':'Gold','isLegendary':False,'enchantment':None,'slotPosition':0,'large_unused_payload':{'nested':['unused']}}
    result=_instances([item],{},'items')[0]
    assert result=={k:v for k,v in item.items() if k!='large_unused_payload'}
    result=_instances([{'cardId':'s'}],{'skills':[{'cardId':'s','tierOverride':'Silver'}]},'skills')[0]
    assert result['tier']=='Silver'


def test_refresh_reuses_unchanged_rows_and_recovers_changed_and_new_runs(client):
    import sqlite3
    from plugins.bazaar_plugin import comp_analysis_store as store
    namespace=client.application.view_functions['api_comp_card'].__globals__
    path=namespace['RUNS_DB_PATH']
    key=(str(Path(path).resolve()),19,CURRENT_PHASE)
    entry=store._engines[key]
    old=entry.engine;old_rows={r['id']:r for r in old.runs}
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE runs SET stat_wins=3 WHERE id='0'")
        conn.execute("INSERT INTO runs SELECT 'new-run',hero,username,created_at,items_json,skills_json,combats_json,stat_wins,stat_losses,screenshot_url,player_rank,season,phase,card_ids_text,raw_json FROM runs WHERE id='1'")
    new=store.prewarm(entry.factory,season=19,phase=CURRENT_PHASE,wait=True)
    new_rows={r['id']:r for r in new.runs}
    assert new_rows['1'] is old_rows['1']
    assert new_rows['0'] is not old_rows['0']
    assert old_rows['0']['wins']==10 and new_rows['0']['wins']==3
    assert len(new.runs)==61 and len(old.runs)==60
    response=client.get('/comp-preview/api/comp/card?card=q')
    assert response.json['baseline']['count']==61
    assert response.json['baseline']['ten_wins']==40
    assert b'_source_digest' not in response.data and b'_validated_run' not in response.data


def test_list_and_analysis_follow_committed_insert_update_delete(client):
    import sqlite3
    import time
    from plugins.bazaar_plugin import comp_analysis_store as store
    namespace = client.application.view_functions['api_comp_card'].__globals__
    path = namespace['RUNS_DB_PATH']
    args = {'card_filters': '[{"name":"q"}]', 'min_wins': '10'}
    assert client.get('/comp-preview/api/runs', query_string=args).json['total'] == 40
    assert client.get('/comp-preview/api/comp/card?card=q').json['baseline']['count'] == 60

    def await_analysis(count, ten_wins):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            response = client.get('/comp-preview/api/comp/card?card=q')
            assert response.status_code == 200
            baseline = response.json['baseline']
            if baseline['count'] == count and baseline['ten_wins'] == ten_wins:
                return
            time.sleep(.01)
        raise AssertionError('Committed records did not reach analysis results')

    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO runs SELECT 'fresh-run',hero,username,created_at,items_json,skills_json,combats_json,stat_wins,stat_losses,screenshot_url,player_rank,season,phase,card_ids_text,raw_json FROM runs WHERE id='1'")
    # Same post-commit hook as /api/ingest; no manual engine rebuild.
    store.invalidate()
    assert client.get('/comp-preview/api/runs', query_string=args).json['total'] == 41
    await_analysis(61, 41)
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE runs SET stat_wins=3 WHERE id='fresh-run'")
    # External writes are also detected by the next analysis query.
    assert client.get('/comp-preview/api/runs', query_string=args).json['total'] == 40
    await_analysis(61, 40)
    with sqlite3.connect(path) as conn:
        conn.execute("DELETE FROM runs WHERE id='fresh-run'")
    await_analysis(60, 40)


def test_background_watcher_refreshes_external_insert_without_a_query(client, monkeypatch):
    import sqlite3
    import time
    from threading import Event
    from plugins.bazaar_plugin import comp_analysis_store as store
    namespace = client.application.view_functions['api_comp_card'].__globals__
    path = namespace['RUNS_DB_PATH']
    entry = store._engines[(str(Path(path).resolve()), 19, CURRENT_PHASE)]
    checked = Event()
    original = store.prewarm
    def watched_check(*args, **kwargs):
        value = original(*args, **kwargs)
        checked.set()
        return value
    monkeypatch.setattr(store, 'prewarm', watched_check)
    store.start_background_refresh(entry.factory, season=19, phase=CURRENT_PHASE)
    assert checked.wait(2)
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO runs SELECT 'external-fresh-run',hero,username,created_at,items_json,skills_json,combats_json,stat_wins,stat_losses,screenshot_url,player_rank,season,phase,card_ids_text,raw_json FROM runs WHERE id='1'")
    # Inspect the published entry only: neither analyze(), prewarm() nor invalidate()
    # is called by this test after the insert.
    deadline = time.monotonic() + 20
    while entry.engine.source_count != 61 and time.monotonic() < deadline:
        time.sleep(.05)
    assert entry.engine.source_count == 61
    assert client.get('/comp-preview/api/comp/card?card=q').json['baseline']['count'] == 61
