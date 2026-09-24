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
            CREATE TABLE final_composition_cards (
                version_id TEXT NOT NULL, run_id TEXT NOT NULL, day INTEGER NOT NULL,
                event_id TEXT NOT NULL, card_id TEXT NOT NULL, card_kind TEXT NOT NULL,
                tier TEXT, enchantment TEXT, slot_position INTEGER NOT NULL,
                PRIMARY KEY (version_id, run_id, day, card_id, card_kind, slot_position)
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
        conn.executemany("INSERT INTO daily_archetype_cards VALUES (?, ?, ?, ?, ?, ?, ?)", [
            ("v2", "daily-dispersed", 4, "z", "common", 3, .75),
            ("v2", "daily-dispersed", 4, "y", "common", 2, .50),
            ("v2", "daily-dispersed", 4, "x", "variant", 1, .25),
        ])
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

        def post(self, *args, **kwargs):
            kwargs.setdefault("headers", {}).update(_auth())
            return raw_client.post(*args, **kwargs)
    return AuthClient(web_app)


def _auth():
    import base64
    return {"Authorization": "Basic " + base64.b64encode(b"tester:test-password").decode()}



def test_node_share_create_and_read_is_version_pinned(monkeypatch, stats_db):
    client = _client(monkeypatch, stats_db)
    created = client.post("/api/routes/share", json={
        "share_type": "node", "version": "v2", "hero": "Vanessa", "day": 3, "node_id": "daily-a"
    })
    assert created.status_code == 201
    result = created.get_json()
    assert result["share_type"] == "node"
    assert result["version_id"] == "v2"
    detail = client.get("/api/routes/share/" + result["share_id"])
    assert detail.status_code == 200
    data = detail.get_json()
    assert data["version_id"] == "v2"
    assert data["hero"] == "Vanessa"
    assert data["node_id"] == "daily-a"
    assert "run_id" not in detail.get_data(as_text=True)
    with sqlite3.connect(stats_db.parent / "stats.db") as conn:
        assert conn.execute("SELECT open_count FROM route_shares WHERE share_id=?", (result["share_id"],)).fetchone()[0] == 1


def test_node_share_rejects_unknown_node(monkeypatch, stats_db):
    client = _client(monkeypatch, stats_db)
    response = client.post("/api/routes/share", json={
        "share_type": "node", "version": "v2", "hero": "Vanessa", "day": 3, "node_id": "missing"
    })
    assert response.status_code == 404


def test_route_share_preserves_paths_and_tracks_expand(monkeypatch, stats_db):
    client = _client(monkeypatch, stats_db)
    paths = [[{"day": 3, "node_id": "daily-a"}, {"day": 4, "node_id": "daily-b"}]]
    created = client.post("/api/routes/share", json={
        "share_type": "route", "version": "v2", "hero": "Vanessa",
        "start_day": 3, "node_id": "daily-a", "paths": paths,
    })
    assert created.status_code == 201
    share = created.get_json()
    assert share["share_type"] == "route"
    read = client.get("/api/routes/share/" + share["share_id"])
    assert read.status_code == 200
    body = read.get_json()
    assert body["version_id"] == "v2"
    assert body["paths"] == paths
    assert "run_id" not in read.get_data(as_text=True)
    event = client.post("/api/routes/share/" + share["share_id"] + "/event",
                        json={"event_type": "route_expanded"})
    assert event.status_code == 201
    with sqlite3.connect(stats_db.parent / "stats.db") as conn:
        row = conn.execute("SELECT open_count, expand_count FROM route_shares WHERE share_id=?",
                           (share["share_id"],)).fetchone()
        assert row == (1, 1)
        assert conn.execute("SELECT COUNT(*) FROM route_share_events WHERE share_id=?",
                            (share["share_id"],)).fetchone()[0] == 1


def test_route_share_rejects_unknown_path_node(monkeypatch, stats_db):
    client = _client(monkeypatch, stats_db)
    response = client.post("/api/routes/share", json={
        "share_type": "route", "version": "v2", "hero": "Vanessa", "start_day": 3,
        "paths": [[{"day": 3, "node_id": "daily-a"}, {"day": 4, "node_id": "missing"}]],
    })
    assert response.status_code == 404


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
    assert "run_id" not in json.dumps(body)
    assert directions[0]["associated_cards"][0]["card_id"] == "x"
    assert directions[0]["associated_cards"][0]["support_rate"] == pytest.approx(5 / 6)
    assert len(directions) <= 3
    assert [edge["child_node_id"] for edge in body["edges"]] == [
        "daily-b", "daily-dispersed",
    ]


def test_public_daily_routes_keeps_empty_core_nodes_in_summary(monkeypatch, stats_db):
    client = _client(monkeypatch, stats_db)
    response = client.web_app.app.test_client().get(
        "/api/routes/daily/summary?version=v2&hero=Vanessa"
    )
    assert response.status_code == 200
    assert "daily-dispersed" in [row["node_id"] for row in response.get_json()["nodes"]]


def test_empty_core_node_returns_dynamic_signature_cards_without_changing_representative_cards(
        monkeypatch, stats_db):
    raw_client = _client(monkeypatch, stats_db).web_app.app.test_client()
    daily = raw_client.get("/api/routes/daily?version=v2&hero=Vanessa&day=4")
    detail = raw_client.get(
        "/api/routes/daily/node?version=v2&hero=Vanessa&day=4&node_id=daily-dispersed"
    )

    assert daily.status_code == 200
    node = next(row for row in daily.get_json()["nodes"] if row["node_id"] == "daily-dispersed")
    assert [row["cardId"] for row in node["representative_cards"]] == ["z"]
    assert [row["card_id"] for row in node["signature_cards"]] == ["z", "y"]
    assert node["signature_cards"][0]["support_runs"] == 3
    assert node["signature_cards"][0]["support_rate"] == .75
    assert node["signature_cards"][0]["display"]["name"] == "中文-z"

    assert detail.status_code == 200
    detail_node = detail.get_json()["node"]
    assert [row["card_id"] for row in detail_node["signature_cards"]] == ["z", "y"]


def test_public_daily_routes_summary_groups_all_days(monkeypatch, stats_db):
    client = _client(monkeypatch, stats_db)
    response = client.web_app.app.test_client().get(
        "/api/routes/daily/summary?version=v2&hero=Vanessa"
    )
    assert response.status_code == 200
    assert [row["day"] for row in response.get_json()["nodes"]] == [3, 4, 4]


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
        conn.executemany(
            "INSERT INTO final_composition_cards VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("v2", run_id, 3, f"detail-event-{index}", skill, "skill", None, None, -1)
                for index, run_id in enumerate(snapshots)
                for skill in (["skill-a", "skill-b"] if index < 3 else (["skill-a"] if index < 5 else ["skill-c"]))
            ],
        )
        conn.executemany(
            "INSERT INTO final_composition_cards VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("v2", "detail-1", 3, "detail-event-0", "a", "item", None, None, 4),
                ("v2", "detail-1", 3, "detail-event-0", "b", "item", None, None, 1),
                ("v2", "detail-1", 3, "detail-event-0", "x", "item", None, None, 7),
                ("v2", "detail-1", 3, "detail-event-0", "y", "item", None, None, 2),
            ],
        )

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
    assert [row["cardId"] for row in body["recommended_compositions"][0]["cards"]] == ["b", "y", "a", "x"]
    assert [row["slot_position"] for row in body["recommended_compositions"][0]["cards"]] == [1, 2, 4, 7]
    assert [row["card_id"] for row in body["associated_cards"][:3]] == ["x", "y", "z"]
    assert [row["card_id"] for row in body["associated_skills"][:2]] == ["skill-a", "skill-b"]
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
        "v2", "daily-node-v4", "Vanessa", 3, "daily-a"
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


