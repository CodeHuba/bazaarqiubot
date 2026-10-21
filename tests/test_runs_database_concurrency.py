"""Exercise the actual ingest handler against independent SQLite connections."""
import ast
from contextlib import closing
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import sys
from types import ModuleType, SimpleNamespace

from flask import Flask, jsonify, request
import pytest


@pytest.fixture
def ingest(tmp_path, monkeypatch):
    path = tmp_path / 'runs.db'
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('''CREATE TABLE runs (
            id TEXT PRIMARY KEY, hero TEXT, username TEXT, created_at TEXT,
            items_json TEXT, skills_json TEXT, combats_json TEXT, snapshots_json TEXT,
            stat_wins INTEGER, stat_losses INTEGER, player_rating INTEGER,
            player_rating_after INTEGER, player_rank TEXT, player_rank_after TEXT,
            screenshot_url TEXT, raw_json TEXT, collected_at TEXT, season INTEGER,
            phase TEXT, card_ids_text TEXT)''')
        conn.executemany('INSERT INTO runs(id, stat_wins) VALUES (?, 10)', [('old-1',), ('old-2',)])
        conn.commit()

    connections, cached, invalidations = [], [], []

    class Connection(sqlite3.Connection):
        closed = False

        def close(self):
            super().close()
            self.closed = True

    def connect(db_path, **kwargs):
        kwargs['timeout'] = .1
        conn = sqlite3.connect(db_path, factory=Connection, **kwargs)
        connections.append(conn)
        return conn

    query = ModuleType('plugins.bazaar_plugin.runs_query')
    query._card_tier_cache = {'stale': True}
    query._hero_overview_cache = {'stale': True}
    store = ModuleType('plugins.bazaar_plugin.comp_analysis_store')
    store.invalidate = lambda: invalidations.append(True)
    package = ModuleType('plugins.bazaar_plugin')
    package.__path__ = []
    package.runs_query = query
    for name, module in (('plugins.bazaar_plugin', package),
                         ('plugins.bazaar_plugin.runs_query', query),
                         ('plugins.bazaar_plugin.comp_analysis_store', store)):
        monkeypatch.setitem(sys.modules, name, module)

    app = Flask(__name__)
    namespace = dict(
        app=app, request=request, jsonify=jsonify, closing=closing, json=json,
        datetime=datetime, RUNS_DB_PATH=str(path), RUNS_SEASON_ID=19, CURRENT_PHASE='19.2',
        _sqlite3=SimpleNamespace(connect=connect, Error=sqlite3.Error,
                                OperationalError=sqlite3.OperationalError,
                                SQLITE_BUSY=sqlite3.SQLITE_BUSY, SQLITE_LOCKED=sqlite3.SQLITE_LOCKED),
        _ingest_authorized=lambda: True, ENABLE_DAY_SNAPSHOT_QUEUE=False,
        _winrate_cache_append=lambda items, wins: cached.append((items, wins)),
    )
    source = Path(__file__).parents[1] / 'web_runs/app.py'
    wanted = {'_initialize_runs_db', '_ensure_snapshot_job_schema',
              '_ensure_combat_snapshot_schema', '_insert_combat_snapshots', 'api_ingest'}
    functions = [node for node in ast.parse(source.read_text(encoding='utf-8')).body
                 if isinstance(node, ast.FunctionDef) and node.name in wanted]
    for function in functions:
        function.decorator_list = []
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(source), 'exec'), namespace)
    app.add_url_rule('/api/ingest', view_func=namespace['api_ingest'], methods=['POST'])
    yield SimpleNamespace(path=path, app=app, namespace=namespace, connections=connections,
                          cached=cached, invalidations=invalidations, query=query)
    for conn in connections:
        if not conn.closed:
            conn.close()


def run(run_id='fresh', **extra):
    return dict(id=run_id, hero='Vanessa', statWins=10, items=[{'cardId': 'item'}], **extra)


def ids(path):
    with closing(sqlite3.connect(path, timeout=.1)) as conn:
        return {row[0] for row in conn.execute('SELECT id FROM runs')}


