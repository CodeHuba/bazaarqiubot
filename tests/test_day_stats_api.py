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
            CREATE TABLE daily_archetype_nodes (
                version_id TEXT NOT NULL, season INTEGER NOT NULL, phase TEXT NOT NULL,
                hero TEXT NOT NULL, day INTEGER NOT NULL, node_id TEXT NOT NULL,
                rank INTEGER NOT NULL, run_count INTEGER NOT NULL,
                day_observable_runs INTEGER NOT NULL, day_share REAL NOT NULL,
                core_items TEXT NOT NULL, core_support_runs INTEGER NOT NULL,
                core_support_rate REAL NOT NULL, representative_items TEXT NOT NULL,
                PRIMARY KEY (version_id, hero, day, node_id)
            );
            CREATE TABLE daily_archetype_cards (
                version_id TEXT NOT NULL, node_id TEXT NOT NULL, day INTEGER NOT NULL,
                card_id TEXT NOT NULL, role TEXT NOT NULL, support_runs INTEGER NOT NULL,
                support_rate REAL NOT NULL,
                PRIMARY KEY (version_id, node_id, card_id, role)
            );
            CREATE TABLE daily_archetype_edges (
                version_id TEXT NOT NULL, edge_id TEXT NOT NULL,
                parent_day INTEGER NOT NULL, parent_node_id TEXT NOT NULL,
                child_day INTEGER NOT NULL, child_node_id TEXT NOT NULL,
                run_count INTEGER NOT NULL, parent_runs INTEGER NOT NULL,
                parent_observable_runs INTEGER NOT NULL, continuation_rate REAL NOT NULL,
                transition_rate REAL NOT NULL, stage_gap INTEGER NOT NULL,
                PRIMARY KEY (version_id, edge_id)
            );
            CREATE TABLE daily_archetype_edge_members (
                version_id TEXT NOT NULL, edge_id TEXT NOT NULL, run_id TEXT NOT NULL,
                PRIMARY KEY (version_id, edge_id, run_id)
            );
            CREATE TABLE daily_archetype_members (
                version_id TEXT NOT NULL, node_id TEXT NOT NULL, day INTEGER NOT NULL,
                run_id TEXT NOT NULL,
                PRIMARY KEY (version_id, node_id, day, run_id)
            );
            CREATE TABLE final_compositions (
                version_id TEXT NOT NULL, run_id TEXT NOT NULL, event_id TEXT NOT NULL,
                season INTEGER NOT NULL, phase TEXT NOT NULL, hero TEXT,
                player_rank TEXT NOT NULL, day INTEGER NOT NULL, day_stage TEXT NOT NULL,
                hour INTEGER, captured_at TEXT, fetched_at TEXT NOT NULL,
                player_level INTEGER, outcome TEXT, is_pvp INTEGER,
                opponent_hero TEXT, opponent_name TEXT, item_signature TEXT NOT NULL,
                PRIMARY KEY (version_id, run_id, day)
            );
            CREATE TABLE daily_archetype_edge_cards (
                version_id TEXT NOT NULL, edge_id TEXT NOT NULL, change_kind TEXT NOT NULL,
                card_id TEXT NOT NULL, run_count INTEGER NOT NULL, change_rate REAL NOT NULL,
                PRIMARY KEY (version_id, edge_id, change_kind, card_id)
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
        conn.execute("INSERT INTO daily_archetype_nodes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     ("v2", 18, "18.2", "Vanessa", 3, "daily-a", 1, 10, 20, .5,
                      '["a","b"]', 8, .8, '["a","b","x"]'))
        conn.execute("INSERT INTO daily_archetype_nodes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     ("v2", 18, "18.2", "Vanessa", 4, "daily-b", 1, 6, 18, 1/3,
                      '["a","c"]', 4, 2/3, '["a","c","y"]'))
        conn.execute("INSERT INTO daily_archetype_nodes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     ("v2", 18, "18.2", "Vanessa", 4, "daily-dispersed", 2, 4, 18, 2/9,
                      '[]', 0, 0.0, '["z"]'))
        conn.execute("INSERT INTO daily_archetype_cards VALUES (?, ?, ?, ?, ?, ?, ?)",
                     ("v2", "daily-a", 3, "a", "core", 10, 1.0))
        conn.execute("INSERT INTO daily_archetype_cards VALUES (?, ?, ?, ?, ?, ?, ?)",
                     ("v2", "daily-b", 4, "c", "core", 6, 1.0))
        conn.execute("INSERT INTO daily_archetype_edges VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     ("v2", "edge-daily", 3, "daily-a", 4, "daily-b", 6, 10, 8, .8, .75, 0))
        conn.execute("INSERT INTO daily_archetype_edges VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     ("v2", "edge-dispersed", 3, "daily-a", 4, "daily-dispersed", 2, 10, 8, .8, .25, 0))
        conn.execute("INSERT INTO daily_archetype_edge_cards VALUES (?, ?, ?, ?, ?, ?)",
                     ("v2", "edge-daily", "added_core", "c", 6, 1.0))
        for number in range(6):
            run_id = f"route-{number}"
            items = ["a", "c", "x"] if number < 5 else ["a", "c", "y"]
            conn.execute("INSERT INTO daily_archetype_edge_members VALUES (?, ?, ?)",
                         ("v2", "edge-daily", run_id))
            conn.execute("INSERT INTO final_compositions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         ("v2", run_id, f"event-{number}", 18, "18.2", "Vanessa",
                          "Legendary", 4, "middle", None, None, "2026-09-20", None,
                          None, None, None, None, json.dumps(items)))
        conn.commit()
    return path


def _client(monkeypatch, stats_db):
    monkeypatch.setenv("DAY_STATS_DB_PATH", str(stats_db))
    monkeypatch.setenv("STATS_DB_PATH", str(stats_db.parent / "stats.db"))
    monkeypatch.setenv("STATS_PASSWORD", "test-password")
    import importlib
    import sys
    import types
    from pathlib import Path
    repo_path = str(Path(__file__).resolve().parents[1])
    app_path = str(Path(repo_path) / "web_runs")
    for path in (repo_path, app_path):
        if path not in sys.path:
            sys.path.insert(0, path)
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
    for module_name in ("app", "day_stats_builder"):
        sys.modules.pop(module_name, None)
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


def test_day_stats_batched_rows_handles_more_than_sqlite_default_variable_limit(monkeypatch, stats_db):
    client = _client(monkeypatch, stats_db)
    values = [f"run-{number}" for number in range(1205)]
    with sqlite3.connect(stats_db) as conn:
        rows = client.web_app._day_stats_batched_rows(
            conn, "SELECT ? AS version_id, value FROM json_each(?) WHERE value IN ({placeholders})",
            ("v2", json.dumps(values)), values,
        )
    assert len(rows) == len(values)


def test_public_daily_routes_api_returns_nodes_edges_and_changes(monkeypatch, stats_db):
    client = _client(monkeypatch, stats_db)
    response = client.web_app.app.test_client().get(
        "/api/routes/daily?version=v2&hero=Vanessa&day=3"
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["nodes"][0]["node_id"] == "daily-a"
    assert body["nodes"][0]["core_items"] == ["a", "b"]
    assert body["nodes"][0]["core_support_runs"] == 8
    assert body["nodes"][0]["core_support_rate"] == .8
    assert body["edges"][0]["child_day"] == 4
    assert body["edges"][0]["transition_rate"] == .75
    assert body["edges"][0]["changes"][0]["change_kind"] == "added_core"
    assert body["edges"][0]["changes"][0]["card_id"] == "c"
    directions = body["directions"]["daily-a"]
    assert directions[0]["run_count"] == 6
    assert directions[0]["target_core_items"] == ["a", "c"]
    assert directions[0]["variants"][0]["target_core_items"] == ["a", "c"]
    assert directions[0]["representative_run_id"].startswith("route-")
    assert directions[0]["associated_cards"][0]["card_id"] == "x"
    assert directions[0]["associated_cards"][0]["support_rate"] == pytest.approx(5 / 6)
    assert len(directions) <= 3
    assert [edge["child_node_id"] for edge in body["edges"]] == ["daily-b"]


def test_public_daily_routes_hides_dispersed_nodes_from_summary(monkeypatch, stats_db):
    client = _client(monkeypatch, stats_db)
    response = client.web_app.app.test_client().get(
        "/api/routes/daily/summary?version=v2&hero=Vanessa"
    )
    assert response.status_code == 200
    assert "daily-dispersed" not in [row["node_id"] for row in response.get_json()["nodes"]]


def test_public_daily_routes_summary_groups_all_days(monkeypatch, stats_db):
    client = _client(monkeypatch, stats_db)
    response = client.web_app.app.test_client().get(
        "/api/routes/daily/summary?version=v2&hero=Vanessa"
    )
    assert response.status_code == 200
    assert [row["day"] for row in response.get_json()["nodes"]] == [3, 4]


def test_public_daily_node_detail_uses_global_members_for_rankings(monkeypatch, stats_db):
    snapshots = {
        "detail-1": ["a", "b", "x", "y"],
        "detail-2": ["a", "b", "x", "y"],
        "detail-3": ["a", "b", "x", "y", "z"],
        "detail-4": ["a", "b", "y", "z"],
        "detail-5": ["a", "x", "z"],
        "detail-6": ["a", "b", "p", "q"],
    }
    with sqlite3.connect(stats_db) as conn:
        for index, (run_id, items) in enumerate(snapshots.items()):
            conn.execute("INSERT INTO daily_archetype_members VALUES (?, ?, ?, ?)",
                         ("v2", "daily-a", 3, run_id))
            conn.execute("INSERT INTO final_compositions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         ("v2", run_id, f"detail-event-{index}", 18, "18.2", "Vanessa",
                          "Legendary", 3, "early", None, None, "2026-09-20", None,
                          None, None, None, None, json.dumps(items)))

    client = _client(monkeypatch, stats_db).web_app.app.test_client()
    response = client.get(
        "/api/routes/daily/node?version=v2&hero=Vanessa&day=3&node_id=daily-a"
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["node"]["node_id"] == "daily-a"
    assert body["node"]["global_run_count"] == 6
    assert [row["run_count"] for row in body["recommended_compositions"]] == [3, 1, 1]
    assert body["recommended_compositions"][0]["item_ids"] == ["a", "b", "x", "y"]
    assert [row["card_id"] for row in body["associated_cards"][:3]] == ["x", "y", "z"]
    assert "run_id" not in json.dumps(body)


def test_public_daily_node_detail_cache_uses_resolved_version_for_latest(monkeypatch, stats_db):
    client = _client(monkeypatch, stats_db)
    web_app = client.web_app
    raw_client = web_app.app.test_client()
    url = "/api/routes/daily/node?version=latest&hero=Vanessa&day=3&node_id=daily-a"

    first = raw_client.get(url)
    assert first.status_code == 200
    assert first.get_json()["version_id"] == "v2"
    expected_key = web_app._day_stats_result_cache_key(
        "v2", "daily-node-v1", "Vanessa", 3, "daily-a"
    )
    assert web_app._day_stats_cache_get(expected_key) == first.get_json()

    monkeypatch.setattr(
        web_app, "_day_stats_connection",
        lambda: (_ for _ in ()).throw(AssertionError("cache hit must avoid sqlite")),
    )
    second = raw_client.get(url)
    assert second.status_code == 200
    assert second.get_json() == first.get_json()


@pytest.mark.parametrize("paths,error", [
    ([[{"day": 3, "node_id": "daily-a"}]] * 65, "paths may contain at most 64 paths"),
    ([[{"day": day, "node_id": "daily-a"} for day in range(1, 18)]],
     "each path may contain at most 16 nodes"),
    ([[{"day": 3, "node_id": "daily-a"}] * 5 for _ in range(52)],
     "paths may contain at most 256 total nodes"),
])
def test_public_daily_path_expansion_rejects_cost_limits_before_database(
        monkeypatch, stats_db, paths, error):
    client = _client(monkeypatch, stats_db)
    monkeypatch.setattr(
        client.web_app, "_day_stats_connection",
        lambda: (_ for _ in ()).throw(AssertionError("oversized input must be rejected before sqlite")),
    )

    response = client.web_app.app.test_client().post("/api/routes/daily/expand", json={
        "version": "v2", "hero": "Vanessa", "paths": paths,
    })

    assert response.status_code == 400
    assert response.get_json() == {"error": error}


def test_public_daily_path_expansion_normalizes_and_deduplicates_paths(monkeypatch, stats_db):
    with sqlite3.connect(stats_db) as conn:
        conn.executemany("INSERT INTO daily_archetype_members VALUES (?, ?, ?, ?)", [
            ("v2", "daily-a", 3, "dedup-run"),
            ("v2", "daily-b", 4, "dedup-run"),
        ])

    client = _client(monkeypatch, stats_db)
    web_app = client.web_app
    original_member_runs = web_app._daily_route_member_runs
    calls = 0

    def counted_member_runs(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original_member_runs(*args, **kwargs)

    monkeypatch.setattr(web_app, "_daily_route_member_runs", counted_member_runs)
    response = web_app.app.test_client().post("/api/routes/daily/expand", json={
        "version": "v2", "hero": "Vanessa",
        "paths": [
            [{"day": 3, "node_id": " daily-a "}, {"day": 4, "node_id": "daily-b"}],
            [{"day": 3, "node_id": "daily-a"}, {"day": 4, "node_id": "daily-b"}],
        ],
    })

    assert response.status_code == 200
    assert response.get_json()["current_node_id"] == "daily-b"
    assert response.get_json()["path_run_count"] == 1
    # Expansion loads all distinct node memberships in one batch rather than
    # issuing one membership query for every node in every path.
    assert calls == 0


def test_public_daily_path_expansion_rejects_mixed_origins(monkeypatch, stats_db):
    client = _client(monkeypatch, stats_db).web_app.app.test_client()
    response = client.post("/api/routes/daily/expand", json={
        "version": "v2", "hero": "Vanessa", "paths": [
            [{"day": 3, "node_id": "daily-a"}, {"day": 4, "node_id": "daily-b"}],
            [{"day": 4, "node_id": "daily-b"}],
        ],
    })
    assert response.status_code == 400
    assert response.get_json() == {"error": "all paths must start at the same node"}


def test_public_daily_path_expansion_unions_converged_paths_and_returns_all_qualified_branches(
        monkeypatch, stats_db):
    with sqlite3.connect(stats_db) as conn:
        conn.execute("INSERT INTO daily_archetype_nodes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     ("v2", 18, "18.2", "Vanessa", 2, "daily-root", 1, 12, 20, .6,
                      '["root"]', 12, 1.0, '["root"]'))
        conn.execute("INSERT INTO daily_archetype_nodes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     ("v2", 18, "18.2", "Vanessa", 3, "daily-x", 2, 6, 20, .3,
                      '[\"x\"]', 6, 1.0, '[\"x\"]'))
        for child_index in range(4):
            child_id = f"daily-c{child_index}"
            core = f"c{child_index}"
            conn.execute("INSERT INTO daily_archetype_nodes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         ("v2", 18, "18.2", "Vanessa", 5, child_id, child_index + 1,
                          3, 12, .25, json.dumps([core]), 3, 1.0, json.dumps([core])))
        for number in range(12):
            run_id = f"expand-{number}"
            start_node = "daily-a" if number < 6 else "daily-x"
            child_id = f"daily-c{number // 3}"
            conn.executemany("INSERT INTO daily_archetype_members VALUES (?, ?, ?, ?)", [
                ("v2", "daily-root", 2, run_id),
                ("v2", start_node, 3, run_id),
                ("v2", "daily-b", 4, run_id),
                ("v2", child_id, 5, run_id),
            ])

    client = _client(monkeypatch, stats_db).web_app.app.test_client()
    response = client.post("/api/routes/daily/expand", json={
        "version": "v2",
        "hero": "Vanessa",
        "paths": [
            [{"day": 2, "node_id": "daily-root"}, {"day": 3, "node_id": "daily-a"}, {"day": 4, "node_id": "daily-b"}],
            [{"day": 2, "node_id": "daily-root"}, {"day": 3, "node_id": "daily-x"}, {"day": 4, "node_id": "daily-b"}],
            [{"day": 2, "node_id": "daily-root"}, {"day": 3, "node_id": "daily-a"}, {"day": 4, "node_id": "daily-b"}],
        ],
    })

    assert response.status_code == 200
    body = response.get_json()
    assert body["current_node_id"] == "daily-b"
    assert body["current_day"] == 4
    assert body["path_run_count"] == 12
    assert body["path_observable_runs"] == 12
    assert body["display_threshold"] == 3
    assert len(body["branches"]) == 4
    assert [row["path_run_count"] for row in body["branches"]] == [3, 3, 3, 3]
    assert body["visible_coverage_rate"] == 1.0
    assert body["hidden_run_count"] == 0
    assert "run_id" not in json.dumps(body)


