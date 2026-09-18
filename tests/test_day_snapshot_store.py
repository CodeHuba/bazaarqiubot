import sqlite3

import pytest


def test_only_new_legendary_ten_win_runs_are_enqueued_in_separate_database(tmp_path):
    from web_runs.day_snapshot_store import DaySnapshotStore

    store = DaySnapshotStore(tmp_path / "day_snapshots.db")
    store.initialize()

    assert store.enqueue_if_eligible(
        run_id="legendary-ten",
        season=18,
        phase="18.2",
        hero="Vanessa",
        player_rank="Legendary",
        stat_wins=10,
    ) is True
    assert store.enqueue_if_eligible(
        run_id="legendary-nine",
        season=18,
        phase="18.2",
        hero="Vanessa",
        player_rank="Legendary",
        stat_wins=9,
    ) is False
    assert store.enqueue_if_eligible(
        run_id="diamond-ten",
        season=18,
        phase="18.2",
        hero="Vanessa",
        player_rank="Diamond",
        stat_wins=10,
    ) is False
    assert store.enqueue_if_eligible(
        run_id="legendary-ten",
        season=18,
        phase="18.2",
        hero="Vanessa",
        player_rank="Legendary",
        stat_wins=10,
    ) is False

    with sqlite3.connect(store.path) as conn:
        assert conn.execute("SELECT run_id, status FROM snapshot_jobs").fetchall() == [
            ("legendary-ten", "pending")
        ]


def test_claim_completion_and_retry_backoff_are_isolated_to_snapshot_database(tmp_path):
    from web_runs.day_snapshot_store import DaySnapshotStore

    store = DaySnapshotStore(tmp_path / "day_snapshots.db")
    store.initialize()
    store.enqueue_if_eligible("run-1", 18, "18.2", "Vanessa", "Legendary", 10, now="2026-09-18T00:00:00")

    first = store.claim(limit=1, now="2026-09-18T00:00:00")
    assert first[0]["run_id"] == "run-1" and first[0]["attempts"] == 1
    store.complete_failure("run-1", "upstream 403", first[0]["lease_token"],
                           now="2026-09-18T00:00:00")
    assert store.claim(limit=1, now="2026-09-18T00:04:59") == []
    second = store.claim(limit=1, now="2026-09-18T00:05:00")
    assert second[0]["run_id"] == "run-1" and second[0]["attempts"] == 2
    store.complete_success("run-1", [{"eventId": "event-1", "day": 1}],
                           second[0]["lease_token"], now="2026-09-18T00:05:01")

    with sqlite3.connect(store.path) as conn:
        assert conn.execute("SELECT status FROM snapshot_jobs").fetchone() == ("success",)
        assert conn.execute("SELECT run_id, event_id, day FROM combat_snapshots").fetchall() == [
            ("run-1", "event-1", 1)
        ]


def test_snapshot_events_are_unique_per_run_not_globally_by_event_id(tmp_path):
    from web_runs.day_snapshot_store import DaySnapshotStore

    store = DaySnapshotStore(tmp_path / "day_snapshots.db")
    store.initialize()
    for run_id in ("run-a", "run-b"):
        store.enqueue_if_eligible(run_id, 18, "18.2", "Vanessa", "Legendary", 10, now="2026-09-18T00:00:00")
        job = store.claim(limit=1, now="2026-09-18T00:00:00")[0]
        store.complete_success(run_id, [{"eventId": "shared", "day": 2}],
                               job["lease_token"], now="2026-09-18T00:00:01")

    with sqlite3.connect(store.path) as conn:
        assert conn.execute("SELECT run_id, event_id FROM combat_snapshots ORDER BY run_id").fetchall() == [
            ("run-a", "shared"), ("run-b", "shared")
        ]



def test_current_phase_backfill_is_idempotent_and_filters_in_sql(tmp_path):
    from web_runs.day_snapshot_store import DaySnapshotStore

    runs_path = tmp_path / "runs.db"
    with sqlite3.connect(runs_path) as conn:
        conn.execute("CREATE TABLE runs (id TEXT, hero TEXT, player_rank TEXT, stat_wins INTEGER, season INTEGER, phase TEXT)")
        conn.executemany("INSERT INTO runs VALUES (?,?,?,?,?,?)", [
            ("eligible", "Vanessa", "Legendary", 10, 18, "18.2"),
            ("wrong-rank", "Vanessa", "Diamond", 10, 18, "18.2"),
            ("wrong-wins", "Vanessa", "Legendary", 9, 18, "18.2"),
            ("old-phase", "Vanessa", "Legendary", 10, 18, "18.1"),
        ])

    store = DaySnapshotStore(tmp_path / "day.db")
    store.initialize()
    assert store.backfill_current_phase(runs_path, 18, "18.2") == {"eligible": 1, "created": 1}
    assert store.backfill_current_phase(runs_path, 18, "18.2") == {"eligible": 1, "created": 0}


def test_non_processing_jobs_cannot_be_completed(tmp_path):
    from web_runs.day_snapshot_store import DaySnapshotStore

    store = DaySnapshotStore(tmp_path / "day_snapshots.db")
    store.initialize()
    store.enqueue_if_eligible("run-1", 18, "18.2", "Vanessa", "Legendary", 10)

    with pytest.raises(ValueError, match="not claimed"):
        store.complete_success("run-1", [{"eventId": "event-1", "day": 1}],
                               "missing-lease-token")
