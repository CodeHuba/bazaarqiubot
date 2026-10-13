import json
import sqlite3

import pytest
from flask import Flask

from web_runs.zaza_space import ZazaSpaceStore, create_zaza_blueprint, _profile_id

PROFILE = '0b180fb3-7fb9-430d-85c8-019b3658d8c7'
OTHER = '2d4bdb0f-f103-4b1c-9ef7-df15c73860d3'
URL = 'https://bazaardb.gg/run/profile/' + PROFILE


def _runs_db(path):
    with sqlite3.connect(path) as conn:
        conn.execute('''CREATE TABLE runs(id TEXT PRIMARY KEY, hero TEXT, created_at TEXT,
                     stat_wins INTEGER,stat_losses INTEGER,season INTEGER,phase TEXT,
                     items_json TEXT,profileId TEXT,raw_json TEXT,player_rank TEXT,skills_json TEXT)''')
        for rid, hero, profile, phase, wins, cards in [
            ('right','Dooley',PROFILE,'19.1',7,['a','b']),
            ('second','Dooley',PROFILE,'19.1',2,['a']),
            ('wrong','Pygmalien',OTHER,'19.1',10,['b']),
            ('old','Dooley',PROFILE,'19.0',10,['a']),
            ('next','Dooley',PROFILE,'19.2',8,['c']),
        ]:
            conn.execute('INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                         (rid,hero,'2026-10-02T12:00:00Z' if rid=='right' else '2026-10-01T12:00:00Z',wins,3,19,phase,
                          json.dumps([{'cardId':c} for c in cards]),profile,json.dumps({'profileId':profile}),'Bronze',json.dumps([{'baseId':'skill'}])))


def _client(tmp_path, admin=False, phase=None, created_after=None, created_before=None):
    path=tmp_path/'runs.db'
    if not path.exists():
        _runs_db(path)
    app=Flask(__name__)
    app.config['TESTING']=True
    app.register_blueprint(create_zaza_blueprint(runs_path=lambda:path,space_path=lambda:tmp_path/'spaces.db',
        snapshots_path=lambda:tmp_path/'snapshots.db',season=lambda:19,phase=phase or (lambda:'19.1'),admin_authorized=lambda:admin,
        created_after=created_after,created_before=created_before))
    return app.test_client()


def _active(tmp_path, client):
    space=client.post('/api/zaza/spaces',json={'profileUrl':URL,'name':'Alice 空间'}).json['space']
    store=ZazaSpaceStore(tmp_path/'spaces.db')
    store.transition(space['id'],'approve')
    store.mark_collection(space['id'],season=19,phase='19.1',run_ids=['right','second','wrong','old'],profile={'nickname':'actual nickname','verified':True,'supporter':False})
    return space,store


def test_pending_searchable_without_run_leak_and_canonical_validation(tmp_path):
    client=_client(tmp_path)
    space=client.post('/api/zaza/spaces',json={'profileUrl':URL+'/', 'name':'Alice 空间'}).json['space']
    assert space['profile_url']==URL
    assert space['status']=='pending'
    assert client.get('/api/zaza/spaces?query=Alice').json['spaces'][0]['id']==space['id']
    detail=client.get('/api/zaza/spaces/'+space['id']).json
    assert detail['runs']==[] and detail['summary']['run_count']==0
    assert detail['profile']['nickname'] is None
    assert client.post('/api/zaza/spaces',json={'profileUrl':URL,'name':'Other 空间'}).status_code==409
    for invalid in ('profile-a',PROFILE,'https://bazaardb.gg/profile/'+PROFILE,URL+'?x=1','https://evil.example/run/profile/'+PROFILE):
        with pytest.raises(ValueError):
            _profile_id(invalid)


def test_detail_exact_identity_scope_and_cards_and_pagination(tmp_path):
    client=_client(tmp_path)
    space,_=_active(tmp_path,client)
    path='/api/zaza/spaces/'+space['id']
    detail=client.get(path+'?cards=a,b&perPage=1&page=1').json
    assert detail['pagination']=={'page':1,'per_page':1,'total':1}
    assert detail['runs'][0]['wins']==7
    assert detail['runs'][0]['skills'][0]['skill_id']=='skill'
    assert detail['profile']['nickname']=='actual nickname'
    assert detail['profile']['verified'] is True
    assert detail['summary']['run_count']==2 and detail['summary']['average_wins']==4.5
    assert {c['card_id'] for c in detail['suggestions']}=={'a','b'}
    assert client.get(path+'?cards=a&cards=b').json['pagination']['total']==1
    assert client.get(path+'?hero=Pygmalien').json['pagination']['total']==0
    assert client.get(path+'?from=2026-10-02T00:00:00Z').json['pagination']['total']==1
    assert client.get(path+'?to=2026-10-01T23:59:59Z').json['pagination']['total']==1
    assert client.get(path+'?page=2&perPage=1').json['runs'][0]['wins']==2
    for query in ('page=0','page=no','perPage=101','from=invalid'):
        assert client.get(path+'?'+query).status_code==400


def test_phase_time_window_filters_seeded_runs_and_survives_stop_rollover(tmp_path):
    phase=['19.1']
    start='2026-10-02T12:00:00Z'
    client=_client(tmp_path,phase=lambda:phase[0],created_after=lambda:start)
    space,store=_active(tmp_path,client)
    with sqlite3.connect(tmp_path/'runs.db') as conn:
        conn.execute("UPDATE runs SET created_at='2026-10-02T20:00:00+08:00' WHERE id='right'")
    scoped=store.local_runs(tmp_path/'runs.db',PROFILE,19,'19.1',created_after=start)
    assert [row['id'] for row in scoped]==['right']
    path='/api/zaza/spaces/'+space['id']
    assert client.get(path).json['summary']['run_count']==1
    store.mark_collection(space['id'],season=19,phase='19.1',run_ids=['right'],created_after=start)
    with sqlite3.connect(tmp_path/'runs.db') as conn:
        assert conn.execute('SELECT count(*) FROM runs').fetchone()[0]==5
    store.transition(space['id'],'stop')
    phase[0]='19.2'
    start='2026-10-03T00:00:00Z'
    assert client.get(path).json['summary']['run_count']==1
    assert client.get(path).json['scope']['phase']=='19.1'