def test_public_routes_page_and_api_do_not_require_basic_auth(monkeypatch, stats_db):
    client = _client(monkeypatch, stats_db)
    raw_client = client.web_app.app.test_client()

    page = raw_client.get("/routes")
    latest = raw_client.get("/api/routes/latest")
    cores = raw_client.get("/api/routes/cores?version=latest&hero=Vanessa&day=1")
    route = raw_client.get("/api/routes/cores/core-1?version=v2")

    assert page.status_code == 200
    assert b"routes.js" in page.data
    assert latest.status_code == 200
    assert cores.status_code == 200
    assert route.status_code == 200
    assert latest.get_json()["version_id"] == "v2"
    assert cores.get_json()["cores"][0]["core_id"] == "core-1"
    assert route.get_json()["edges"][0]["transition_rate"] == .6


def test_public_routes_daily_query_is_recorded_as_feature_usage(monkeypatch, stats_db):
    client = _client(monkeypatch, stats_db)
    web_app = client.web_app
    web_app._event_rate.clear()
    events = []
    monkeypatch.setattr(web_app, "_track_feature", lambda feature, page, outcome: events.append((feature, page, outcome)))

    test_client = web_app.app.test_client()
    summary = test_client.get("/api/routes/daily/summary?version=v2&hero=Vanessa")
    response = test_client.get("/api/routes/daily?version=v2&hero=Vanessa&day=3")

    assert summary.status_code == 200
    assert response.status_code == 200
    assert ("routes_summary", "routes", "success") in events
    assert ("routes_query", "routes", "success") in events


