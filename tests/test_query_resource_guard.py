"""Exercise overload and cancellation with real Flask requests and SQLite work."""
import ast
import base64
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import pickle
import hmac
import os
from functools import wraps
from pathlib import Path
import sqlite3
from threading import Event
import time

from flask import Flask, Response, jsonify, request
import pytest

from test_runs_list_performance import runs_query
from plugins.bazaar_plugin.query_budget import (
    BoundedTTLCache, BudgetExceeded, QueryController, attach_connection,
    budgeted_rows, checkpoint, current_budget,
    singleflight,
)

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('resource_guard', ROOT/'web_runs/resource_guard.py')
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def make_app(controller, handler):
    app = Flask(__name__)
    app.testing = True
    recorded = []
    @app.after_request
    def analytics(response):
        recorded.append((request.path, response.status_code))
        return response
    guard.install_resource_guard(app, controller)
    app.add_url_rule('/api/runs', view_func=handler)
    app.add_url_rule('/', endpoint='health_page', view_func=lambda: 'healthy')
    return app, recorded


def test_concurrency_rejects_without_queueing_and_light_pages_stay_available():
    controller = QueryController(limit=1, memory=lambda: 2048)
    started, release = Event(), Event()
    def slow():
        started.set()
        assert release.wait(3)
        return jsonify(total=1)
    app, recorded = make_app(controller, slow)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(lambda: app.test_client().get('/api/runs'))
        assert started.wait(1)
        try:
            start = time.monotonic()
            response = app.test_client().get('/api/runs')
            assert time.monotonic()-start < .5
            assert response.status_code == 503
            assert response.json['code'] == 'query_concurrency'
            assert response.headers['Retry-After'] == '3'
            assert response.headers['Cache-Control'] == 'no-store'
            assert app.test_client().head('/api/runs').status_code == 503
            assert app.test_client().get('/').status_code == 200
        finally:
            release.set()
        assert pending.result(2).status_code == 200
    assert controller.status()['active'] == 0
    assert controller.status()['peak'] == 1
    assert ('/api/runs', 503) in recorded
    assert current_budget() is None


def test_memory_admission_recovers_when_pressure_is_gone():
    memory = [100]
    controller = QueryController(memory=lambda: memory[0])
    called = []
    app, _ = make_app(controller, lambda: called.append(1) or jsonify(ok=True))
    assert app.test_client().get('/api/runs').json['code'] == 'query_memory'
    assert called == []
    memory[0] = 1024
    assert app.test_client().get('/api/runs').status_code == 200
    assert called == [1]
    assert controller.status()['active'] == 0


def test_real_sql_is_interrupted_and_connection_and_slot_are_released():
    controller = QueryController(seconds=.02, memory=lambda: 2048)
    connections = []
    def sql():
        conn = sqlite3.connect(':memory:')
        connections.append(conn)
        attach_connection(conn)
        try:
            # Deliberately huge query: success before a timeout is impossible.
            conn.execute('''WITH RECURSIVE seq(x) AS (
                VALUES(1) UNION ALL SELECT x+1 FROM seq WHERE x<100000000)
                SELECT sum(x) FROM seq''').fetchone()
            pytest.fail('SQL should have been interrupted')
        except sqlite3.OperationalError:
            return jsonify(error='interrupted'), 500  # Existing handler behavior.
    app, recorded = make_app(controller, sql)
    start = time.monotonic()
    response = app.test_client().get('/api/runs')
    assert time.monotonic()-start < 1
    assert response.status_code == 503
    assert response.json['code'] == 'query_timeout'
    assert recorded[-1] == ('/api/runs', 503)
    assert controller.status()['interrupted'] == {'timeout': 1}
    assert controller.status()['active'] == 0
    assert current_budget() is None
    with pytest.raises(sqlite3.ProgrammingError):
        connections[0].execute('SELECT 1')
    # A subsequent request has a fresh deadline, not a leaked cancellation flag.
    app.view_functions['sql'] = lambda: jsonify(ok=True)
    assert app.test_client().get('/api/runs').status_code == 200


def test_python_loop_timeout_and_mid_query_memory_pressure():
    memory = [2048]
    controller = QueryController(seconds=.01, memory=lambda: memory[0])
    def python_work():
        try:
            sum(x*x for x in budgeted_rows(range(10000000)))
        except BudgetExceeded:
            return jsonify(error='caught'), 500
        pytest.fail('Python work should stop')
    app, _ = make_app(controller, python_work)
    assert app.test_client().get('/api/runs').json['code'] == 'query_timeout'
    with controller.acquire(seconds=2) as budget:
        memory[0] = 100
        with pytest.raises(BudgetExceeded):
            checkpoint()
        assert budget.reason == 'memory'
    assert controller.status()['active'] == 0


def test_unhandled_route_exception_still_releases_resources():
    controller = QueryController(memory=lambda: 2048)
    def fail():
        conn = sqlite3.connect(':memory:')
        attach_connection(conn)
        raise ValueError('bad route')
    app, _ = make_app(controller, fail)
    with pytest.raises(ValueError):
        app.test_client().get('/api/runs')
    assert controller.status()['active'] == 0
    assert current_budget() is None


