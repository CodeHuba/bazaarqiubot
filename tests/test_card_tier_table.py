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
        # 同一基础卡即使记录重复，一局仍只算一次出场。
        if index == 0:
            cards.append({"cardId": "a"})
        rows.append((
            f"r{index}", "Vanessa", json.dumps(cards),
            10 if index < 12 else 8,
            f"2026-09-{index + 1:02d}T00:00:00", "test-season", "test-phase", "Legendary",
        ))
    conn.executemany("INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()


def test_card_tier_table_uses_dynamic_threshold_and_excludes_shared_cards(tmp_path, monkeypatch):
    db_path = tmp_path / "runs.db"
    _make_db(db_path)
    monkeypatch.setattr(module, "RUNS_SEASON_ID", "test-season")
    monkeypatch.setattr(module, "CURRENT_PHASE", "test-phase")

    query = RunsQuery(db_path=str(db_path), mapping_path=str(tmp_path / "missing.json"))
    query.load()
    query.card_mapping = {
        card_id: {"name": card_id}
        for card_id in ("a", "b", "c", "low-sample", "shared")
    }
    query.card_heroes = {
        "a": ["Vanessa"], "b": ["Vanessa"], "c": ["Vanessa"],
        "low-sample": ["Vanessa"], "shared": ["Vanessa", "Dooley"],
    }
    module._card_tier_cache.clear()
    query.card_display_info = lambda card_id: {
        "cardId": card_id, "name": f"中文-{card_id}", "name_en": card_id,
        "img": f"https://cards.example/{card_id}.webp", "size": "Small",
    }

    result = query.card_tier_table("Vanessa", rank_filter="legendary")

    assert result["total_runs"] == 15
    assert result["sample_threshold"] == 10
    assert result["formula"] == {"appearance_weight": 0.7, "winrate_weight": 0.3}
    rated_ids = [card["cardId"] for group in result["tiers"] for card in group["cards"]]
    assert rated_ids == ["a", "b", "c"]
    assert result["tiers"][0]["name"] == "夯"
    assert result["tiers"][0]["cards"][0]["appearance_count"] == 15
    assert result["tiers"][0]["cards"][0]["ten_win"] == 12
    insufficient_ids = [card["cardId"] for card in result["insufficient"]]
    assert insufficient_ids == ["low-sample"]
    assert "shared" not in rated_ids + insufficient_ids


def test_card_tier_table_keeps_boundary_ties_in_higher_tier(tmp_path, monkeypatch):
    db_path = tmp_path / "runs.db"
    _make_db(db_path)
    # 让 a、b 的出场和 10 胜表现完全一致，二者共同跨过“夯”首个配额边界。
    conn = sqlite3.connect(db_path)
    for index in range(12, 15):
        row = conn.execute("SELECT items_json FROM runs WHERE id=?", (f"r{index}",)).fetchone()
        items = json.loads(row[0])
        items.append({"cardId": "b"})
        conn.execute("UPDATE runs SET items_json=? WHERE id=?", (json.dumps(items), f"r{index}"))
    conn.commit()
    conn.close()
    monkeypatch.setattr(module, "RUNS_SEASON_ID", "test-season")
    monkeypatch.setattr(module, "CURRENT_PHASE", "test-phase")

    query = RunsQuery(db_path=str(db_path), mapping_path=str(tmp_path / "missing.json"))
    query.load()
    query.card_mapping = {card_id: {"name": card_id} for card_id in ("a", "b", "c", "low-sample")}
    query.card_heroes = {card_id: ["Vanessa"] for card_id in query.card_mapping}
    module._card_tier_cache.clear()
    query.card_display_info = lambda card_id: {"cardId": card_id, "name": card_id, "img": "", "size": "Small"}

    result = query.card_tier_table("Vanessa")

    assert result["tie_policy"] == "higher_tier"
    assert [card["cardId"] for card in result["tiers"][0]["cards"]] == ["a", "b"]