def test_public_routes_daily_empty_result_is_recorded_as_empty(monkeypatch, stats_db):
    client = _client(monkeypatch, stats_db)
    web_app = client.web_app
    web_app._event_rate.clear()
    events = []
    monkeypatch.setattr(web_app, "_track_feature", lambda feature, page, outcome: events.append((feature, page, outcome)))

    response = web_app.app.test_client().get("/api/routes/daily?version=v2&hero=Mak&day=3")

    assert response.status_code == 200
    assert ("routes_query", "routes", "empty") in events


def test_routes_page_contains_page_view_tracking_contract(monkeypatch, stats_db):
    client = _client(monkeypatch, stats_db)
    page = client.web_app.app.test_client().get("/routes")

    assert page.status_code == 200
    assert b"api/track/pv" in page.data
    assert b"routes" in page.data


@pytest.mark.parametrize("url", [
    "/api/routes/cores?hero=Vanessa&day=1",
    "/api/routes/cores/core-1",
])
def test_public_routes_requires_version(monkeypatch, stats_db, url):
    client = _client(monkeypatch, stats_db)
    response = client.web_app.app.test_client().get(url)
    assert response.status_code == 400
    assert response.get_json()["error"] == "version is required"


@pytest.mark.parametrize("url", [
    "/api/routes/cores?version=bad%20version&hero=Vanessa&day=1",
    "/api/routes/cores?version=v2&hero=UnknownHero&day=1",
    "/api/routes/cores?version=v2&hero=%3Cscript%3E&day=1",
    "/api/routes/cores?version=v2&hero=Vanessa&day=4",
    "/api/routes/cores/bad%20core?version=v2",
])
def test_public_routes_rejects_invalid_inputs(monkeypatch, stats_db, url):
    client = _client(monkeypatch, stats_db)
    assert client.web_app.app.test_client().get(url).status_code == 400


