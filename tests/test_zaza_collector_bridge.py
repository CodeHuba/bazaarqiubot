import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pytest
from flask import Flask

# The pytest console script can otherwise resolve Hermes' own tools package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.zaza_collector_bridge import (
    collector_status,
    enqueue_profile,
    has_completion_result,
    register_collector_routes,
)

TOKEN = 'staging-secret'
SEASON, PHASE = 19, '19.1'
START = '2026-01-01T00:00:00Z'


class Store:
    def __init__(self, path):
        self.path = str(path)


def build(tmp_path, monkeypatch=None, config=None, seed_base=None):
    """Deployment prepares the isolated DB (optionally merged from prod base)."""
    queue = tmp_path / 'queue.db'
    isolated = tmp_path / 'isolated-runs.db'
    sqlite3.connect(isolated).close()
    if seed_base:
        base = tmp_path / 'base.db'
        with sqlite3.connect(base) as db:
            db.execute('CREATE TABLE runs (id TEXT PRIMARY KEY, profileId TEXT, season INTEGER, phase TEXT, created_at TEXT, stat_wins INTEGER, stat_losses INTEGER, hero TEXT, player_rank TEXT, items_json TEXT, skills_json TEXT, raw_json TEXT)')
            db.execute("INSERT INTO runs VALUES('hist','p-1',19,'19.1','2026-01-01T01:00:00Z',1,0,'h','Bronze','[]','[]','{}')")
        _merge_base(base, isolated)
    app = Flask(__name__)
    for key, value in (config or {}).items():
        app.config[key] = value
    if monkeypatch is not None:
        monkeypatch.setenv('RUNS_DB_PATH', str(tmp_path / 'prod.db'))
    register_collector_routes(app, Store(queue), isolated, SEASON, PHASE, START, TOKEN)
    store = Store(queue)
    app.extensions['isolated_runs'] = isolated
    app.extensions['store'] = store
    return app, store, isolated


def _merge_base(base, isolated):
    with sqlite3.connect(base) as src, sqlite3.connect(isolated) as dst:
        dst.execute('''CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, profileId TEXT NOT NULL,
          season INTEGER NOT NULL, phase TEXT NOT NULL, created_at TEXT NOT NULL, stat_wins INTEGER,
          stat_losses INTEGER, hero TEXT, player_rank TEXT, items_json TEXT, skills_json TEXT, raw_json TEXT)''')
        for row in src.execute('SELECT * FROM runs'):
            dst.execute('INSERT OR IGNORE INTO runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', row)


def claim(app, headers=None):
    return app.test_client().post('/api/zaza/collector/claim', headers=headers or {'X-Ingest-Token': TOKEN})


def job_of(response):
    jobs = response.get_json()['jobs']
    return jobs[0] if jobs else None


def run(rid='r1', created='2026-01-02T00:00:00Z', profile='p-1', wins=0):
    return {'id': rid, 'createdAt': created, 'profileId': profile, 'wins': wins,
            'losses': 0, 'hero': 'Dooley', 'items': [], 'skills': []}


def test_auth_and_claim_lease_uniqueness(tmp_path):
    app, store, _ = build(tmp_path)
    enqueue_profile(store, 'p-1', SEASON, PHASE)
    client = app.test_client()
    assert client.post('/api/zaza/collector/claim').status_code == 401
    assert client.post('/api/zaza/collector/claim', headers={'X-Ingest-Token': 'wrong'}).status_code == 401
    first = job_of(claim(app))
    second = job_of(claim(app))
    assert first and second is None  # one active lease; no double dispatch
    assert first['mode'] == 'initial' and first['created_after'].replace('+00:00', 'Z').startswith('2026-01-01')
    assert first['created_before'] > START


