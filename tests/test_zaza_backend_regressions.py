"""Regression coverage for scoped facts without changing legacy global capture."""
import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from flask import Flask

from tools import zaza_space_worker as worker
from web_runs.day_snapshot_store import DaySnapshotStore
from web_runs.zaza_space import create_zaza_blueprint
from test_zaza_space_worker import PROFILE, Session, _setup


def fact(event='old', hour=9, pvp=True, day=1):
    return {'context': 'CombatStart', 'eventId': event, 'day': day, 'hour': hour,
            'playerBoard': [{'baseId': event}], 'combat': {'is_pvp': pvp, 'outcome': 'Win'}}


def setup_capture(tmp_path):
    store, runs, space = _setup(tmp_path)
    store.mark_collection(space['id'], season=19, phase='19.1', run_ids=['right'])
    snapshots = worker.ZazaSnapshotStore(tmp_path/'snapshots.db')
    snapshots.initialize()
    snapshots.enqueue({'id': 'right', 'season': 19, 'phase': '19.1', 'hero': 'Dooley', 'player_rank': 'Bronze', 'stat_wins': 1})
    global_store = DaySnapshotStore(snapshots.path)
    return store, runs, space, snapshots, global_store


@pytest.mark.parametrize('hour,pvp', [(9, True), (6, False), (6, None)])
def test_old_unsuitable_global_success_allows_scoped_capture_and_preserves_global(tmp_path, hour, pvp):
    store, runs, space, snapshots, global_store = setup_capture(tmp_path)
    global_store.enqueue_if_eligible('right', 19, '19.1', 'Dooley', 'Legendary', 10)
    job = global_store.claim(1)[0]
    global_store.complete_success('right', [fact(hour=hour, pvp=pvp)], job['lease_token'])
    with global_store.connect() as conn:
        before_job = tuple(conn.execute('SELECT * FROM snapshot_jobs').fetchone())
        before_facts = [tuple(r) for r in conn.execute('SELECT * FROM combat_snapshots')]
        before_cards = [tuple(r) for r in conn.execute('SELECT * FROM combat_player_cards')]
    session = Session()
    assert worker.capture_once(store, snapshots, season=19, phase='19.1', session=session) == 1
    assert len(session.paths) == 1
    with global_store.connect() as conn:
        assert tuple(conn.execute('SELECT * FROM snapshot_jobs').fetchone()) == before_job
        assert [tuple(r) for r in conn.execute('SELECT * FROM combat_snapshots')] == before_facts
        assert [tuple(r) for r in conn.execute('SELECT * FROM combat_player_cards')] == before_cards
    app = Flask(__name__)
    app.register_blueprint(create_zaza_blueprint(runs, store.path, snapshots.path, season=19, phase='19.1'))
    http = app.test_client()
    detail = http.get('/api/zaza/spaces/'+space['id']).json
    row = detail['runs'][0]
    assert row['day_count'] == 1 and row['snapshot_status'] == 'available'
    days = http.get(f"/api/zaza/spaces/{space['id']}/runs/{row['run_key']}/days").json['days']
    assert [d['hour'] for d in days] == [6]
    assert days[0]['items'][0]['card_id'] == 'a'
    assert worker.capture_once(store, snapshots, season=19, phase='19.1', session=session) == 0


def test_public_old_success_is_missing_until_scoped_capture(tmp_path):
    store, runs, space, snapshots, global_store = setup_capture(tmp_path)
    global_store.enqueue_if_eligible('right', 19, '19.1', 'Dooley', 'Legendary', 10)
    job = global_store.claim(1)[0]
    global_store.complete_success('right', [fact()], job['lease_token'])
    app = Flask(__name__)
    app.register_blueprint(create_zaza_blueprint(runs, store.path, snapshots.path, season=19, phase='19.1'))
    row = app.test_client().get('/api/zaza/spaces/'+space['id']).json['runs'][0]
    assert row['day_count'] == 0 and row['snapshot_status'] == 'pending'
    assert snapshots.status()['snapshots'] == 0


def test_global_pending_and_processing_are_never_claimed_by_zaza(tmp_path):
    store, _, _, snapshots, global_store = setup_capture(tmp_path)
    global_store.enqueue_if_eligible('right', 19, '19.1', 'Dooley', 'Legendary', 10)
    assert snapshots.claim_run('right') is None
    global_job = global_store.claim(1)[0]
    assert snapshots.claim_run('right') is None
    global_store.complete_success('right', [fact('six', 6), fact('nine', 9, day=2)], global_job['lease_token'])
    assert snapshots.claim_run('right') is None
    assert worker.capture_once(store, snapshots, season=19, phase='19.1', session=Session()) == 0
    assert global_store.status()['snapshots'] == 2