def test_public_routes_rate_limit_bounds_repeated_requests(monkeypatch, stats_db):
    client = _client(monkeypatch, stats_db)
    raw_client = client.web_app.app.test_client()
    client.web_app._routes_rate_limit.clear()
    responses = [raw_client.get('/api/routes/latest', headers={'X-Forwarded-For': '203.0.113.9'}) for _ in range(31)]
    assert all(response.status_code == 200 for response in responses[:30])
    assert responses[-1].status_code == 429


def test_public_routes_responses_are_short_public_cacheable(monkeypatch, stats_db):
    client = _client(monkeypatch, stats_db)
    raw_client = client.web_app.app.test_client()
    responses = [
        raw_client.get("/api/routes/latest"),
        raw_client.get("/api/routes/cores?version=v2&hero=Vanessa&day=1"),
        raw_client.get("/api/routes/cores/core-1?version=v2"),
    ]
    assert all(response.status_code == 200 for response in responses)
    assert all(response.cache_control.public for response in responses)
    assert all(response.cache_control.max_age == 60 for response in responses)
    assert all(not response.cache_control.no_store for response in responses)


def test_public_routes_page_contract_and_navigation(monkeypatch, stats_db):
    client = _client(monkeypatch, stats_db)
    raw_client = client.web_app.app.test_client()
    html = raw_client.get("/routes").get_data(as_text=True)
    js = raw_client.get("/static/routes.js").get_data(as_text=True)
    css = raw_client.get("/static/routes.css").get_data(as_text=True)

    assert '<meta name="robots" content="index, follow">' in html
    assert 'href="static/routes.css?v=20260921c"' in html
    assert 'src="static/routes.js?v=20260921c"' in html
    assert "fetch('api/track/pv'" in html
    assert 'href="/routes" class="nav-tab active"' in html
    assert "可拖拽阵容路线画布" in html and "严格相邻 Day" in html
    assert "/api/routes/daily/expand" in js and "/api/routes/daily/node" in js
    assert "function apiPath(path)" in js
    assert "location.pathname" in js
    assert "paths:requestPaths" in js
    assert "state.nodes.has" in js and "state.paths.set" in js
    assert "pointerdown" in js and "wheel" in js and "fitView" in js
    assert "推荐阵容 Top 3" in js and "关联牌 Top 8" in js
    assert "查询相似真实阵容" not in js and "/runs?" not in js
    assert "canvas-viewport" in css and "node-detail" in css
    assert "object-fit:contain" in css.replace(" ", "")
    assert "@media(max-width:650px)" in css.replace(" ", "")


