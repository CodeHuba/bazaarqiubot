import pytest


def test_complete_failure_uses_atomic_lease_guard(tmp_path):
    from web_runs.day_snapshot_store import DaySnapshotStore

    store = DaySnapshotStore(tmp_path / 'day.db')
    store.initialize()
    store.enqueue_if_eligible('run-1', 18, '18.2', 'Vanessa', 'Legendary', 10)
    job = store.claim()[0]
    store.complete_failure('run-1', 'failure', job['lease_token'])


def test_worker_requires_https(monkeypatch):
    import importlib.util
    from pathlib import Path

    path = Path(__file__).parents[1] / 'tools' / 'bazaardb_snapshot_worker.py'
    monkeypatch.setenv('BAZAAR_INGEST_API', 'http://example.test/api/ingest')
    monkeypatch.setenv('BAZAAR_INGEST_TOKEN', 'token')
    monkeypatch.syspath_prepend(str(path.parent))
    spec = importlib.util.spec_from_file_location('worker_https_test', path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    with pytest.raises(RuntimeError, match='HTTPS'):
        spec.loader.exec_module(module)