def test_complete_validation_boundaries_and_dedup(tmp_path):
    app, store, isolated = build(tmp_path)
    enqueue_profile(store, 'p-1', SEASON, PHASE)
    client = app.test_client()
    job = job_of(claim(app))
    headers = {'X-Ingest-Token': TOKEN}

    bad_cases = [
        {'id': job['id'], 'lease_token': job['lease_token'], 'runs': 'nope'},
        {'id': job['id'], 'lease_token': job['lease_token'], 'runs': [run(profile='other')]},
        {'id': job['id'], 'lease_token': job['lease_token'], 'runs': [run(created='2025-12-31T23:59:59Z')]},
        {'id': job['id'], 'lease_token': job['lease_token'], 'runs': [run(created=job['created_before'])]},
        {'id': job['id'], 'lease_token': job['lease_token'], 'runs': [run(created='2026-01-01T00:00:00+08:00')]},
        {'id': job['id'], 'lease_token': job['lease_token'], 'runs': [run(rid='r1'), run(rid='r1', wins=1)]},
        {'id': job['id'], 'lease_token': job['lease_token'], 'runs': [run(wins=-1)]},
        {'id': job['id'], 'lease_token': job['lease_token'], 'runs': [{'id': 'x'}]},
    ]
    for case in bad_cases:
        response = client.post('/api/zaza/collector/complete', json=case, headers=headers)
        assert response.status_code == 400, response.json
        with sqlite3.connect(store.path) as db:
            state = db.execute('SELECT status,lease_token,completed_at,attempts FROM zaza_collector_jobs WHERE id=?', (job['id'],)).fetchone()
        assert state == ('leased', job['lease_token'], None, 1)

    ok = {'id': job['id'], 'lease_token': job['lease_token'], 'runs': [run(wins=0), run(rid='r2', wins=3)]}
    response = client.post('/api/zaza/collector/complete', json=ok, headers=headers)
    assert response.status_code == 200 and response.json['synced'] is True
    assert client.post('/api/zaza/collector/complete', json=ok, headers=headers).status_code == 409
    with sqlite3.connect(isolated) as db:
        rows = dict(db.execute('SELECT id,stat_wins FROM runs').fetchall())
    assert rows == {'r1': 0, 'r2': 3}  # 0-win preserved, no history deletion


@pytest.mark.parametrize('wins,losses,expected_status', [(10, 3, 200), (0, 0, 200), (-1, 0, 400), (10, True, 400)])
def test_bazaardb_camel_case_stats_survive_collector_ingest(tmp_path, wins, losses, expected_status):
    app, store, isolated = build(tmp_path)
    enqueue_profile(store, 'p-1', SEASON, PHASE)
    job = job_of(claim(app))
    upstream = run()
    del upstream['wins'], upstream['losses']
    upstream.update(statWins=wins, statLosses=losses)
    response = app.test_client().post('/api/zaza/collector/complete',
        json={'id': job['id'], 'lease_token': job['lease_token'], 'runs': [upstream]},
        headers={'X-Ingest-Token': TOKEN})
    assert response.status_code == expected_status
    with sqlite3.connect(isolated) as db:
        has_runs = db.execute("SELECT 1 FROM sqlite_master WHERE name='runs'").fetchone()
        rows = db.execute('SELECT stat_wins,stat_losses,raw_json FROM runs').fetchall() if has_runs else []
    if expected_status == 200:
        assert [(r[0], r[1]) for r in rows] == [(wins, losses)]
        assert json.loads(rows[0][2]) == upstream
    else:
        assert rows == []


def test_failure_requeues_with_backoff_then_recovery(tmp_path):
    app, store, _ = build(tmp_path)
    enqueue_profile(store, 'p-1', SEASON, PHASE)
    client = app.test_client()
    headers = {'X-Ingest-Token': TOKEN}
    job = job_of(claim(app))
    assert client.post('/api/zaza/collector/failure', json={'id': job['id'], 'lease_token': 'bad', 'error': 'x'}, headers=headers).status_code == 409
    assert client.post('/api/zaza/collector/failure', json={'id': job['id'], 'error': 'x'}, headers=headers).status_code == 400
    assert client.post('/api/zaza/collector/failure', json={'id': job['id'], 'lease_token': job['lease_token'], 'error': 'browser closed'}, headers=headers).status_code == 200
    assert client.post('/api/zaza/collector/claim', headers=headers).get_json() == {'jobs': []}
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE zaza_collector_jobs SET next_attempt_at='2000-01-01T00:00:00Z'")
    again = job_of(claim(app))
    assert again and again['mode'] == 'initial'
    client.post('/api/zaza/collector/complete',
                json={'id': again['id'], 'lease_token': again['lease_token'], 'runs': [run()]}, headers=headers)
    assert has_completion_result(store, 'p-1', SEASON, PHASE)


