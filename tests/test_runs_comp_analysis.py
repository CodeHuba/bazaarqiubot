import importlib.util
import json
import sqlite3
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT / "plugins" / "bazaar_plugin"

# 避免导入 plugins.bazaar_plugin.__init__ 时拉起 QQ 运行时依赖。
plugins_pkg = types.ModuleType("plugins")
plugins_pkg.__path__ = [str(ROOT / "plugins")]
bazaar_pkg = types.ModuleType("plugins.bazaar_plugin")
bazaar_pkg.__path__ = [str(PLUGIN_DIR)]
sys.modules.setdefault("plugins", plugins_pkg)
sys.modules.setdefault("plugins.bazaar_plugin", bazaar_pkg)
spec = importlib.util.spec_from_file_location("plugins.bazaar_plugin.runs_query", PLUGIN_DIR / "runs_query.py")
assert spec and spec.loader
runs_query_module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = runs_query_module
spec.loader.exec_module(runs_query_module)
RunsQuery = runs_query_module.RunsQuery


def _card(card_id):
    return {"cardId": card_id}


def _make_runs_db(path: Path, phase: str = "test-phase"):
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE runs (
            id TEXT, hero TEXT, items_json TEXT, stat_wins INTEGER,
            screenshot_url TEXT, created_at TEXT, season TEXT, phase TEXT,
            player_rank TEXT
        )"""
    )
    rows = [
        (
            f"r{i}", "Vanessa", [_card("target"), _card("core-a"), _card("core-b"), _card(f"flex-{i}")],
            10 if i < 9 else 8, f"/r{i}.webp", f"2026-01-{i:02d}", "test-season", phase, "Legendary"
        )
        for i in range(1, 13)
    ]
    rows.append(("old", "Vanessa", [_card("target"), _card("old-core")], 10, "/old.webp", "2025-12-01", "test-season", "old-phase", "Legendary"))
    conn.executemany(
        "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(run_id, hero, json.dumps(items), wins, screenshot, created, season, row_phase, rank)
         for run_id, hero, items, wins, screenshot, created, season, row_phase, rank in rows],
    )
    conn.commit()
    conn.close()


def _fake_card_images(monkeypatch):
    from plugins.bazaar_plugin import card_image_helper

    cards = {
        cid: {"internalName": cid, "name": f"中文-{cid}", "size": "Small", "art": ""}
        for cid in ["target", "core-a", "core-b", "old-core"] + [f"flex-{i}" for i in range(1, 13)]
    }
    monkeypatch.setattr(card_image_helper, "_CACHE", {"cards": cards})
    monkeypatch.setattr(card_image_helper, "_url_is_reachable", lambda _: False)
    monkeypatch.setattr(
        card_image_helper,
        "get_art_url",
        lambda card_id=None, internal_name=None, size="art": f"https://cards.example/{card_id}/{size}.webp",
    )


@pytest.mark.usefixtures("monkeypatch")
def test_comp_uses_current_phase_and_tolerates_missing_mapping(tmp_path, monkeypatch):
    _fake_card_images(monkeypatch)
    db_path = tmp_path / "runs.db"
    _make_runs_db(db_path)

    import plugins.bazaar_plugin.runs_query as module
    monkeypatch.setattr(module, "RUNS_SEASON_ID", "test-season")
    monkeypatch.setattr(module, "CURRENT_PHASE", "test-phase")
    module._comp_cache.clear()

    query = RunsQuery(db_path=str(db_path), mapping_path=str(tmp_path / "missing.json"))
    result = query.comp("Vanessa", required_card="target", min_count=1, min_config_count=1)

    assert result["total_runs"] == 12
    assert result["phase"] == "test-phase"
    assert result["season"] == "test-season"
    assert result["recommendations"]
    assert all(card["name_zh"].startswith("中文-") for card in result["recommendations"][0]["cards"])
    assert result["recommendations"][0]["run_id"] in {f"r{i}" for i in range(1, 13)}


def test_load_ignores_null_mapping_values(tmp_path, monkeypatch):
    _fake_card_images(monkeypatch)
    db_path = tmp_path / "runs.db"
    _make_runs_db(db_path)
    mapping_path = tmp_path / "mapping.json"
    mapping_path.write_text(json.dumps({"target": None}), encoding="utf-8")

    query = RunsQuery(db_path=str(db_path), mapping_path=str(mapping_path))
    query.load()

    assert query.card_mapping["target"]["name"] == "target"


def test_card_display_info_uses_original_art_and_size(tmp_path, monkeypatch):
    _fake_card_images(monkeypatch)
    query = RunsQuery(db_path=str(tmp_path / "missing.db"), mapping_path=str(tmp_path / "missing.json"))
    query.load()
    query.card_mapping["target"] = {"name": "target"}
    query.size_map["target"] = "Large"

    display = query.card_display_info("target")

    assert display["cardId"] == "target"
    assert display["size"] == "Large"
    assert display["img"].endswith("/artLarge.webp")