def test_public_daily_path_expansion_uses_each_runs_next_observed_day_and_reports_gaps(
        monkeypatch, stats_db):
    with sqlite3.connect(stats_db) as conn:
        for day, node_id, core in ((6, "next-six", ["six"]), (7, "next-seven", []),
                                   (8, "not-next", ["late"])):
            conn.execute("INSERT INTO daily_archetype_nodes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         ("v2", 18, "18.2", "Vanessa", day, node_id, 1, 3, 6, .5,
                          json.dumps(core), 0 if not core else 3, 0.0 if not core else 1.0,
                          json.dumps(core)))
        for number in range(6):
            run_id = f"gap-{number}"
            target_day = 6 if number < 3 else 7
            target_node = "next-six" if number < 3 else "next-seven"
            conn.executemany("INSERT INTO daily_archetype_members VALUES (?, ?, ?, ?)", [
                ("v2", "daily-a", 3, run_id),
                ("v2", "daily-b", 4, run_id),
                ("v2", target_node, target_day, run_id),
                ("v2", "not-next", 8, run_id),
            ])
            for day, items in ((3, ["a"]), (4, ["b"]), (target_day, [target_node]),
                               (8, ["late"])):
                conn.execute("INSERT INTO final_compositions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                             ("v2", run_id, f"{run_id}-{day}", 18, "18.2", "Vanessa",
                              "Legendary", day, "middle", None, None, "2026-09-20", None,
                              None, None, None, None, json.dumps(items)))

    response = _client(monkeypatch, stats_db).web_app.app.test_client().post(
        "/api/routes/daily/expand", json={
            "version": "v2", "hero": "Vanessa", "paths": [[
                {"day": 3, "node_id": "daily-a"},
                {"day": 4, "node_id": "daily-b"},
            ]],
        })

    assert response.status_code == 200
    body = response.get_json()
    assert body["path_observable_runs"] == 6
    assert [(row["target_day"], row["path_run_count"], row["day_gap"],
             row["skipped_days"], row["is_gap"])
            for row in body["branches"]] == [
        (6, 3, 2, 1, True),
        (7, 3, 3, 2, True),
    ]
    assert body["branches"][1]["node"]["core_items"] == []
    assert all(row["node_id"] != "not-next" for row in body["branches"])
    assert "run_id" not in json.dumps(body)


