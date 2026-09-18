import importlib.util
import json
import sqlite3
import sys
import types
from pathlib import Path


# Load the query module without importing the full QQ plugin runtime.
_pkg = types.ModuleType("plugins.bazaar_plugin")
_pkg.__path__ = [str(Path(__file__).parents[1] / "plugins" / "bazaar_plugin")]
sys.modules.setdefault("plugins", types.ModuleType("plugins"))
sys.modules["plugins.bazaar_plugin"] = _pkg
_data_spec = importlib.util.spec_from_file_location(
    "plugins.bazaar_plugin.data_client",
    Path(__file__).parents[1] / "plugins" / "bazaar_plugin" / "data_client.py",
)
_data_module = importlib.util.module_from_spec(_data_spec)
sys.modules[_data_spec.name] = _data_module
_data_spec.loader.exec_module(_data_module)
_query_spec = importlib.util.spec_from_file_location(
    "plugins.bazaar_plugin.runs_query",
    Path(__file__).parents[1] / "plugins" / "bazaar_plugin" / "runs_query.py",
)
_query_module = importlib.util.module_from_spec(_query_spec)
sys.modules[_query_spec.name] = _query_module
_query_spec.loader.exec_module(_query_module)

RUNS_SEASON_ID = _data_module.RUNS_SEASON_ID
CURRENT_PHASE = _data_module.CURRENT_PHASE
RunsQuery = _query_module.RunsQuery


def _make_db(path):
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE runs (
            id TEXT PRIMARY KEY,
            hero TEXT,
            username TEXT,
            created_at TEXT,
            items_json TEXT,
            skills_json TEXT,
            combats_json TEXT,
            stat_wins INTEGER,
            stat_losses INTEGER,
            screenshot_url TEXT,
            player_rank TEXT,
            season INTEGER,
            phase TEXT
        )
        """
    )
    rows = [
        ("run-day-6", "Vanessa", "u1", "2026-09-13T10:00:00+00:00", [{"cardId": "card-a"}], [{"d": 1, "o": "win"}, {"d": 6, "o": "loss"}], 5, 1),
        ("run-day-5", "Vanessa", "u2", "2026-09-13T09:00:00+00:00", [{"cardId": "card-b"}], [{"d": 1, "o": "win"}, {"d": 5, "o": "win"}], 5, 0),
    ]
    conn.executemany(
        """
        INSERT INTO runs
        (id, hero, username, created_at, items_json, skills_json, combats_json,
         stat_wins, stat_losses, screenshot_url, player_rank, season, phase)
        VALUES (?, ?, ?, ?, ?, '[]', ?, ?, ?, '', 'Legendary', ?, ?)
        """,
        [
            (run_id, hero, username, created_at, json.dumps(items), json.dumps(combats), wins, losses, RUNS_SEASON_ID, CURRENT_PHASE)
            for run_id, hero, username, created_at, items, combats, wins, losses in rows
        ],
    )
    conn.commit()
    conn.close()


def test_query_filters_runs_by_reached_game_day(tmp_path):
    db_path = tmp_path / "runs.db"
    _make_db(db_path)

    result = RunsQuery(db_path=str(db_path)).query(day=6, rank_filter="all")

    assert result["total"] == 1
    assert [run["id"] for run in result["runs"]] == ["run-day-6"]


def test_query_without_game_day_keeps_all_runs(tmp_path):
    db_path = tmp_path / "runs.db"
    _make_db(db_path)

    result = RunsQuery(db_path=str(db_path)).query(rank_filter="all")

    assert result["total"] == 2


def test_day_only_query_ignores_unrelated_broken_card_json(tmp_path):
    db_path = tmp_path / "runs.db"
    _make_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE runs SET items_json=?, skills_json=? WHERE id=?",
        ("{broken", "not-json", "run-day-6"),
    )
    conn.commit()
    conn.close()

    result = RunsQuery(db_path=str(db_path)).query(day=6, rank_filter="all")

    assert result["total"] == 1
    assert [run["id"] for run in result["runs"]] == ["run-day-6"]


def test_day_query_skips_row_with_broken_combats_json(tmp_path):
    db_path = tmp_path / "runs.db"
    _make_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE runs SET combats_json=? WHERE id=?", ("{broken", "run-day-6"))
    conn.commit()
    conn.close()

    result = RunsQuery(db_path=str(db_path)).query(day=6, rank_filter="all")

    assert result["total"] == 0
    assert result["runs"] == []


def test_api_runs_accepts_any_positive_game_day_before_querying():
    app_source = (Path(__file__).parents[1] / "web_runs" / "app.py").read_text(encoding="utf-8")
    start = app_source.index("def api_runs():")
    end = app_source.index("\n\n@app.route('/api/winrate'", start)
    function = app_source[start:end]

    validation = "if day is not None and day < 1:"
    assert validation in function
    assert "1 <= day <= 10" not in function
    assert function.index(validation) < function.index("client = RunsQuery()")
