import json
import sqlite3
from types import SimpleNamespace

import pytest

from tools import zaza_space_worker as worker
from web_runs.zaza_space import ZazaSpaceStore, create_zaza_blueprint
from flask import Flask

PROFILE='0b180fb3-7fb9-430d-85c8-019b3658d8c7'
URL='https://bazaardb.gg/run/profile/'+PROFILE


def _setup(tmp_path):
    runs=tmp_path/'runs.db'
    with sqlite3.connect(runs) as conn:
        conn.execute('CREATE TABLE runs(id TEXT PRIMARY KEY,profileId TEXT,season INTEGER,phase TEXT,stat_wins INTEGER,hero TEXT,player_rank TEXT)')
        conn.executemany('INSERT INTO runs VALUES(?,?,?,?,?,?,?)',[
            ('right',PROFILE,19,'19.1',1,'Dooley','Bronze'),
            ('wrong','2d4bdb0f-f103-4b1c-9ef7-df15c73860d3',19,'19.1',10,'Pygmalien','Legendary'),
            ('old',PROFILE,19,'19.0',10,'Dooley','Legendary'),
            ('next',PROFILE,19,'19.2',5,'Dooley','Bronze')])
    store=ZazaSpaceStore(tmp_path/'spaces.db');store.initialize()
    space=store.create(URL,'Alice 空间',auto_approve=True)
    return store,runs,space


class Session:
    def __init__(self,callback=None):
        self.paths=[];self.callback=callback
    def get(self,url,**kwargs):
        self.paths.append(url)
        if '/profile/' in url:
            payload={'profile':{'id':PROFILE,'nickname':'actual','verified':True,'isSupporter':False}}
        else:
            if self.callback:
                self.callback()
            payload={'snapshots':[{'context':'CombatStart','eventId':'event','day':1,'hour':6,'playerBoard':[{'baseId':'a'}],'playerSkills':[{'baseId':'s'}]}],
                     'combats':[{'start_event_id':'event','outcome':'Win','is_pvp':True}]}
        return SimpleNamespace(text='1:'+json.dumps(payload,separators=(',',':')),status_code=200,raise_for_status=lambda:None)


def test_actual_parser_capture_shared_facts_and_local_scope(tmp_path):
    store,runs,space=_setup(tmp_path)
    session=Session()
    path=tmp_path/'snapshots.db'
    assert worker.process_once(store,runs,season=19,phase='19.1',session=session,snapshots_path=path)==1
    assert session.paths==['https://bazaardb.gg/run/profile/'+PROFILE,'https://bazaardb.gg/run/tracker/right']
    assert store.get(space['id'])['status']=='active'
    snapshots=worker.ZazaSnapshotStore(path)
    with snapshots.connect() as conn:
        assert tuple(conn.execute('SELECT run_id,day,outcome,player_rank FROM zaza_combat_snapshots').fetchone())==('right',1,'Win','Bronze')
        assert conn.execute('SELECT count(*) FROM combat_snapshots').fetchone()[0]==0
        assert conn.execute('SELECT count(*) FROM snapshot_jobs').fetchone()[0]==0
    assert snapshots.enqueue_if_eligible('bronze',19,'19.1','Dooley','Bronze',10) is False
    assert snapshots.enqueue_if_eligible('right',19,'19.1','Dooley','Legendary',10) is False
    assert worker.capture_once(store,snapshots,season=19,phase='19.1',session=session)==0
    app=Flask(__name__)
    app.register_blueprint(create_zaza_blueprint(runs,path.parent/'spaces.db',path,season=19,phase='19.1'))
    detail=app.test_client().get('/api/zaza/spaces/'+space['id']).json
    key=detail['runs'][0]['run_key']
    days=app.test_client().get(f"/api/zaza/spaces/{space['id']}/runs/{key}/days").json['days']
    assert days[0]['items'][0]['card_id']=='a' and days[0]['skills'][0]['skill_id']=='s'
    assert 'event' not in json.dumps(days)