def test_wal_ingest_commits_while_a_long_read_cursor_is_open(ingest):
    ingest.namespace['_initialize_runs_db'](str(ingest.path))
    with closing(sqlite3.connect(ingest.path, timeout=.1)) as reader:
        assert reader.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
        cursor = reader.execute('SELECT id FROM runs ORDER BY id')
        assert cursor.fetchone()[0] == 'old-1'
        response = ingest.app.test_client().post('/api/ingest', json=[run()])
        assert response.status_code == 200, response.json
        assert response.json['new'] == 1
        assert ids(ingest.path) == {'old-1', 'old-2', 'fresh'}
        assert cursor.fetchall() == [('old-2',)]
    assert all(conn.closed for conn in ingest.connections)


def test_failed_commit_rolls_back_and_closes_before_retry(ingest):
    # Force the production incident in DELETE mode: a reader prevents COMMIT.
    with closing(sqlite3.connect(ingest.path, timeout=.1)) as reader:
        cursor = reader.execute('SELECT id FROM runs')
        cursor.fetchone()
        response = ingest.app.test_client().post('/api/ingest', json=[run()])
        assert response.status_code == 503
        assert response.headers['Retry-After'] == '2'
        assert ids(ingest.path) == {'old-1', 'old-2'}
        assert ingest.cached == []
        assert ingest.invalidations == []
        assert all(conn.closed for conn in ingest.connections)
        cursor.close()
    retry = ingest.app.test_client().post('/api/ingest', json=[run()])
    assert retry.status_code == 200
    assert retry.json['new'] == 1


def test_database_errors_are_not_swallowed_and_partial_batch_is_rolled_back(ingest):
    def snapshot(conn, record):
        if record['id'] == 'fail':
            raise sqlite3.OperationalError('disk I/O error')

    ingest.namespace['_insert_combat_snapshots'] = snapshot
    response = ingest.app.test_client().post('/api/ingest', json=[run(), run('fail')])
    assert response.status_code == 500
    assert ids(ingest.path) == {'old-1', 'old-2'}
    assert ingest.cached == []
    assert ingest.invalidations == []
    assert all(conn.closed for conn in ingest.connections)


def test_redis_update_happens_after_commit_and_connection_close(ingest):
    def append(items, wins):
        assert all(conn.closed for conn in ingest.connections)
        assert 'fresh' in ids(ingest.path)
        # Even a slow Redis call must not hold SQLite's writer lock.
        with closing(sqlite3.connect(ingest.path, timeout=.1)) as other:
            other.execute('BEGIN IMMEDIATE')
            other.execute("UPDATE runs SET stat_wins=1 WHERE id='old-1'")
            other.rollback()
        ingest.cached.append((items, wins))

    ingest.namespace['_winrate_cache_append'] = append
    client = ingest.app.test_client()
    first = client.post('/api/ingest', json=[run()])
    assert first.status_code == 200
    assert first.json['new'] == 1
    duplicate = client.post('/api/ingest', json=[run()])
    assert duplicate.status_code == 200
    assert duplicate.json['new'] == 0
    assert len(ingest.cached) == 1
    assert len(ingest.invalidations) == 1
    assert not ingest.query._card_tier_cache and not ingest.query._hero_overview_cache


def test_malformed_snapshot_rolls_back_only_that_run(ingest):
    response = ingest.app.test_client().post('/api/ingest', json=[
        run('bad', combatSnapshots=['invalid-snapshot']), run('good'),
    ])
    assert response.status_code == 200
    assert response.json['new'] == 1
    assert ids(ingest.path) == {'old-1', 'old-2', 'good'}
    assert len(ingest.cached) == 1


def test_snapshot_jobs_are_enqueued_only_after_commit_and_not_for_duplicates(ingest):
    queued = []

    def enqueue(**job):
        assert all(conn.closed for conn in ingest.connections)
        assert job['run_id'] in ids(ingest.path)
        queued.append(job)

    ingest.namespace.update(
        ENABLE_DAY_SNAPSHOT_QUEUE=True, _day_snapshot_store_error=None,
        _day_snapshot_store=SimpleNamespace(eligible=lambda rank, wins: wins >= 10,
                                            enqueue_if_eligible=enqueue),
    )
    client = ingest.app.test_client()
    first = client.post('/api/ingest', json=[run()])
    assert first.status_code == 200
    assert first.json['snapshot_jobs_eligible'] == 1
    duplicate = client.post('/api/ingest', json=[run()])
    assert duplicate.status_code == 200
    assert duplicate.json['snapshot_jobs_eligible'] == 0
    assert len(queued) == 1