def test_route_v2_cache_namespace_is_v5():
    from pathlib import Path
    source = (Path(__file__).parents[1] / "web_runs" / "app.py").read_text(encoding="utf-8")
    assert "'daily-v5'" in source
    assert "'daily-summary-v5'" in source
    assert "'daily-v4'" not in source
    assert "'daily-summary-v4'" not in source


def test_app_prefers_its_own_directory_for_sibling_modules():
    from pathlib import Path
    source = (Path(__file__).parents[1] / "web_runs" / "app.py").read_text(encoding="utf-8")
    assert "_APP_DIR = os.path.dirname(os.path.abspath(__file__))" in source
    assert "_sys.path.insert(0, _APP_DIR)" in source


def test_public_navigation_includes_routes_without_removing_existing_items():
    from pathlib import Path
    static = Path(__file__).parents[1] / "web_runs" / "static"
    pages = ["runs.html", "winrate.html", "partner.html", "topcard.html", "trivia.html", "feedback.html", "support.html"]
    for name in pages:
        html = (static / name).read_text(encoding="utf-8")
        assert ('<a href="/routes" class="nav-tab">阵容路线</a>' in html
                or '<a class="nav-tab" href="/routes">阵容路线</a>' in html)
        for href in ("/runs", "/winrate", "/partner", "/topcard", "/trivia"):
            assert f'href="{href}"' in html


def test_runs_page_restores_route_link_filters_and_can_auto_query():
    from pathlib import Path
    html = (Path(__file__).parents[1] / "web_runs" / "static" / "runs.html").read_text(encoding="utf-8")
    assert "new URLSearchParams(location.search)" in html
    assert "params.get('hero')" in html
    assert "params.get('cards')" in html
    assert "params.get('auto') === '1'" in html
    assert "queryRuns(1)" in html


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