def test_public_daily_path_expansion_returns_branch_path_indexes_for_partial_convergence(
        monkeypatch, stats_db):
    with sqlite3.connect(stats_db) as conn:
        for day, node_id, core in (
            (2, "partial-root", ["root"]),
            (3, "partial-left", ["left"]),
            (3, "partial-right", ["right"]),
            (4, "partial-merge", ["merge"]),
            (5, "partial-child", ["child"]),
        ):
            conn.execute("INSERT INTO daily_archetype_nodes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         ("v2", 18, "18.2", "Vanessa", day, node_id, 1, 3, 3, 1.0,
                          json.dumps(core), 3, 1.0, json.dumps(core)))
        member_rows = []
        for side in ("left", "right"):
            for number in range(3):
                run_id = f"partial-{side}-{number}"
                member_rows.extend([
                    ("v2", "partial-root", 2, run_id),
                    ("v2", f"partial-{side}", 3, run_id),
                    ("v2", "partial-merge", 4, run_id),
                ])
                if side == "left":
                    member_rows.append(("v2", "partial-child", 5, run_id))
        for number in range(3):
            run_id = f"partial-outsider-{number}"
            member_rows.extend([
                ("v2", "partial-merge", 4, run_id),
                ("v2", "partial-child-2", 5, run_id),
            ])
        conn.execute("INSERT INTO daily_archetype_nodes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     ("v2", 18, "18.2", "Vanessa", 5, "partial-child-2", 2, 3, 6, .5,
                      json.dumps(["child", "two"]), 3, 1.0, json.dumps(["child", "two"])))
        conn.executemany("INSERT INTO daily_archetype_members VALUES (?, ?, ?, ?)", member_rows)
    response = _client(monkeypatch, stats_db).web_app.app.test_client().post(
        "/api/routes/daily/expand", json={
            "version": "v2", "hero": "Vanessa", "paths": [
                [{"day": 2, "node_id": "partial-root"}, {"day": 3, "node_id": "partial-left"},
                 {"day": 4, "node_id": "partial-merge"}],
                [{"day": 2, "node_id": "partial-root"}, {"day": 3, "node_id": "partial-right"},
                 {"day": 4, "node_id": "partial-merge"}],
            ],
        })
    assert response.status_code == 200
    body = response.get_json()
    assert body["branches"][0]["path_indexes"] == [0]
    assert body["branches"][0]["path_run_count"] == 3
    assert any(
        row["node_id"] == "partial-child-2"
        and row["path_run_count"] == 3
        and row["path_indexes"] == []
        for row in body["branches"]
    )
    assert "run_id" not in json.dumps(body)