def test_stale_zaza_lease_cannot_write_or_release_new_claim(tmp_path):
    _, _, _, snapshots, _ = setup_capture(tmp_path)
    old = snapshots.claim_run('right')
    with snapshots.connect() as conn:
        conn.execute("UPDATE zaza_snapshot_jobs SET started_at=? WHERE run_id='right'", ((datetime.now()-timedelta(hours=2)).isoformat(),))
    new = snapshots.claim_run('right')
    assert old['lease_token'] != new['lease_token']
    with pytest.raises(ValueError):
        snapshots.complete_success('right', [fact('six', 6)], old['lease_token'])
    snapshots.release(old)
    with snapshots.connect() as conn:
        assert conn.execute('SELECT lease_token FROM zaza_snapshot_jobs').fetchone()[0] == new['lease_token']
    snapshots.complete_success('right', [fact('six', 6)], new['lease_token'])
    assert snapshots.status()['snapshots'] == 1


def test_pause_during_fetch_releases_without_facts(tmp_path):
    store, _, _, snapshots, global_store = setup_capture(tmp_path)
    enabled = [True]
    session = Session(callback=lambda: enabled.__setitem__(0, False))
    assert worker.capture_once(store, snapshots, season=19, phase='19.1', session=session, enabled=lambda: enabled[0]) == 1
    assert snapshots.status()['snapshots'] == 0 and global_store.status()['snapshots'] == 0
    with snapshots.connect() as conn:
        assert tuple(conn.execute('SELECT status,attempts FROM zaza_snapshot_jobs').fetchone()) == ('retry', 0)


def test_global_late_enqueue_and_completion_cannot_overwrite_scoped_facts(tmp_path):
    _, _, _, snapshots, global_store = setup_capture(tmp_path)
    scoped = snapshots.claim_run('right')
    # Legacy admission can race after the Zaza transaction. Separate fact tables
    # preserve both results even though two upstream requests may occur.
    global_store.enqueue_if_eligible('right', 19, '19.1', 'Dooley', 'Legendary', 10)
    legacy = global_store.claim(1)[0]
    snapshots.complete_success('right', [fact('same', 6)], scoped['lease_token'])
    global_store.complete_success('right', [fact('same', 9)], legacy['lease_token'])
    with snapshots.connect() as conn:
        assert conn.execute('SELECT hour FROM zaza_combat_snapshots').fetchone()[0] == 6
        assert conn.execute('SELECT hour FROM combat_snapshots').fetchone()[0] == 9
    assert snapshots.status()['snapshots'] == 1


def test_raw_identity_overrides_conflicting_materialized_column(tmp_path):
    from test_zaza_multispace import OTHER, _runs_db
    from web_runs.zaza_space import _database_runs
    runs = tmp_path/'runs.db'
    _runs_db(runs)
    store = worker.ZazaSpaceStore(tmp_path/'spaces.db'); store.initialize()
    with __import__('sqlite3').connect(runs) as conn:
        conn.execute('UPDATE runs SET profileId=? WHERE id=?', (OTHER, 'right'))
        conn.execute('UPDATE runs SET profileId=? WHERE id=?', (PROFILE, 'wrong'))
    assert {r['id'] for r in store.local_runs(runs, PROFILE, 19, '19.1')} == {'right', 'second'}
    assert {r['id'] for r in _database_runs(runs, PROFILE, 19, '19.1', ['right','second','wrong'])} == {'right', 'second'}


def test_scoped_failure_backoff_and_terminal_exhaustion(tmp_path):
    _, _, _, snapshots, global_store = setup_capture(tmp_path)
    for attempt in range(1, 6):
        job = snapshots.claim_run('right')
        assert job
        snapshots.complete_failure('right', 'network failure', job['lease_token'])
        with snapshots.connect() as conn:
            row = conn.execute('SELECT status,attempts,last_error FROM zaza_snapshot_jobs').fetchone()
            assert row['status'] == ('failed' if attempt == 5 else 'retry')
            assert row['attempts'] == attempt
            assert row['last_error'] == 'network failure'
            assert snapshots.claim_run('right') is None
            conn.execute("UPDATE zaza_snapshot_jobs SET next_attempt_at='2000-01-01'")
    assert snapshots.claim_run('right') is None
    assert global_store.status()['jobs'] == {}


def test_authoritative_profile_object_only():
    session = Session()
    def response(payload):
        return SimpleNamespace(text='1:'+json.dumps(payload), status_code=200, raise_for_status=lambda: None)
    session.get = lambda *a, **k: response({'profile': {'id': PROFILE, 'nickname': 'qqr', 'verified': False, 'isSupporter': True}})
    assert worker.validate_profile(PROFILE, session=session) == {'profile_id': PROFILE, 'nickname': 'qqr', 'verified': False, 'supporter': True}
    for payload in ({'runs': [{'profileId': PROFILE, 'username': 'spoof'}]},
                    {'profile': {'id': '2d4bdb0f-f103-4b1c-9ef7-df15c73860d3', 'nickname': 'wrong'}, 'runs': [{'profileId': PROFILE, 'username': 'spoof'}]},
                    {'id': PROFILE, 'nickname': 'unrelated'}):
        session.get = lambda *a, **k: response(payload)
        with pytest.raises(ValueError):
            worker.validate_profile(PROFILE, session=session)