def test_phase_window_end_is_exclusive_and_empty_sync_removes_old_members(tmp_path):
    client=_client(tmp_path,created_before='2026-10-02T12:00:00Z')
    space,store=_active(tmp_path,client)
    path='/api/zaza/spaces/'+space['id']
    detail=client.get(path).json
    assert detail['summary']['run_count']==1 and detail['runs'][0]['wins']==2
    store.mark_collection(space['id'],season=19,phase='19.1',run_ids=[])
    assert client.get(path).json['summary']['run_count']==0


def test_admin_settings_audit_auth_future_only_and_lifecycle(tmp_path):
    public=_client(tmp_path)
    assert public.get('/api/admin/zaza/settings').status_code==401
    assert public.get('/api/admin/zaza/audit').status_code==401
    pending=public.post('/api/zaza/spaces',json={'profileUrl':URL,'name':'Alice 空间'}).json['space']
    client=_client(tmp_path,admin=True)
    assert client.put('/api/admin/zaza/settings',json={'auto_approve':True}).json['settings']['auto_approve'] is True
    fresh=client.post('/api/zaza/spaces',json={'profileUrl':'https://bazaardb.gg/run/profile/'+OTHER,'name':'Other 空间'}).json['space']
    assert fresh['status']=='creating'
    store=ZazaSpaceStore(tmp_path/'spaces.db')
    assert store.get(pending['id'])['status']=='pending'
    assert _client(tmp_path,admin=True).get('/api/admin/zaza/settings').json['settings']['auto_approve'] is True
    endpoint='/api/admin/zaza/spaces/'+pending['id']
    assert client.put(endpoint,json={'action':'approve'}).status_code==200
    assert client.put(endpoint,json={'action':'approve'}).status_code==409
    assert client.put(endpoint,json={'action':'rename','name':'New 空间'}).json['space']['id']==pending['id']
    assert client.put(endpoint,json={'action':'delete'}).status_code==200
    assert client.get('/api/zaza/spaces/'+pending['id']).status_code==404
    assert client.post('/api/zaza/spaces',json={'profileUrl':URL,'name':'Alice 空间'}).status_code==201
    actions={row['action'] for row in client.get('/api/admin/zaza/audit').json['audit']}
    assert {'create','settings','approve','rename','delete'}<=actions


def test_stop_freezes_scope_and_membership_resume_retains_history(tmp_path):
    current=['19.1']
    client=_client(tmp_path,phase=lambda:current[0])
    space,store=_active(tmp_path,client)
    store.transition(space['id'],'stop')
    current[0]='19.2'
    assert store.mark_collection(space['id'],season=19,phase='19.2',run_ids=['next'])['status']=='stopped'
    detail=client.get('/api/zaza/spaces/'+space['id']).json
    assert detail['scope']['phase']=='19.1' and detail['summary']['run_count']==2
    store.transition(space['id'],'resume')
    store.mark_collection(space['id'],season=19,phase='19.2',run_ids=['next'])
    detail=client.get('/api/zaza/spaces/'+space['id']).json
    assert detail['scope']['phase']=='19.2' and detail['summary']['run_count']==1
    with store.connect() as conn:
        assert conn.execute("SELECT count(*) FROM zaza_run_interest WHERE phase='19.1'").fetchone()[0]==4


def test_stale_discovery_completion_cannot_revive_stopped_space(tmp_path):
    store=ZazaSpaceStore(tmp_path/'spaces.db')
    store.initialize()
    space=store.create(URL,'Alice 空间',auto_approve=True)
    job=store.claim_outbox()[0]
    assert store.claim_outbox()==[]
    store.transition(space['id'],'stop')
    result=store.mark_collection(space['id'],season=19,phase='19.1',run_ids=['right'],job=job)
    assert result['status']=='stopped'
    with pytest.raises(ValueError):
        store.complete_outbox(job['id'],job['lease_token'])


def test_retry_backoff_stale_lease_and_repeated_success(tmp_path):
    store=ZazaSpaceStore(tmp_path/'spaces.db');store.initialize()
    space=store.create(URL,'Alice 空间',auto_approve=True)
    job=store.claim_outbox()[0]
    with store.connect() as conn:
        conn.execute("UPDATE zaza_outbox SET updated_at='2000-01-01T00:00:00Z'")
    newer=store.claim_outbox()[0]
    with pytest.raises(ValueError):
        store.complete_outbox(job['id'],job['lease_token'])
    store.complete_outbox(newer['id'],newer['lease_token'],error='timeout')
    assert store.claim_outbox()==[]
    with store.connect() as conn:
        conn.execute("UPDATE zaza_outbox SET available_at='2000-01-01T00:00:00Z'")
    retry=store.claim_outbox()[0]
    store.mark_collection(space['id'],season=19,phase='19.1',run_ids=[],job=retry)
    store.complete_outbox(retry['id'],retry['lease_token'])
    store.transition(space['id'],'retry')
    again=store.claim_outbox()[0]
    store.complete_outbox(again['id'],again['lease_token'])
    with store.connect() as conn:
        assert conn.execute("SELECT count(*) FROM zaza_outbox WHERE status='success'").fetchone()[0]==2