def test_bounded_cache_expires_and_evicts_old_conditions():
    cache = BoundedTTLCache(limit=2)
    cache['expired'] = ('old', time.time()-1)
    cache['a'] = ('A', time.time()+60)
    cache['b'] = ('B', time.time()+60)
    assert cache.get('expired') is None
    assert cache.get('a')[0] == 'A'
    cache['c'] = ('C', time.time()+60)
    assert len(cache) == 2 and cache.get('b') is None
    cache.clear()
    assert not cache


def test_redis_winrate_batches_keep_exact_counts_and_bound_response_size():
    rows = [pickle.dumps((frozenset({'a','b'} if i%2 else {'b'}), 10 if i%3 else 5))
            for i in range(1400)]
    ranges = []
    class Redis:
        def llen(self, key): return len(rows)
        def lrange(self, key, start, end):
            ranges.append((start, end))
            return rows[start:end+1]
    namespace = dict(_redis_client=Redis(), _WINRATE_CACHE_KEY='test',
                     _pickle=pickle, checkpoint=checkpoint)
    tree = ast.parse((ROOT/'web_runs/app.py').read_text(encoding='utf-8'))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_winrate_from_cache')
    exec(compile(ast.Module(body=[fn], type_ignores=[]), 'app.py', 'exec'), namespace)
    total, wins = namespace['_winrate_from_cache']([{'a'}])
    assert total == 700
    assert wins == sum(i%2 == 1 and i%3 != 0 for i in range(1400))
    assert len(ranges) == 3 and max(end-start+1 for start,end in ranges) <= 512


def test_real_runs_handler_closes_query_connection_on_early_validation_return(tmp_path):
    path = tmp_path/'runs.db'
    conn = sqlite3.connect(path)
    class Query:
        def __init__(self, **kwargs): self.conn = conn
        def load(self): attach_connection(self.conn)
        def resolve_hero(self, hero): return None
    namespace = dict(request=request, jsonify=jsonify, RunsQuery=Query, RUNS_DB_PATH=str(path),
                     normalize_run_card_filters=lambda raw: [])
    tree = ast.parse((ROOT/'web_runs/app.py').read_text(encoding='utf-8'))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'api_runs')
    fn.decorator_list = []
    exec(compile(ast.Module(body=[fn], type_ignores=[]), 'app.py', 'exec'), namespace)
    app, _ = make_app(QueryController(memory=lambda: 2048), namespace['api_runs'])
    assert app.test_client().get('/api/runs?hero=invalid').status_code == 400
    with pytest.raises(sqlite3.ProgrammingError): conn.execute('SELECT 1')


def test_same_ranking_conditions_share_work_and_failed_work_can_retry():
    started, release = Event(), Event()
    calls = []
    class Ranking:
        db_path = 'fixture.db'
        @singleflight
        def compute(self, hero):
            calls.append(hero)
            started.set()
            assert release.wait(2)
            return {'hero': hero}
    with ThreadPoolExecutor(max_workers=5) as pool:
        pending = [pool.submit(Ranking().compute, 'Vanessa') for _ in range(5)]
        assert started.wait(1)
        # All workers are inside compute before releasing the leader.
        time.sleep(.05)
        release.set()
        assert [p.result(2) for p in pending] == [{'hero':'Vanessa'}]*5
    assert calls == ['Vanessa']
    class Retry:
        db_path = 'fixture.db'
        calls = 0
        @singleflight
        def compute(self):
            self.calls += 1
            if self.calls == 1:
                raise ValueError('first calculation failed')
            return 'recovered'
    retry = Retry()
    with pytest.raises(ValueError): retry.compute()
    assert retry.compute() == 'recovered'


def test_resource_status_keeps_existing_admin_auth_and_disables_caching(monkeypatch):
    monkeypatch.setenv('STATS_PASSWORD', 'local-test-password')
    app = Flask(__name__)
    guard.install_resource_guard(app, QueryController(memory=lambda: 2048))
    namespace = dict(app=app, request=request, jsonify=jsonify, Response=Response,
                     hmac=hmac, os=os, wraps=wraps)
    tree = ast.parse((ROOT/'web_runs/app.py').read_text(encoding='utf-8'))
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name in ('_stats_authorized', 'require_stats_auth', 'api_stats_resources')]
    exec(compile(ast.Module(body=functions, type_ignores=[]), 'app.py', 'exec'), namespace)
    client = app.test_client()
    assert client.get('/api/stats/resources').status_code == 401
    encoded = base64.b64encode(b'admin:local-test-password').decode()
    response = client.get('/api/stats/resources', headers={'Authorization':'Basic '+encoded})
    assert response.status_code == 200
    assert response.cache_control.private and response.cache_control.no_store
    assert response.json['queries']['limit'] == 2
    assert 'analysis' in response.json
