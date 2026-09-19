import json
import sqlite3

import pytest


@pytest.fixture
def stats_db(tmp_path):
    path = tmp_path / "day-stats.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE build_versions (
                version_id TEXT PRIMARY KEY, schema_version INTEGER NOT NULL,
                built_at TEXT NOT NULL, source_path TEXT NOT NULL, source_cutoff TEXT,
                source_success_run_count INTEGER NOT NULL,
                source_snapshot_count INTEGER NOT NULL,
                final_composition_count INTEGER NOT NULL
            );
            CREATE TABLE early_day_item_cores (
                version_id TEXT NOT NULL, season INTEGER NOT NULL, phase TEXT NOT NULL,
                hero TEXT NOT NULL, day INTEGER NOT NULL, core_id TEXT NOT NULL,
                card_ids TEXT NOT NULL, support_runs INTEGER NOT NULL,
                observable_runs INTEGER NOT NULL, coverage REAL NOT NULL,
                rank INTEGER NOT NULL, min_support INTEGER NOT NULL,
                PRIMARY KEY (version_id, season, phase, hero, day, core_id)
            );
            CREATE TABLE item_core_route_nodes (
                version_id TEXT NOT NULL, core_id TEXT NOT NULL, day INTEGER NOT NULL,
                signature TEXT NOT NULL, run_count INTEGER NOT NULL,
                observable_runs INTEGER NOT NULL, parent_rate REAL NOT NULL,
                start_rate REAL NOT NULL, added TEXT NOT NULL, removed TEXT NOT NULL,
                retained TEXT NOT NULL,
                PRIMARY KEY (version_id, core_id, day, signature)
            );
            CREATE TABLE item_core_route_edges (
                version_id TEXT NOT NULL, core_id TEXT NOT NULL, parent_day INTEGER NOT NULL,
                parent_signature TEXT NOT NULL, child_day INTEGER NOT NULL,
                child_signature TEXT NOT NULL, run_count INTEGER NOT NULL,
                observable_runs INTEGER NOT NULL, transition_rate REAL NOT NULL,
                PRIMARY KEY (version_id, core_id, parent_day, parent_signature,
                             child_day, child_signature)
            );
            """
        )
        conn.execute("INSERT INTO build_versions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                     ("v1", 3, "2026-09-19T00:00:00Z", "/source.db", "2026-09-18T13:01:00", 12, 99, 77))
        conn.execute("INSERT INTO build_versions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                     ("v2", 3, "2026-09-20T00:00:00Z", "/source.db", "2026-09-19T13:01:00", 13, 109, 88))
        conn.execute("INSERT INTO early_day_item_cores VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     ("v2", 18, "18.2", "Vanessa", 1, "core-1", '["a","b"]', 5, 10, .5, 1, 2))
        conn.execute("INSERT INTO early_day_item_cores VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     ("v1", 18, "18.2", "Vanessa", 1, "core-old", '["old"]', 2, 8, .25, 1, 2))
        conn.execute("INSERT INTO item_core_route_nodes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     ("v2", "core-1", 1, '["a","b"]', 5, 10, 1.0, .5, '[]', '[]', '["a","b"]'))
        conn.execute("INSERT INTO item_core_route_nodes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     ("v2", "core-1", 2, '["a","b","c"]', 3, 8, .375, .3, '["c"]', '[]', '["a","b"]'))
        conn.execute("INSERT INTO item_core_route_edges VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     ("v2", "core-1", 1, '["a","b"]', 2, '["a","b","c"]', 3, 5, .6))
    return path


def _client(monkeypatch, stats_db):
    monkeypatch.setenv("DAY_STATS_DB_PATH", str(stats_db))
    monkeypatch.setenv("STATS_DB_PATH", str(stats_db.parent / "stats.db"))
    monkeypatch.setenv("STATS_PASSWORD", "test-password")
    import importlib
    import sys
    app_path = "/mnt/d/PJ/QiuBot-github/QiuBot/web_runs"
    repo_path = "/mnt/d/PJ/QiuBot-github/QiuBot"
    for path in (repo_path, app_path):
        if path not in sys.path:
            sys.path.insert(0, path)
    import types
    bazaar_stub = types.ModuleType("plugins.bazaar_plugin")
    bazaar_stub.__path__ = [repo_path + "/plugins/bazaar_plugin"]
    monkeypatch.setitem(sys.modules, "plugins.bazaar_plugin", bazaar_stub)
    data_stub = types.ModuleType("plugins.bazaar_plugin.data_client")
    data_stub.RUNS_SEASON_ID = 18
    data_stub.CURRENT_PHASE = "18.2"
    monkeypatch.setitem(sys.modules, "plugins.bazaar_plugin.data_client", data_stub)
    rq_stub = types.ModuleType("plugins.bazaar_plugin.runs_query")
    class RunsQueryStub:
        def load(self):
            self.card_mapping = {}
            self.size_map = {}
        def card_display_info_cached(self, card_id):
            return {
                "cardId": card_id, "name": f"中文-{card_id}",
                "name_en": f"English-{card_id}",
                "img": f"/static/card-art/{card_id}.webp", "size": "Small",
            }
    rq_stub.RunsQuery = RunsQueryStub
    monkeypatch.setitem(sys.modules, "plugins.bazaar_plugin.runs_query", rq_stub)
    ocr_stub = types.ModuleType("ocr_worker")
    setattr(ocr_stub, "start_worker", lambda: None)
    setattr(ocr_stub, "enqueue_run", lambda *args, **kwargs: None)
    monkeypatch.setitem(sys.modules, "ocr_worker", ocr_stub)
    import sqlite3 as sqlite_module
    original_connect = sqlite_module.connect
    def connect_redirect(path, *args, **kwargs):
        if isinstance(path, str) and path.startswith('/opt/qiubot/data/'):
            path = str(stats_db.parent / ('stats.db' if path.endswith('stats.db') else 'compat.db'))
        return original_connect(path, *args, **kwargs)
    monkeypatch.setattr(sqlite_module, "connect", connect_redirect)
    web_app = importlib.import_module("app")
    monkeypatch.setattr(sqlite_module, "connect", original_connect)
    monkeypatch.setattr(web_app, "DAY_STATS_DB_PATH", str(stats_db), raising=False)
    monkeypatch.setattr(web_app, "_day_stats_redis_client", None)
    web_app._day_stats_memory_cache.clear()
    raw_client = web_app.app.test_client()
    class AuthClient:
        def __init__(self, app_module):
            self.web_app = app_module

        def get(self, *args, **kwargs):
            kwargs.setdefault("headers", {}).update(_auth())
            return raw_client.get(*args, **kwargs)
    return AuthClient(web_app)


def _auth():
    import base64
    return {"Authorization": "Basic " + base64.b64encode(b"tester:test-password").decode()}


def test_day_stats_latest_returns_version_metadata(monkeypatch, stats_db):
    response = _client(monkeypatch, stats_db).get("/api/day-stats/latest")
    assert response.status_code == 200
    assert response.get_json() == {
        "version_id": "v2", "schema_version": 3,
        "built_at": "2026-09-20T00:00:00Z",
        "source_cutoff": "2026-09-19T13:01:00",
        "source_success_run_count": 13, "source_snapshot_count": 109,
        "final_composition_count": 88,
    }


def test_day_stats_cores_resolves_latest_and_lists_early_cores(monkeypatch, stats_db):
    response = _client(monkeypatch, stats_db).get(
        "/api/day-stats/cores?version=latest&hero=Vanessa&day=1"
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["version_id"] == "v2"
    assert body["source_cutoff"] == "2026-09-19T13:01:00"
    assert body["sample_counts"] == {"source_success_run_count": 13, "source_snapshot_count": 109,
                                      "final_composition_count": 88}
    assert body["cores"][0]["core_id"] == "core-1"
    assert body["cores"][0]["card_ids"] == ["a", "b"]
    assert body["cores"][0]["cards"] == [
        {"cardId": "a", "name": "中文-a", "name_en": "English-a",
         "img": "/static/card-art/a.webp", "size": "Small"},
        {"cardId": "b", "name": "中文-b", "name_en": "English-b",
         "img": "/static/card-art/b.webp", "size": "Small"},
    ]


def test_day_stats_route_returns_nodes_and_edges_for_explicit_version(monkeypatch, stats_db):
    response = _client(monkeypatch, stats_db).get(
        "/api/day-stats/cores/core-1/route?version=v2"
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["version_id"] == "v2"
    assert len(body["nodes"]) == 2
    assert body["nodes"][0]["signature_ids"] == ["a", "b"]
    assert [card["name"] for card in body["nodes"][0]["cards"]] == ["中文-a", "中文-b"]
    assert body["nodes"][1]["added"] == ["c"]
    assert body["nodes"][1]["added_cards"][0]["name"] == "中文-c"
    assert body["nodes"][1]["day_observable_rate"] == .375
    assert "parent_rate" not in body["nodes"][1]
    assert body["edges"][0]["transition_rate"] == .6


def test_day_routes_page_requires_basic_auth_and_serves_static_contract(monkeypatch, stats_db):
    client = _client(monkeypatch, stats_db)
    raw_client = client.web_app.app.test_client()
    unauthorized = raw_client.get("/admin/day-routes")
    assert unauthorized.status_code == 401
    assert "Basic" in unauthorized.headers["WWW-Authenticate"]

    response = client.get("/admin/day-routes")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'href="/static/day-routes.css"' in html
    assert 'src="/static/day-routes.js"' in html
    assert "传奇10胜样本" in html
    assert "职业选择" in html
    assert "Day 1" in html and "Day 2" in html and "Day 3" in html


def test_day_routes_frontend_uses_same_origin_auth_and_readable_route_semantics(monkeypatch, stats_db):
    client = _client(monkeypatch, stats_db)
    js = client.get("/static/day-routes.js").get_data(as_text=True)
    css = client.get("/static/day-routes.css").get_data(as_text=True)
    assert "credentials: 'same-origin'" in js
    assert "STATS_PASSWORD" not in js
    for label in ("新增", "移除", "保留", "OTHER", "当日样本占比", "起始占比"):
        assert label in js
    assert "父节点转型率" not in js
    assert "AbortController" in js
    assert "requestToken" in js
    assert "object-fit: contain" in css


@pytest.mark.parametrize("url", [
    "/api/day-stats/cores?hero=Vanessa&day=1",
    "/api/day-stats/cores/core-1/route",
])
def test_day_stats_requires_version_or_latest(monkeypatch, stats_db, url):
    response = _client(monkeypatch, stats_db).get(url)
    assert response.status_code == 400
    assert response.get_json()["error"] == "version is required"


@pytest.mark.parametrize("url", [
    "/api/day-stats/cores?version=bad%20version&hero=Vanessa&day=1",
    "/api/day-stats/cores?version=" + ("v" * 81) + "&hero=Vanessa&day=1",
    "/api/day-stats/cores/core-1/route?version=bad%2Fversion",
    "/api/day-stats/cores/core-1/route?version=" + ("v" * 81),
])
def test_day_stats_rejects_invalid_version_before_cache_or_database(monkeypatch, stats_db, url):
    client = _client(monkeypatch, stats_db)

    def unexpected_access(*_args, **_kwargs):
        raise AssertionError("invalid version must be rejected before cache/database access")

    monkeypatch.setattr(client.web_app, "_day_stats_cache_get", unexpected_access)
    monkeypatch.setattr(client.web_app, "_day_stats_connection", unexpected_access)

    response = client.get(url)
    assert response.status_code == 400
    assert response.get_json() == {
        "error": "invalid version; expected latest or 1-80 characters matching [A-Za-z0-9][A-Za-z0-9_.-]*"
    }


@pytest.mark.parametrize("version", [
    "latest", "v2", "Release_2026.09-20", "A" * 80,
])
def test_day_stats_version_validator_accepts_supported_format(monkeypatch, stats_db, version):
    client = _client(monkeypatch, stats_db)
    assert client.web_app._valid_day_stats_version(version)


def test_day_stats_returns_404_for_missing_version_or_core(monkeypatch, stats_db):
    client = _client(monkeypatch, stats_db)
    assert client.get("/api/day-stats/cores?version=nope&hero=Vanessa&day=1").status_code == 404
    assert client.get("/api/day-stats/cores/nope/route?version=v2").status_code == 404


def test_day_stats_cache_hit_avoids_second_database_connection(monkeypatch, stats_db):
    client = _client(monkeypatch, stats_db)
    original = client.web_app._day_stats_connection
    calls = 0

    def counted_connection():
        nonlocal calls
        calls += 1
        return original()

    monkeypatch.setattr(client.web_app, "_day_stats_connection", counted_connection)
    url = "/api/day-stats/cores?version=latest&hero=Vanessa&day=1"
    assert client.get(url).status_code == 200
    assert client.get(url).status_code == 200
    assert calls == 1


def test_all_day_stats_resources_are_cached_without_public_auth_response(monkeypatch, stats_db):
    client = _client(monkeypatch, stats_db)
    original = client.web_app._day_stats_connection
    calls = 0

    def counted_connection():
        nonlocal calls
        calls += 1
        return original()

    monkeypatch.setattr(client.web_app, "_day_stats_connection", counted_connection)
    urls = [
        "/api/day-stats/latest",
        "/api/day-stats/cores?version=v2&hero=Vanessa&day=1",
        "/api/day-stats/cores/core-1/route?version=v2",
    ]
    first = [client.get(url) for url in urls]
    assert calls == 3
    second = [client.get(url) for url in urls]
    assert calls == 3
    assert all("private" in response.headers["Cache-Control"] for response in first + second)
    assert all("no-store" in response.headers["Cache-Control"] for response in first + second)


def test_day_stats_cache_isolated_by_resolved_version(monkeypatch, stats_db):
    client = _client(monkeypatch, stats_db)
    latest = client.get("/api/day-stats/cores?version=latest&hero=Vanessa&day=1").get_json()
    old = client.get("/api/day-stats/cores?version=v1&hero=Vanessa&day=1").get_json()
    assert latest["version_id"] == "v2"
    assert latest["cores"][0]["core_id"] == "core-1"
    assert old["version_id"] == "v1"
    assert old["cores"][0]["core_id"] == "core-old"


def test_day_stats_cache_failure_falls_back_to_sqlite(monkeypatch, stats_db):
    client = _client(monkeypatch, stats_db)

    class BrokenCache:
        def get(self, key):
            raise RuntimeError("cache unavailable")

        def setex(self, key, ttl, value):
            raise RuntimeError("cache unavailable")

    monkeypatch.setattr(client.web_app, "_day_stats_redis_client", BrokenCache())
    client.web_app._day_stats_memory_cache.clear()
    response = client.get("/api/day-stats/cores?version=v2&hero=Vanessa&day=1")
    assert response.status_code == 200
    assert response.get_json()["cores"][0]["core_id"] == "core-1"


def test_redis_backfill_uses_version_ttl_for_immutable_keys(monkeypatch, stats_db):
    client = _client(monkeypatch, stats_db)
    now = 1000.0

    class RedisHit:
        def get(self, _key):
            return json.dumps({"value": 1})

    monkeypatch.setattr(client.web_app, "_day_stats_redis_client", RedisHit())
    monkeypatch.setattr(client.web_app.time, "time", lambda: now)
    client.web_app._day_stats_memory_cache.clear()
    key = client.web_app._day_stats_result_cache_key("v2", "route", "core-1")

    assert client.web_app._day_stats_cache_get(key) == {"value": 1}
    expires_at, _value = client.web_app._day_stats_memory_cache[key]
    assert expires_at == now + client.web_app._DAY_STATS_VERSION_TTL


@pytest.mark.parametrize("url", [
    "/api/day-stats/cores?version=v2&hero=Vanessa&day=0",
    "/api/day-stats/cores?version=v2&hero=Vanessa&day=4",
    "/api/day-stats/cores?version=v2&hero=Vanessa&day=one",
    "/api/day-stats/cores?version=v2&hero=%3Cscript%3E&day=1",
    "/api/day-stats/cores?version=v2&hero=" + ("V" * 41) + "&day=1",
    "/api/day-stats/cores/bad%20core/route?version=v2",
])
def test_day_stats_rejects_invalid_inputs(monkeypatch, stats_db, url):
    response = _client(monkeypatch, stats_db).get(url)
    assert response.status_code == 400
