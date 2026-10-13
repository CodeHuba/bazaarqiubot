import json
from types import SimpleNamespace

import pytest

from tools.zaza_browser_profile_collector import (
    BrowserProfileClient,
    CollectorServerSession,
    process_jobs,
    extract_token_from_performance_log,
)


class FakeDriver:
    def __init__(self, body='[]', status=200):
        self.body, self.status, self.calls = body, status, []

    def execute_async_script(self, script, url):
        self.calls.append((script, url))
        return json.dumps({'status': self.status, 'ok': self.status < 400,
                           'body': self.body})


def run(i, created='2026-10-12T00:00:00Z'):
    return {'id': f'r{i}', 'createdAt': created}


def test_browser_fetch_uses_async_script_and_keeps_token_out_of_server_payload():
    driver = FakeDriver(json.dumps([run(1)]))
    client = BrowserProfileClient(driver, 'SECRET-TOKEN')
    assert client.get('/api/run', {'profileId': 'p', 'sort': 'newest'}) == [run(1)]
    assert len(driver.calls) == 1
    assert 't=SECRET-TOKEN' in driver.calls[0][1]


def test_browser_auth_and_non_array_responses_are_errors():
    with pytest.raises(Exception, match='认证'):
        BrowserProfileClient(FakeDriver('{}', 401), 't').get('/api/run', {})
    with pytest.raises(ValueError, match='array'):
        BrowserProfileClient(FakeDriver('{}'), 't').get('/api/run', {})


class FakeHTTP:
    def __init__(self):
        self.trust_env = True
        self.posts = []

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        path = url.split('example.test', 1)[1]
        if path.endswith('/claim'):
            return Response({'jobs': [JOB]})
        return Response({})


class Response:
    def __init__(self, payload, status=200):
        self.payload, self.status_code = payload, status

    def json(self):
        return self.payload


JOB = {'id': 'j1', 'lease_token': 'lease', 'profile_id': 'p', 'season': 19,
       'phase': '19.1', 'mode': 'scheduled', 'created_after': 'a',
       'created_before': 'b'}


def test_server_session_disables_proxy_and_has_explicit_protocol():
    http = FakeHTTP()
    server = CollectorServerSession('https://example.test', 'server-secret', session=http)
    assert http.trust_env is False
    assert server.claim() == [JOB]
    server.complete(JOB, [run(1)])
    assert http.posts[-1][1]['json'] == {'id': 'j1', 'lease_token': 'lease', 'runs': [run(1)]}
    # server token is intentionally sent as Authorization; browser t token never is.
    assert all('SECRET-TOKEN' not in json.dumps(post) for post in http.posts)


def test_failure_is_reported_and_complete_is_not_sent(monkeypatch):
    http = FakeHTTP()
    server = CollectorServerSession('https://example.test', 's', session=http)
    browser = object()
    def fail(*args, **kwargs):
        raise RuntimeError('request failed')
    monkeypatch.setattr('tools.zaza_browser_profile_collector.collect_profile_runs', fail)
    assert process_jobs(server, browser) == 0
    paths = [url.rsplit('/', 1)[-1] for url, _ in http.posts]
    assert paths == ['claim', 'failure']
    assert http.posts[-1][1]['json']['id'] == 'j1'


def test_performance_log_extracts_token_without_printing_it():
    token = 'v2.secret-value'
    driver = SimpleNamespace(get_log=lambda kind: [{
        'message': json.dumps({'message': {'method': 'Network.requestWillBeSent',
            'params': {'request': {'url': 'https://x/api/run?sort=x&t=' + token}}}})
    }])
    assert extract_token_from_performance_log(driver) == token
