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


def _partner_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE runs (
            items_json TEXT, stat_wins INTEGER, season TEXT, phase TEXT,
            created_at TEXT, player_rank TEXT
        )"""
    )
    conn.executemany(
        "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?)",
        [
            (json.dumps([{"cardId": "target"}, {"cardId": "p1"}, None]), 10, "s", "p", "2026-09-01", "Legendary"),
            (json.dumps([{"cardId": "target"}, {"cardId": "p1"}, {"cardId": "p2"}]), 8, "s", "p", "2026-09-02", "Legendary"),
            (json.dumps([{"cardId": "other"}]), 10, "s", "p", "2026-09-03", "Legendary"),
        ],
    )
    conn.commit()
    conn.close()


def test_partner_counts_target_runs_during_the_single_parse_pass(tmp_path, monkeypatch):
    db_path = tmp_path / "runs.db"
    _partner_db(db_path)
    monkeypatch.setattr(module, "RUNS_SEASON_ID", "s")
    monkeypatch.setattr(module, "CURRENT_PHASE", "p")

    query = RunsQuery(db_path=str(db_path), mapping_path=str(tmp_path / "missing.json"))
    query.load()
    query.find_card_ids = lambda _: ["target"]
    query.translate_name = lambda name: name
    query.get_zh_name = lambda name: name
    query.card_display_info = lambda card_id: {
        "cardId": card_id, "name": card_id, "name_en": card_id,
        "img": "", "size": "Small",
    }

    original_loads = module.json.loads
    calls = 0

    def counting_loads(value):
        nonlocal calls
        calls += 1
        return original_loads(value)

    monkeypatch.setattr(module.json, "loads", counting_loads)
    result = query.partner("target", min_count=1, top_n=5)

    assert calls == 3
    assert result["target_total"] == 2
    assert result["by_appear"][0]["cardId"] == "p1"
    assert result["by_appear"][0]["appear_rate"] == 1.0


def test_default_single_winrate_route_has_a_cache_branch():
    source = (ROOT / "web_runs" / "app.py").read_text(encoding="utf-8")
    route = source[source.index("def api_winrate():"):source.index("\n\n@app.route('/api/partner'", source.index("def api_winrate():"))]
    single = route[route.index("        else:\n            cards", route.index("if multi:")):]

    assert "if use_cache:" in single
    assert "_winrate_from_cache" in single
