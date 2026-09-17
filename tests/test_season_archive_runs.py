import importlib.util
import sqlite3
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "tools" / "season_archive_runs.py"
spec = importlib.util.spec_from_file_location("season_archive_runs", SCRIPT)
if spec is None or spec.loader is None:
    raise RuntimeError(f"could not load {SCRIPT}")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def make_source_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE phases (season INTEGER, phase TEXT, start_time TEXT,
            PRIMARY KEY (season, phase));
        CREATE TABLE runs (id TEXT PRIMARY KEY, created_at TEXT, season INTEGER,
            phase TEXT, raw_json TEXT);
        CREATE INDEX idx_runs_scope ON runs(season, phase, created_at);
        CREATE TABLE run_snapshot_jobs (run_id TEXT PRIMARY KEY, status TEXT);
        CREATE TABLE run_combat_snapshots (event_id TEXT PRIMARY KEY, run_id TEXT,
            season INTEGER, phase TEXT, day INTEGER NOT NULL);
        CREATE INDEX idx_snapshots_scope ON run_combat_snapshots(season, phase, day);
        CREATE TABLE run_combat_player_cards (event_id TEXT NOT NULL, card_id TEXT NOT NULL,
            card_kind TEXT NOT NULL, slot_position INTEGER, PRIMARY KEY(event_id, card_id, card_kind, slot_position)) WITHOUT ROWID;
        """
    )
    conn.execute("INSERT INTO phases VALUES (18, '18.1', '2026-09-01T00:00:00+00:00')")
    conn.execute("INSERT INTO runs VALUES ('r1', '2026-09-02T00:00:00+00:00', 18, '18.1', '{}')")
    conn.execute("INSERT INTO run_snapshot_jobs VALUES ('r1', 'done')")
    conn.execute("INSERT INTO run_combat_snapshots VALUES ('e1', 'r1', 18, '18.1', 5)")
    conn.execute("INSERT INTO run_combat_player_cards VALUES ('e1', 'c1', 'item', 1)")
    conn.commit()
    conn.close()


def test_empty_online_database_preserves_schema_and_starts_new_phase(tmp_path: Path):
    source = tmp_path / "old.db"
    destination = tmp_path / "new.db"
    make_source_db(source)

    counts = module.create_empty_online_database(
        source, destination, "2026-09-20T02:00:00+00:00", 18, "18.2"
    )

    assert counts == {
        "phases": 1,
        "run_combat_player_cards": 0,
        "run_combat_snapshots": 0,
        "run_snapshot_jobs": 0,
        "runs": 0,
    }
    conn = sqlite3.connect(destination)
    assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    assert conn.execute("SELECT * FROM phases").fetchall() == [
        (18, "18.2", "2026-09-20T02:00:00+00:00")
    ]
    assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM run_combat_snapshots").fetchone()[0] == 0
    assert conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_runs_scope'").fetchone()
    conn.close()


def test_empty_online_database_rejects_unknown_schema_table(tmp_path: Path):
    source = tmp_path / "old.db"
    destination = tmp_path / "new.db"
    make_source_db(source)
    conn = sqlite3.connect(source)
    conn.execute("CREATE TABLE unexpected_table (id INTEGER)")
    conn.close()

    try:
        module.create_empty_online_database(source, destination, "2026-09-20T02:00:00+00:00", 18, "18.2")
    except RuntimeError as error:
        assert "unexpected_table" in str(error)
    else:
        raise AssertionError("unknown schema table must be rejected")
    assert not destination.exists()
