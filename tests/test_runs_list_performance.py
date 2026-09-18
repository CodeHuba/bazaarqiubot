import importlib.util
import json
import sqlite3
import sys
import types
from pathlib import Path


ROOT = Path(__file__).parents[1]
_pkg = types.ModuleType("plugins.bazaar_plugin")
_pkg.__path__ = [str(ROOT / "plugins" / "bazaar_plugin")]
sys.modules.setdefault("plugins", types.ModuleType("plugins"))
sys.modules["plugins.bazaar_plugin"] = _pkg

for module_name in ("data_client", "runs_query"):
    spec = importlib.util.spec_from_file_location(
        f"plugins.bazaar_plugin.{module_name}",
        ROOT / "plugins" / "bazaar_plugin" / f"{module_name}.py",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

runs_query = sys.modules["plugins.bazaar_plugin.runs_query"]
RunsQuery = runs_query.RunsQuery


def _make_db(path: Path, count: int = 20) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE runs (
            id TEXT PRIMARY KEY, hero TEXT, username TEXT, created_at TEXT,
            items_json TEXT, skills_json TEXT, combats_json TEXT, stat_wins INTEGER,
            stat_losses INTEGER, screenshot_url TEXT, player_rank TEXT,
            season INTEGER, phase TEXT, card_ids_text TEXT
        )"""
    )
    rows = []
    for index in range(count):
        card_id = f"card-{index}"
        rows.append((
            f"run-{index}", "Vanessa", "tester", f"2026-09-17T{index:02d}:00:00+00:00",
            json.dumps([{"cardId": card_id}]), "[]", "[]", 10, 0, "", "Legendary",
            runs_query.RUNS_SEASON_ID, runs_query.CURRENT_PHASE, card_id,
        ))
    conn.executemany("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


def test_runs_query_builds_display_data_only_for_requested_page(tmp_path, monkeypatch):
    db_path = tmp_path / "runs.db"
    mapping_path = tmp_path / "mapping.json"
    _make_db(db_path)
    mapping_path.write_text(
        json.dumps({f"card-{index}": {"name": f"Card {index}"} for index in range(20)}),
        encoding="utf-8",
    )

    query = RunsQuery(db_path=str(db_path), mapping_path=str(mapping_path))
    calls = []
    monkeypatch.setattr(query, "_card_image_info", lambda card_id: calls.append(card_id) or {})

    result = query.query(page=2, page_size=5, min_wins=10)

    assert result["total"] == 20
    assert len(result["runs"]) == 5
    assert len(calls) == 5


def test_runs_query_never_probes_remote_card_urls(tmp_path, monkeypatch):
    db_path = tmp_path / "runs.db"
    mapping_path = tmp_path / "mapping.json"
    _make_db(db_path, count=1)
    mapping_path.write_text(json.dumps({"card-0": {"name": "Card 0"}}), encoding="utf-8")

    import plugins.bazaar_plugin.card_image_helper as card_image_helper
    monkeypatch.setattr(
        card_image_helper,
        "get_art_url",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("remote URL probe is forbidden")),
    )
    monkeypatch.setattr(
        card_image_helper,
        "get_card_image",
        lambda **kwargs: {"artLarge": "https://cdn.example/card.webp", "size": "Medium"},
    )

    result = RunsQuery(db_path=str(db_path), mapping_path=str(mapping_path)).query(min_wins=10)

    assert result["runs"][0]["cards"][0]["img"] == "https://cdn.example/card.webp"


def test_runs_page_has_timeout_and_cancels_previous_request():
    html = (ROOT / "web_runs" / "static" / "runs.html").read_text(encoding="utf-8")
    start = html.index("async function queryRuns(page)")
    end = html.index("\nfunction renderRuns", start)
    function = html[start:end]

    assert "new AbortController()" in html
    assert "activeRunsController.abort()" in function
    assert "setTimeout" in function
    assert "clearInterval(_runsTimer)" in function
    assert "finally" in function
