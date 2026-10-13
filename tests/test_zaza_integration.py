"""Exercise the production route functions in an isolated Flask app and SQLite store."""
import ast
import hmac
import json
import os
import pathlib
import sqlite3
from functools import wraps

import pytest
from flask import Flask, Response, request, send_from_directory

from web_runs.zaza_space import ZazaSpaceStore, create_zaza_blueprint

ROOT = pathlib.Path(__file__).resolve().parents[1]
PROFILE = "12345678-1234-1234-1234-123456789abc"
URL = "https://bazaardb.gg/run/profile/" + PROFILE


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("STATS_PASSWORD", "test-password")
    app = Flask("zaza_isolated", static_folder=str(ROOT / "web_runs" / "static"))
    app.testing = True
    namespace = {"app": app, "request": request, "os": os, "hmac": hmac, "wraps": wraps,
                 "Response": Response,
                 "send_from_directory": lambda directory, filename: send_from_directory(ROOT / "web_runs" / directory, filename)}
    tree = ast.parse((ROOT / "web_runs" / "app.py").read_text(encoding="utf-8"))
    selected = {"_stats_authorized", "require_stats_auth", "zaza_page", "zaza_admin_page"}
    functions: list[ast.stmt] = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in selected]
    assert {node.name for node in functions if isinstance(node, ast.FunctionDef)} == selected
    exec(compile(ast.Module(body=functions, type_ignores=[]), "production_zaza_routes", "exec"), namespace)
    runs = tmp_path / "runs.db"
    with sqlite3.connect(runs) as conn:
        conn.execute("CREATE TABLE runs(id TEXT, hero TEXT, created_at TEXT, stat_wins INTEGER, stat_losses INTEGER, season INTEGER, phase TEXT, items_json TEXT, profileId TEXT, raw_json TEXT, skills_json TEXT)")
        for rid, cards, wins in (("first", ["a", "b"], 0), ("second", ["a"], 10)):
            conn.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?,?,?)", (rid, "Vanessa", "2026-10-09T12:00:00Z", wins, 3, 19, "19.3", json.dumps([{"cardId": c} for c in cards]), PROFILE, json.dumps({'profileId': PROFILE}), "[]"))
    app.register_blueprint(create_zaza_blueprint(runs_path=runs, space_path=tmp_path / "spaces.db",
                           snapshots_path=tmp_path / "snapshots.db", season=19, phase="19.3",
                           card_resolver=lambda cid: {"name": "Resolved " + cid, "img": "/art.svg", "size": "Medium"},
                           admin_authorized=namespace["_stats_authorized"]))
    return app.test_client(), ZazaSpaceStore(tmp_path / "spaces.db")


def test_actual_public_and_admin_page_routes_use_existing_auth(client):
    http, _ = client
    assert http.get("/zaza").status_code == 200
    assert b"zaza.js" in http.get("/zaza").data
    unauthorized = http.get("/admin/zaza")
    assert unauthorized.status_code == 401
    assert "Basic" in unauthorized.headers["WWW-Authenticate"]
    assert http.get("/admin/zaza", auth=("admin", "wrong")).status_code == 401
    authorized = http.get("/admin/zaza", auth=("admin", "test-password"))
    assert authorized.status_code == 200 and b"zaza-admin.js" in authorized.data
    for endpoint in ("/api/admin/zaza/spaces", "/api/admin/zaza/settings"):
        assert http.get(endpoint).status_code == 401
        assert http.get(endpoint, auth=("admin", "test-password")).status_code == 200


def test_create_search_fixed_id_rename_and_real_filtered_detail(client):
    http, store = client
    created = http.post("/api/zaza/spaces", json={"profileUrl": URL, "name": "测试空间"})
    assert created.status_code == 201
    space = created.json["space"]
    assert space["status"] == "pending"
    assert http.get("/api/zaza/spaces?query=测试").json["spaces"][0]["id"] == space["id"]
    endpoint = "/api/zaza/spaces/" + space["id"]
    assert http.get(endpoint).json["summary"]["run_count"] == 0
    assert http.put("/api/admin/zaza/spaces/" + space["id"], json={"action": "approve"}).status_code == 401
    store.transition(space["id"], "approve")
    store.mark_collection(space["id"], season=19, phase="19.3", run_ids=["first", "second"], profile={"nickname": "Real Nickname"})
    renamed = http.put("/api/admin/zaza/spaces/" + space["id"], auth=("admin", "test-password"), json={"action": "rename", "name": "新名字空间"})
    assert renamed.status_code == 200 and renamed.json["space"]["id"] == space["id"]
    detail = http.get(endpoint + "?cards=a,b&hero=Vanessa&start=2026-10-09&end=2026-10-10&page=1&perPage=20").json
    assert detail["space"]["name"] == "新名字空间"
    assert detail["profile"]["nickname"] == "Real Nickname"
    assert detail["profile"]["url"] == URL
    assert detail["summary"]["run_count"] == 2
    assert detail["summary"]["average_wins"] == 5
    assert detail["summary"]["ten_win_rate"] == .5
    assert detail["pagination"]["total"] == 1
    assert detail["runs"][0]["wins"] == 0
    assert detail["runs"][0]["items"][0]["name"] == "Resolved a"
    assert http.get(endpoint + "?page=2&perPage=1").json["pagination"]["page"] == 2


def test_main_navigation_keeps_native_zaza_entry():
    for name in ("index.html", "runs.html", "routes.html", "topcard.html", "support.html"):
        html = (ROOT / "web_runs" / "static" / name).read_text(encoding="utf-8")
        assert ('/zaza' in html or '扎扎空间' in html)
