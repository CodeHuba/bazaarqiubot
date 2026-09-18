import importlib.util
import json
import sqlite3
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = ROOT / "plugins" / "bazaar_plugin"

plugins_pkg = types.ModuleType("plugins")
plugins_pkg.__path__ = [str(ROOT / "plugins")]
bazaar_pkg = types.ModuleType("plugins.bazaar_plugin")
bazaar_pkg.__path__ = [str(PLUGIN_DIR)]
sys.modules.setdefault("plugins", plugins_pkg)
sys.modules.setdefault("plugins.bazaar_plugin", bazaar_pkg)
spec = importlib.util.spec_from_file_location(
    "plugins.bazaar_plugin.runs_query", PLUGIN_DIR / "runs_query.py"
)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
RunsQuery = module.RunsQuery


def _make_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE runs (
            id TEXT, hero TEXT, items_json TEXT, stat_wins INTEGER,
            created_at TEXT, season TEXT, phase TEXT, player_rank TEXT
        )"""
    )
    rows = []
    for index in range(15):
        cards = [{"cardId": "a"}]
        if index < 12:
            cards.append({"cardId": "b"})
        if index < 10:
            cards.append({"cardId": "c"})
        if index < 3:
            cards.append({"cardId": "low-sample"})
        if index == 0:
            cards.append({"cardId": "a"})
        if index == 1:
            cards.append(None)
        rows.append((
            f"r{index}", "Vanessa", json.dumps(cards),
            10 if index < 12 else 8,
            f"2026-09-{index + 1:02d}T00:00:00", "test-season", "test-phase", "Legendary",
        ))
    conn.executemany("INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()


def test_card_tier_table_ranks_exclusive_items_and_keeps_low_samples(tmp_path, monkeypatch):
    db_path = tmp_path / "runs.db"
    _make_db(db_path)
    monkeypatch.setattr(module, "RUNS_SEASON_ID", "test-season")
    monkeypatch.setattr(module, "CURRENT_PHASE", "test-phase")

    query = RunsQuery(db_path=str(db_path), mapping_path=str(tmp_path / "missing.json"))
    query.load()
    query.card_mapping = {
        card_id: {"name": card_id, "type": "Item"}
        for card_id in ("a", "b", "c", "low-sample", "shared")
    }
    query.card_heroes = {
        "a": ["Vanessa"], "b": ["Vanessa"], "c": ["Vanessa"],
        "low-sample": ["Vanessa"], "shared": ["Vanessa", "Dooley"],
    }
    module._card_tier_cache.clear()
    query.card_display_info_cached = lambda card_id: {
        "cardId": card_id, "name": f"中文-{card_id}", "name_en": card_id,
        "img": "", "size": "Small",
    }

    result = query.card_tier_table("Vanessa", rank_filter="legendary")

    assert result["total_runs"] == 15
    assert result["sample_threshold"] == 10
    assert result["formula"] == {"appearance_weight": 0.7, "winrate_weight": 0.3}
    rated_ids = [card["cardId"] for group in result["tiers"] for card in group["cards"]]
    assert rated_ids == ["a", "b", "c"]
    assert result["tiers"][0]["name"] == "夯"
    assert result["tiers"][0]["cards"][0]["appearance_count"] == 15
    assert [card["cardId"] for card in result["insufficient"]] == ["low-sample"]
    assert "shared" not in rated_ids


def test_card_tier_uses_cached_image_metadata_without_remote_probe(tmp_path, monkeypatch):
    db_path = tmp_path / "runs.db"
    _make_db(db_path)
    monkeypatch.setattr(module, "RUNS_SEASON_ID", "test-season")
    monkeypatch.setattr(module, "CURRENT_PHASE", "test-phase")

    query = RunsQuery(db_path=str(db_path), mapping_path=str(tmp_path / "missing.json"))
    query.load()
    query.card_mapping = {"a": {"name": "Alpha", "type": "Item"}}
    query.card_heroes = {"a": ["Vanessa"]}
    module._card_tier_cache.clear()
    query._card_image_info = lambda card_id: {
        "internalName": "Alpha", "name": "阿尔法",
        "art": "https://cards.example/a.webp", "size": "Small",
    }
    query.card_display_info = lambda card_id: (_ for _ in ()).throw(
        AssertionError("card-tier must not run remote image reachability probes")
    )

    result = query.card_tier_table("Vanessa")

    card = result["tiers"][0]["cards"][0]
    assert card["img"] == "https://cards.example/a.webp"
    assert card["name"] == "阿尔法"


def test_all_api_card_display_info_uses_cached_metadata_without_remote_probe(tmp_path):
    query = RunsQuery(db_path=str(tmp_path / "missing.db"), mapping_path=str(tmp_path / "missing.json"))
    query.card_mapping = {"a": {"name": "Alpha"}}
    query._card_image_info = lambda card_id: {
        "internalName": "Alpha", "name": "阿尔法",
        "artLarge": "https://cards.example/a-large.webp",
        "art": "https://cards.example/a.webp", "size": "Medium",
    }

    display = query.card_display_info("a")

    assert display == {
        "cardId": "a", "name": "阿尔法", "name_en": "Alpha",
        "img": "https://cards.example/a-large.webp", "size": "Medium",
    }


def test_heroes_route_uses_official_display_names():
    app_source = (ROOT / "web_runs" / "app.py").read_text(encoding="utf-8")
    start = app_source.index("def api_heroes():")
    end = app_source.index("\n\n@app.route('/api/card_search')", start)
    route = app_source[start:end]

    assert "HERO_EN_TO_ZH" in route
    assert "HERO_ZH_TO_EN" not in route


def test_hero_overview_uses_official_names():
    source = (ROOT / "plugins" / "bazaar_plugin" / "runs_query.py").read_text(encoding="utf-8")
    for name in ("瓦内莎", "皮格马利翁", "斯黛尔"):
        assert name in source
    for legacy in ("海盗/凡妮莎", "猪/皮格", "机甲/斯黛拉"):
        assert legacy not in source
