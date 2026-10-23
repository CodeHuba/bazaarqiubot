from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
import time
from types import SimpleNamespace

import pytest

from test_runs_list_performance import runs_query
from plugins.bazaar_plugin import comp_analysis_store as store


@pytest.fixture
def cache(tmp_path, monkeypatch):
    # Refresh publication tests do not wait for the production coalescing window.
    monkeypatch.setattr(store, '_REFRESH_MIN_INTERVAL', 0)
    store.invalidate(hard=True)
    database=tmp_path/'runs.db';database.write_text('one')
    class Query:
        db_path=str(database)
        mapping_path='mapping'
        translations_path='translations'
        def __init__(self,**kwargs):pass
        def metadata_version(self):return 'same-cards'
    calls=[]
    def build(query,season,phase,previous=None):
        signature=store._signature(query)
        value=Path(query.db_path).read_text()
        calls.append(value)
        engine=SimpleNamespace(source_count=len(calls),payload={'captured_at':value},query=lambda *a,**k:value)
        return engine,signature
    monkeypatch.setattr(store,'_build',build)
    yield Query,calls,build,database
    for entry in list(store._engines.values()):
        if entry.refreshing:entry.ready.wait(3)
    store.invalidate(hard=True)


def analyze(Query):
    return store.analyze(Query(),['q'],season=19,phase='19.2')


def test_unchanged_data_does_not_rebuild_after_sixty_seconds(cache,monkeypatch):
    Query,calls,_,_=cache
    assert analyze(Query)=='one'
    monkeypatch.setattr(store,'time',SimpleNamespace(monotonic=lambda:1000000))
    for _ in range(10):assert analyze(Query)=='one'
    assert calls==['one']


def test_cold_concurrent_requests_share_one_build(cache,monkeypatch):
    Query,calls,original,_=cache
    started,release=Event(),Event()
    def build(*args):
        started.set();assert release.wait(3)
        return original(*args)
    monkeypatch.setattr(store,'_build',build)
    with ThreadPoolExecutor(max_workers=5) as pool:
        pending=[pool.submit(analyze,Query) for _ in range(5)]
        assert started.wait(2);release.set()
        assert [p.result(3) for p in pending]==['one']*5
    assert calls==['one']


def test_db_change_serves_old_results_during_one_refresh_then_swaps(cache,monkeypatch):
    Query,calls,original,database=cache
    assert analyze(Query)=='one'
    started,release=Event(),Event()
    def build(*args):
        started.set();assert release.wait(3)
        return original(*args)
    monkeypatch.setattr(store,'_build',build)
    database.write_text('two-data')
    start=time.perf_counter()
    assert analyze(Query)=='one'
    assert time.perf_counter()-start<.2
    assert started.wait(2)
    for _ in range(5):assert analyze(Query)=='one'
    assert store.cache_status()[0]['refreshing']
    release.set();store.prewarm(Query,season=19,phase='19.2',wait=True)
    assert analyze(Query)=='two-data'
    assert calls==['one','two-data']


def test_ingest_invalidation_keeps_previous_engine_and_failed_refresh_can_retry(cache,monkeypatch):
    Query,calls,original,_=cache
    assert analyze(Query)=='one'
    def fail(*args):raise OSError('temporary read failure')
    monkeypatch.setattr(store,'_build',fail)
    store.invalidate()
    entry=next(iter(store._engines.values()));assert entry.ready.wait(2)
    assert analyze(Query)=='one'
    assert store.cache_status()[0]['error']=='OSError'
    monkeypatch.setattr(store,'_build',original)
    entry.failed_at-=6
    store.prewarm(Query,season=19,phase='19.2',wait=True)
    assert store.cache_status()[0]['error'] is None
    assert len(calls)==2