def test_public_daily_path_expansion_rejects_non_next_observed_transition(monkeypatch, stats_db):
    with sqlite3.connect(stats_db) as conn:
        conn.execute("INSERT INTO daily_archetype_nodes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     ("v2", 18, "18.2", "Vanessa", 5, "skip-target", 1, 1, 1, 1.0,
                      '["target"]', 1, 1.0, '["target"]'))
        conn.executemany("INSERT INTO daily_archetype_members VALUES (?, ?, ?, ?)", [
            ("v2", "daily-a", 3, "invalid-skip"),
            ("v2", "daily-dispersed", 4, "invalid-skip"),
            ("v2", "skip-target", 5, "invalid-skip"),
        ])
        for day in (3, 4, 5):
            conn.execute("INSERT INTO final_compositions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         ("v2", "invalid-skip", f"invalid-skip-{day}", 18, "18.2", "Vanessa",
                          "Legendary", day, "middle", None, None, "2026-09-20", None,
                          None, None, None, None, "[]"))

    response = _client(monkeypatch, stats_db).web_app.app.test_client().post(
        "/api/routes/daily/expand", json={
            "version": "v2", "hero": "Vanessa", "paths": [[
                {"day": 3, "node_id": "daily-a"},
                {"day": 5, "node_id": "skip-target"},
            ]],
        })

    assert response.status_code == 400
    assert response.get_json() == {"error": "path transition is not a next observed Day"}


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
    assert 'href="static/routes.css?v=20260924-node-share"' in html
    assert 'src="static/routes.js?v=20260924-node-share"' in html
    assert "fetch('api/track/pv'" in html
    assert 'href="/routes" class="nav-tab active"' in html
    assert "可拖拽阵容路线画布" in html and "下一次真实观测 Day" in html
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


def test_route_v2_cache_namespace_is_v9():
    from pathlib import Path
    source = (Path(__file__).parents[1] / "web_runs" / "app.py").read_text(encoding="utf-8")
    assert "'daily-v9'" in source
    assert "'daily-summary-v7'" in source
    assert "'daily-v8'" not in source
    assert "'daily-summary-v6'" not in source


def test_daily_routes_accept_observed_days_beyond_day_16(monkeypatch, stats_db):
    with sqlite3.connect(stats_db) as conn:
        conn.execute(
            "INSERT INTO daily_archetype_nodes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("v2", 18, "18.2", "Vanessa", 19, "day-nineteen", 1, 1, 1, 1.0,
             '[\"late\"]', 1, 1.0, '[\"late\"]'),
        )
        conn.execute(
            "INSERT INTO daily_archetype_members VALUES (?, ?, ?, ?)",
            ("v2", "day-nineteen", 19, "late-run"),
        )
        conn.execute(
            "INSERT INTO final_compositions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("v2", "late-run", "late-run-19", 18, "18.2", "Vanessa",
             "Legendary", 19, "very-late", None, None, "2026-09-20", None,
             None, None, None, None, '[\"late\"]'),
        )

    raw_client = _client(monkeypatch, stats_db).web_app.app.test_client()
    daily = raw_client.get("/api/routes/daily?version=v2&hero=Vanessa&day=19")
    detail = raw_client.get(
        "/api/routes/daily/node?version=v2&hero=Vanessa&day=19&node_id=day-nineteen"
    )

    assert daily.status_code == 200
    assert daily.get_json()["nodes"][0]["node_id"] == "day-nineteen"
    assert detail.status_code == 200
    assert detail.get_json()["node"]["node_id"] == "day-nineteen"


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