def test_worker_empty_phase_sync_keeps_history_without_admitting_old_runs(tmp_path):
    store,runs,space=_setup(tmp_path)
    with sqlite3.connect(runs) as conn:
        conn.execute('ALTER TABLE runs ADD COLUMN created_at TEXT')
        conn.execute("UPDATE runs SET created_at='2026-10-07T22:27:37.758125+00:00'")
    session=Session()
    snapshots=tmp_path/'snapshots.db'
    worker.process_once(store,runs,season=19,phase='19.1',session=session,snapshots_path=snapshots,
                        created_after='2026-10-08T01:00:00Z')
    assert store.get(space['id'])['status']=='active'
    assert session.paths==['https://bazaardb.gg/run/profile/'+PROFILE]
    with store.connect() as conn:
        assert conn.execute('SELECT count(*) FROM zaza_run_interest WHERE active=1').fetchone()[0]==0
    with sqlite3.connect(snapshots) as conn:
        assert conn.execute('SELECT count(*) FROM zaza_snapshot_jobs').fetchone()[0]==0
    with sqlite3.connect(runs) as conn:
        assert conn.execute('SELECT count(*) FROM runs').fetchone()[0]==4


def test_pause_stop_during_fetch_and_phase_switch(tmp_path):
    store,runs,space=_setup(tmp_path);path=tmp_path/'snapshots.db'
    assert worker.process_once(store,runs,season=19,phase='19.1',enabled=lambda:False)==0
    assert store.get(space['id'])['status']=='creating'
    session=Session(callback=lambda:store.transition(space['id'],'stop'))
    worker.process_once(store,runs,season=19,phase='19.1',session=session,snapshots_path=path)
    assert store.get(space['id'])['status']=='stopped'
    snapshots=worker.ZazaSnapshotStore(path)
    assert snapshots.status()['snapshots']==0
    assert worker.capture_once(store,snapshots,season=19,phase='19.1',session=session)==0
    store.transition(space['id'],'resume')
    worker.process_once(store,runs,season=19,phase='19.2',session=Session(),snapshots_path=path)
    with snapshots.connect() as conn:
        assert conn.execute('SELECT run_id FROM zaza_combat_snapshots').fetchone()[0]=='next'
    assert store.get(space['id'])['current_phase']=='19.2'


def test_profile_must_match_structured_metadata_not_url_echo():
    session=Session()
    assert worker.validate_profile(PROFILE,session=session)['nickname']=='actual'
    session.get=lambda *a,**k:SimpleNamespace(text='1:'+json.dumps({'profileId':PROFILE,'runs':[]}),status_code=200,raise_for_status=lambda:None)
    with pytest.raises(ValueError):
        worker.validate_profile(PROFILE,session=session)


def test_capture_failure_retries_without_tearing_down_active_space(tmp_path):
    store,runs,space=_setup(tmp_path);path=tmp_path/'snapshots.db'
    class Broken(Session):
        def get(self,url,**kwargs):
            if '/tracker/' in url:
                raise RuntimeError('authentication unavailable')
            return super().get(url,**kwargs)
    worker.process_once(store,runs,season=19,phase='19.1',session=Broken(),snapshots_path=path)
    assert store.get(space['id'])['status']=='active'
    with worker.ZazaSnapshotStore(path).connect() as conn:
        row=conn.execute('SELECT status,last_error FROM zaza_snapshot_jobs').fetchone()
        assert row['status']=='retry' and 'authentication unavailable' in row['last_error']
    store.transition(space['id'],'retry')
    class Failed:
        def get(self,*a,**k):
            raise RuntimeError('profile unavailable')
    worker.process_once(store,runs,season=19,phase='19.1',session=Failed(),snapshots_path=path)
    assert store.get(space['id'])['status']=='active'
    assert 'profile unavailable' in store.get(space['id'])['collection_error']


def test_complete_success_rejects_bad_arrays_atomically(tmp_path):
    store, runs, space = _setup(tmp_path)
    path = tmp_path / 'snapshots.db'
    snapshots = worker.ZazaSnapshotStore(path)
    snapshots.initialize()
    snapshots.enqueue({'id': 'right', 'season': 19, 'phase': '19.1',
                       'hero': 'Dooley', 'player_rank': 'Bronze', 'stat_wins': 1})
    job = snapshots.claim_run('right', 19, '19.1')
    assert job is not None
    good = {'eventId': 'good', 'day': 13, 'hour': 6,
            'playerBoard': [], 'playerSkills': [], 'combat': {'is_pvp': True}}
    bad = dict(good, eventId='bad', playerBoard='$unresolved')
    with pytest.raises(ValueError, match='resolved arrays'):
        snapshots.complete_success('right', [good, bad], job['lease_token'])
    with snapshots.connect() as conn:
        assert conn.execute('SELECT count(*) FROM zaza_combat_snapshots').fetchone()[0] == 0
        row = conn.execute('SELECT status FROM zaza_snapshot_jobs').fetchone()
        assert row['status'] == 'processing'