def test_lease_expiry_reclaims(tmp_path):
    app, store, _ = build(tmp_path)
    enqueue_profile(store, 'p-1', SEASON, PHASE)
    first = job_of(claim(app))
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE zaza_collector_jobs SET lease_until='2000-01-01T00:00:00Z'")
    second = job_of(claim(app))
    assert second and second['lease_token'] != first['lease_token']


def test_status_markers_and_scheduled_mode(tmp_path):
    app, store, _ = build(tmp_path)
    assert collector_status(store, 'ghost', SEASON, PHASE) == {'status': 'not_yet_synced', 'completed_at': None, 'synced': False, 'marker': 'not_yet_synced'}
    assert not has_completion_result(store, 'ghost', SEASON, PHASE)
    enqueue_profile(store, 'p-1', SEASON, PHASE)
    client = app.test_client()
    job = job_of(claim(app))
    client.post('/api/zaza/collector/complete', json={'id': job['id'], 'lease_token': job['lease_token'], 'runs': []}, headers={'X-Ingest-Token': TOKEN})
    status = collector_status(store, 'p-1', SEASON, PHASE)
    assert status['marker'] == 'synced' and status['status'] == 'scheduled'
    assert has_completion_result(store, 'p-1', SEASON, PHASE)
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE zaza_collector_jobs SET next_attempt_at='2000-01-01T00:00:00Z'")
    scheduled = job_of(claim(app))
    assert scheduled['mode'] == 'scheduled'


def test_production_paths_rejected(tmp_path, monkeypatch):
    with pytest.raises(ValueError):
        register_collector_routes(Flask(__name__), Store(tmp_path / 'q.db'), tmp_path / 'missing-runs.db', SEASON, PHASE, START, TOKEN)
    app, store, _ = build(tmp_path)
    monkeypatch.setenv('RUNS_DB_PATH', str(tmp_path / 'prod.db'))
    sqlite3.connect(tmp_path / 'prod.db').close()
    with pytest.raises(ValueError):
        register_collector_routes(Flask(__name__), Store(tmp_path / 'q2.db'), tmp_path / 'prod.db', SEASON, PHASE, START, TOKEN)
    with pytest.raises(ValueError):
        register_collector_routes(Flask(__name__), Store(tmp_path / 'q3.db'), tmp_path / 'prod', SEASON, PHASE, START, TOKEN)


def test_configured_phase_end_caps_claim_window(tmp_path):
    app, store, _ = build(tmp_path, config={'ZAZA_COLLECTOR_CREATED_BEFORE': '2026-02-01T00:00:00Z'})
    enqueue_profile(store, 'p-1', SEASON, PHASE)
    job = job_of(claim(app))
    assert datetime.fromisoformat(job['created_before']) == datetime.fromisoformat('2026-02-01T00:00:00Z')
    client = app.test_client()
    response = client.post('/api/zaza/collector/complete',
                           json={'id': job['id'], 'lease_token': job['lease_token'],
                                 'runs': [run(created='2026-02-01T00:00:00Z')]}, headers={'X-Ingest-Token': TOKEN})
    assert response.status_code == 400


def test_base_history_preserved_and_duplicate_identity_rejected(tmp_path):
    app, store, isolated = build(tmp_path, seed_base=True)
    enqueue_profile(store, 'p-1', SEASON, PHASE)
    client = app.test_client()
    job = job_of(claim(app))
    response = client.post('/api/zaza/collector/complete',
                           json={'id': job['id'], 'lease_token': job['lease_token'],
                                 'runs': [run(rid='hist', created='2026-01-01T01:00:00Z', wins=1)]},
                           headers={'X-Ingest-Token': TOKEN})
    assert response.status_code == 200  # same identity: idempotent upsert, history intact
    with sqlite3.connect(isolated) as db:
        rows = db.execute('SELECT id,stat_wins FROM runs ORDER BY id').fetchall()
    assert rows == [('hist', 1)]
