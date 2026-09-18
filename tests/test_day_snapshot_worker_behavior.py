import importlib.util
import sys
from pathlib import Path


WORKER = Path(__file__).parents[1] / 'tools' / 'bazaardb_snapshot_worker.py'


def _load_worker(monkeypatch):
    monkeypatch.setenv('BAZAAR_INGEST_API', 'https://example.test/api/ingest')
    monkeypatch.setenv('BAZAAR_INGEST_TOKEN', 'test-token')
    tools = str(WORKER.parent)
    if tools not in sys.path:
        sys.path.insert(0, tools)
    spec = importlib.util.spec_from_file_location('snapshot_worker_under_test', WORKER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_auth_failures_refresh_browser_once_per_batch(monkeypatch):
    worker = _load_worker(monkeypatch)
    monkeypatch.setattr(worker, 'reconcile_current_phase', lambda control: None)
    monkeypatch.setattr(worker, 'claim_jobs', lambda control: [
        {'run_id': 'a'}, {'run_id': 'b'},
    ])

    calls = []

    def fake_process(browser, job, allow_auth_retry=True):
        calls.append((job['run_id'], allow_auth_retry))
        if allow_auth_retry:
            raise worker.AuthenticationExpired('expired', 403)

    monkeypatch.setattr(worker, 'process_job', fake_process)

    class Session:
        trust_env = False
        def close(self):
            pass

    class Browser:
        refreshes = 0
        def control_session(self):
            return Session()
        def refresh_authentication(self):
            self.refreshes += 1

    browser = Browser()
    assert worker.process_once(browser) == 2
    assert browser.refreshes == 1
    assert sorted(calls) == [('a', False), ('a', True), ('b', False), ('b', True)]


def test_reconcile_failure_does_not_block_claim(monkeypatch):
    worker = _load_worker(monkeypatch)
    monkeypatch.setattr(worker, 'reconcile_current_phase', lambda control: (_ for _ in ()).throw(RuntimeError('503')))
    claimed = [{'run_id': 'a', 'lease_token': 'lease-a'}]
    monkeypatch.setattr(worker, 'claim_jobs', lambda control: claimed)
    monkeypatch.setattr(worker, 'process_job', lambda browser, job, allow_auth_retry=True: None)

    class Session:
        trust_env = False
        def close(self):
            pass

    class Browser:
        def control_session(self):
            return Session()

    assert worker.process_once(Browser()) == 1


def test_worker_defaults_to_exactly_five_claims_per_minute(monkeypatch):
    worker = _load_worker(monkeypatch)
    assert worker.SNAPSHOT_BATCH_SIZE == 5
    assert worker.WORKER_POLL_SECONDS == 60
    assert worker.SNAPSHOT_WORKERS == 5
    assert worker.SNAPSHOT_CHROME_MAJOR == 0
    assert worker.RECONCILE_INTERVAL_SECONDS == 3600
    assert worker.SNAPSHOT_BATCH_SIZE * 3600 // worker.WORKER_POLL_SECONDS == 300


def test_zero_reconcile_interval_disables_reconcile_but_still_claims(monkeypatch):
    monkeypatch.setenv('SNAPSHOT_RECONCILE_INTERVAL_SECONDS', '0')
    worker = _load_worker(monkeypatch)
    reconcile_calls = []
    monkeypatch.setattr(worker, 'reconcile_current_phase', lambda control: reconcile_calls.append(True))
    monkeypatch.setattr(worker, 'claim_jobs', lambda control: [])

    class Session:
        trust_env = False
        def close(self):
            pass

    class Browser:
        def control_session(self):
            return Session()

    assert worker.process_once(Browser()) == 0
    assert reconcile_calls == []