def test_queries_for_one_new_condition_share_one_calculation(monkeypatch):
    from plugins.bazaar_plugin.comp_analysis import Analysis
    engine=Analysis(dict(runs=[dict(id='1',hero='Vanessa',wins=10,created_at='2026-10-19T00:00:00Z',
        items=[{'cardId':'q'}],skills=[])],cards={'q':{'type':'Item'}},season=19,phase='19.2',
        captured_at='2026-10-19T00:00:00Z',source='test'))
    original=engine._compute;started,release=Event(),Event();calls=[]
    def compute(*args):
        calls.append(1);started.set();assert release.wait(2)
        return original(*args)
    monkeypatch.setattr(engine,'_compute',compute)
    with ThreadPoolExecutor(max_workers=5) as pool:
        pending=[pool.submit(engine.query,['q']) for _ in range(5)]
        assert started.wait(1);release.set()
        results=[f.result(3) for f in pending]
    assert len(calls)==1
    assert all(row==results[0] for row in results)


def test_live_day_window_advances_without_rebuilding_data(monkeypatch):
    from plugins.bazaar_plugin import comp_analysis
    from datetime import datetime,timezone
    start=datetime(2026,10,19,tzinfo=timezone.utc).timestamp()
    monkeypatch.setattr(comp_analysis,'time',SimpleNamespace(time=lambda:start))
    engine=comp_analysis.Analysis(dict(runs=[dict(id='1',hero='Vanessa',wins=10,created_at='2026-10-18T00:00:30Z',
        items=[{'cardId':'q'}],skills=[])],cards={'q':{'type':'Item'}},season=19,phase='19.2',
        captured_at='2026-10-19T00:00:00Z',source='test',live=True))
    assert engine.query(['q'],days=1)['baseline']['count']==1
    monkeypatch.setattr(comp_analysis,'time',SimpleNamespace(time=lambda:start+60))
    assert engine.query(['q'],days=1)['baseline']['count']==0


def test_ingest_burst_is_coalesced_and_eventually_publishes_latest_data(cache, monkeypatch):
    Query, calls, _, database = cache
    monkeypatch.setattr(store, '_REFRESH_MIN_INTERVAL', 60)
    assert analyze(Query) == 'one'
    entry = next(iter(store._engines.values()))
    database.write_text('latest committed records')
    for _ in range(100):
        store.invalidate()
        assert analyze(Query) == 'one'
    assert calls == ['one']
    assert not entry.refreshing
    entry.last_started -= 61
    store.prewarm(Query, season=19, phase='19.2')
    assert entry.ready.wait(2)
    assert analyze(Query) == 'latest committed records'
    assert len(calls) == 2
    assert entry.built_revision == entry.revision


def test_failed_build_does_not_retain_large_local_variables(cache, monkeypatch):
    Query, _, _, _ = cache
    def fail(*args):
        large_records = bytearray(1000000)
        raise OSError('failed after allocation')
    monkeypatch.setattr(store, '_build', fail)
    entry = store.prewarm(Query, season=19, phase='19.2')
    assert entry.ready.wait(2)
    assert isinstance(entry.error, OSError)
    assert entry.error.__traceback__ is None
    assert entry.error.__context__ is None


def test_cold_http_query_retries_instead_of_waiting_for_its_own_worker_slot(cache, monkeypatch):
    from plugins.bazaar_plugin.query_budget import QueryController, BudgetExceeded
    Query, _, original, _ = cache
    admission = QueryController(limit=1, memory=lambda: 2048)
    monkeypatch.setattr(store, 'controller', admission)
    attempted = Event()
    original_acquire = admission.acquire
    def acquire(**kwargs):
        try:
            return original_acquire(**kwargs)
        finally:
            if kwargs.get('label') == 'comp-refresh':
                attempted.set()
    monkeypatch.setattr(admission, 'acquire', acquire)
    started, release = Event(), Event()
    def build(*args):
        started.set()
        assert release.wait(2)
        return original(*args)
    monkeypatch.setattr(store, '_build', build)
    with admission.acquire():
        with pytest.raises(BudgetExceeded):
            analyze(Query)
        assert attempted.wait(1)
    assert admission.status()['active'] == 0
    entry = next(iter(store._engines.values()))
    assert entry.ready.wait(2)  # The refresh was rejected by the occupied slot.
    entry.failed_at -= 6
    store.prewarm(Query, season=19, phase='19.2')
    try:
        assert started.wait(2)
    finally:
        release.set()
    assert entry.ready.wait(2)
    assert analyze(Query) == 'one'
