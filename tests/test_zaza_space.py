import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flask import Flask


def _fixture(tmp_path):
    db = tmp_path / "runs.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE runs (id TEXT, hero TEXT, created_at TEXT, stat_wins INTEGER, stat_losses INTEGER, season INTEGER, phase TEXT, items_json TEXT, profileId TEXT)")
        conn.executemany("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?)", [
            ("r-new", "Dooley", "2026-10-02T12:00:00Z", 8, 2, 18, "18.2", json.dumps([{"cardId":"a"},{"cardId":"b"}]), "2d4bdb0f-f103-4b1c-9ef7-df15c73860d3"),
            ("r-old", "Dooley", "2026-10-01T12:00:00Z", 10, 1, 18, "18.2", json.dumps([{"cardId":"c"}]), "2d4bdb0f-f103-4b1c-9ef7-df15c73860d3"),
            ("r-other-phase", "Dooley", "2026-09-01T12:00:00Z", 10, 0, 18, "18.1", json.dumps([]), "2d4bdb0f-f103-4b1c-9ef7-df15c73860d3"),
        ])
    artifact = tmp_path / "zaza.json"
    artifact.write_text(json.dumps({"season": 18, "phase": "18.2", "username": "pUfFy", "manifest": {"profileId": "2d4bdb0f-f103-4b1c-9ef7-df15c73860d3"}, "runs": [
        {"id":"r-new", "hero":"Dooley", "created_at":"2026-10-02T12:00:00Z", "stat_wins":8, "stat_losses":2,
         "items":[{"cardId":"a"},{"cardId":"b"}], "snapshots":[
             {"eventId":"secret-e1", "day":1, "hour":2, "playerBoard":[{"baseId":"a","title":"A"}], "playerSkills":[{"title":"Skill"}], "combat":{"outcome":"Win"}},
             {"eventId":"secret-e2", "day":1, "hour":6, "playerBoard":[{"baseId":"a","title":"A"},{"baseId":"b","title":"B"}], "playerSkills":[], "combat":{"outcome":"Loss", "is_pvp":True}},
             {"eventId":"secret-e3", "day":3, "hour":6, "playerBoard":[{"baseId":"c","title":"C"}], "playerSkills":[], "combat":{"outcome":"Win", "is_pvp":True}},
         ]},
        {"id":"r-old", "hero":"Dooley", "created_at":"2026-10-01T12:00:00Z", "stat_wins":10, "stat_losses":1,
         "items":[{"cardId":"c"}], "snapshots":[
             {"eventId":"secret-e4", "day":1, "hour":6, "playerBoard":[{"baseId":"a","title":"A"}], "playerSkills":[], "combat":{"outcome":"Win", "is_pvp":True}},
         ]},
    ]}), encoding="utf-8")
    return db, artifact


def _client(tmp_path, resolver=None):
    from web_runs.zaza_space import create_zaza_blueprint
    db, artifact = _fixture(tmp_path)
    app = Flask(__name__)
    app.register_blueprint(create_zaza_blueprint(db, artifact, 18, "18.2", resolver))
    return app.test_client()


def test_public_whitelist_sorting_opaque_keys_and_all_wins(tmp_path):
    client = _client(tmp_path)
    assert client.get("/api/zaza/users").json == {"users": ["Puffy"]}
    assert client.get("/api/zaza/users?username=other").status_code == 404
    response = client.get("/api/zaza/runs?username=PUFFY")
    assert response.status_code == 200
    body = response.json
    assert body["username"] == "Puffy"
    assert [row["wins"] for row in body["runs"]] == [8, 10]
    assert body["runs"][0]["run_key"] != "r-new"
    assert "profileId" not in json.dumps(body)
    assert "raw_json" not in json.dumps(body)
    assert body["runs"][0]["items"][0]["card_id"] == "a"


def test_days_hides_ids_and_selects_hour6_pvp(tmp_path):
    client = _client(tmp_path)
    run_key = client.get("/api/zaza/runs?username=puffy").json["runs"][0]["run_key"]
    body = client.get(f"/api/zaza/runs/{run_key}/days").json
    assert body["run_key"] == run_key
    assert [day["day"] for day in body["days"]] == [1, 3]
    assert body["days"][0]["hour"] == 6
    serialized = json.dumps(body)
    assert "secret-e" not in serialized
    assert body["days"][0]["items"][1]["card_id"] == "b"


def test_routes_cross_run_and_gap_days(tmp_path):
    client = _client(tmp_path)
    response = client.get("/api/zaza/routes?username=PUFFY&hero=Dooley")
    assert response.status_code == 200
    body = response.json
    assert {node["day"] for node in body["nodes"]} == {1, 3}
    assert any(edge["parent_day"] == 1 and edge["child_day"] == 3 and edge["gap_days"] == 1 for edge in body["edges"])
    assert all("sample_count" in node for node in body["nodes"])
    assert client.get("/api/zaza/runs/no-such/days").status_code == 404


def test_card_resolver_and_snapshot_native_display_fields(tmp_path):
    class Resolver:
        def card_display_info_cached(self, card_id):
            return {"name": f"resolved-{card_id}", "img": f"/{card_id}.webp", "size": "Large"}

    client = _client(tmp_path, Resolver())
    run = client.get("/api/zaza/runs?username=Puffy").json["runs"][0]
    assert run["items"][0] == {
        "card_id": "a", "name": "resolved-a", "img": "/a.webp", "size": "Large",
    }
    day = client.get(f"/api/zaza/runs/{run['run_key']}/days").json["days"][0]
    assert day["items"][0]["name"] == "resolved-a"


def test_paths_may_be_resolved_lazily(tmp_path):
    from web_runs.zaza_space import create_zaza_blueprint

    db, artifact = _fixture(tmp_path)
    app = Flask(__name__)
    app.register_blueprint(create_zaza_blueprint(lambda: db, lambda: artifact, 18, "18.2"))
    response = app.test_client().get("/api/zaza/runs?username=Puffy")
    assert response.status_code == 200
    assert response.json["total"] == 2


def test_profile_phase_and_artifact_fail_closed(tmp_path):
    from web_runs.zaza_space import create_zaza_blueprint

    db, artifact = _fixture(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE runs SET profileId='other' WHERE id='r-old'")
    app = Flask(__name__)
    app.register_blueprint(create_zaza_blueprint(db, artifact, 18, "18.2"))
    client = app.test_client()
    assert client.get("/api/zaza/runs?username=Puffy").json["total"] == 1
    artifact.write_text(json.dumps({"season": 18, "phase": "18.1", "runs": []}), encoding="utf-8")
    assert client.get("/api/zaza/runs?username=Puffy").status_code == 503


def test_routes_have_real_representatives_and_no_full_board_core(tmp_path):
    client = _client(tmp_path)
    body = client.get("/api/zaza/routes?username=Puffy&hero=Dooley").json
    assert all(2 <= len(n["core_items"]) <= 4 or not n["core_items"] for n in body["nodes"])
    assert all(n["items"] for n in body["nodes"])
    assert any(e["run_count"] == 1 for e in body["edges"])
