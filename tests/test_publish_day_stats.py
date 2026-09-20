import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_day_stats_builder import _create_source


def _create_publishable_source(path: Path) -> None:
    _create_source(path)
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO snapshot_jobs VALUES ('second-run', 'success')")
        for day, item in ((1, "shared-item"), (2, "later-item")):
            event_id = f"second-{day}"
            conn.execute(
                """INSERT INTO combat_snapshots VALUES
                   ('second-run', ?, 18, '18.2', 'Vanessa', 'Legendary', ?, 1,
                    '2026-09-18T08:00:00', 2, 'Win', 1, NULL, NULL,
                    '[]', '[]', '[]', '[]', '2026-09-18T14:01:00')""",
                (event_id, day),
            )
            conn.execute(
                "INSERT INTO combat_player_cards VALUES ('second-run', ?, ?, 'item', NULL, NULL, 0)",
                (event_id, item),
            )
        # A second observable Day-3 run makes a real core; Day 4 makes a route edge.
        conn.execute(
            """INSERT INTO combat_snapshots VALUES
               ('second-run', 'second-3', 18, '18.2', 'Vanessa', 'Legendary', 3, 1,
                '2026-09-18T08:00:00', 2, 'Win', 1, NULL, NULL,
                '[]', '[]', '[]', '[]', '2026-09-18T14:02:00')"""
        )
        conn.executemany(
            "INSERT INTO combat_player_cards VALUES ('second-run', 'second-3', ?, 'item', NULL, NULL, ?)",
            [("item-a", 0), ("item-b", 1)],
        )


def _marker(path: Path, value: str) -> None:
    path.write_text(value, encoding="utf-8")


def test_validation_rejects_schema_before_daily_routes_v5(tmp_path):
    import tools.publish_day_stats as publisher
    source = tmp_path / "source.db"
    database = tmp_path / "stats.db"
    _create_publishable_source(source)
    publisher.build_day_stats(source, database, version_id="old-v4")
    with sqlite3.connect(database) as conn:
        conn.execute("UPDATE build_versions SET schema_version=4")
    with pytest.raises(publisher.ValidationError, match="below 5"):
        publisher.validate_day_stats(database, expected_version="old-v4")


def test_validation_rejects_missing_or_empty_daily_route_tables(tmp_path):
    import tools.publish_day_stats as publisher
    source = tmp_path / "source.db"
    database = tmp_path / "stats.db"
    _create_publishable_source(source)
    publisher.build_day_stats(source, database, version_id="daily-v5")
    with sqlite3.connect(database) as conn:
        conn.execute("DELETE FROM daily_archetype_edge_members")
    with pytest.raises(publisher.ValidationError, match="daily route"):
        publisher.validate_day_stats(database, expected_version="daily-v5")


def test_success_builds_valid_single_version_and_atomically_replaces(tmp_path):
    from tools.publish_day_stats import publish_day_stats

    source = tmp_path / "source.db"
    current = tmp_path / "current.db"
    _create_publishable_source(source)
    _marker(current, "old-current")

    result = publish_day_stats(
        source, current, version_id="release-v1", backup_count=0, min_free_bytes=0
    )

    assert result["status"] == "published"
    assert result["version_id"] == "release-v1"
    assert result["validation"]["integrity_check"] == "ok"
    with sqlite3.connect(current) as conn:
        assert conn.execute("SELECT COUNT(*) FROM build_versions").fetchone()[0] == 1
        assert conn.execute("SELECT version_id FROM build_versions").fetchone()[0] == "release-v1"
    assert not list(tmp_path.glob(".current.db.build-*.db"))


def test_republish_replaces_instead_of_appending_versions(tmp_path):
    from tools.publish_day_stats import publish_day_stats

    source = tmp_path / "source.db"
    current = tmp_path / "current.db"
    _create_publishable_source(source)

    publish_day_stats(
        source, current, version_id="release-v1", backup_count=0, min_free_bytes=0
    )
    publish_day_stats(
        source, current, version_id="release-v2", backup_count=0, min_free_bytes=0
    )

    with sqlite3.connect(current) as conn:
        assert conn.execute("SELECT COUNT(*) FROM build_versions").fetchone()[0] == 1
        assert conn.execute("SELECT version_id FROM build_versions").fetchone()[0] == "release-v2"


def test_build_failure_keeps_current_and_cleans_temp(tmp_path, monkeypatch):
    import tools.publish_day_stats as publisher

    source = tmp_path / "source.db"
    current = tmp_path / "current.db"
    _create_publishable_source(source)
    _marker(current, "old-current")

    def fail_build(*args, **kwargs):
        raise RuntimeError("builder failed")

    monkeypatch.setattr(publisher, "build_day_stats", fail_build)
    with pytest.raises(RuntimeError, match="builder failed"):
        publisher.publish_day_stats(source, current, min_free_bytes=0)

    assert current.read_text(encoding="utf-8") == "old-current"
    assert not list(tmp_path.glob(".current.db.build-*.db"))


def test_validation_failure_keeps_current_and_cleans_temp(tmp_path, monkeypatch):
    import tools.publish_day_stats as publisher

    source = tmp_path / "source.db"
    current = tmp_path / "current.db"
    _create_publishable_source(source)
    _marker(current, "old-current")

    def bad_build(_source, output, **_kwargs):
        Path(output).write_bytes(b"not sqlite")
        return {}

    monkeypatch.setattr(publisher, "build_day_stats", bad_build)
    with pytest.raises(publisher.ValidationError):
        publisher.publish_day_stats(source, current, version_id="bad-v1", min_free_bytes=0)

    assert current.read_text(encoding="utf-8") == "old-current"
    assert not list(tmp_path.glob(".current.db.build-*.db"))


def test_replace_failure_is_atomic_and_temp_is_cleaned(tmp_path, monkeypatch):
    import tools.publish_day_stats as publisher

    source = tmp_path / "source.db"
    current = tmp_path / "current.db"
    _create_publishable_source(source)
    _marker(current, "old-current")

    real_replace = os.replace

    def fail_only_publish(src, dst):
        if Path(dst) == current:
            raise OSError("replace failed")
        return real_replace(src, dst)

    monkeypatch.setattr(publisher.os, "replace", fail_only_publish)
    with pytest.raises(OSError, match="replace failed"):
        publisher.publish_day_stats(
            source, current, version_id="release-v1", backup_count=0, min_free_bytes=0
        )

    assert current.read_text(encoding="utf-8") == "old-current"
    assert not list(tmp_path.glob(".current.db.build-*.db"))


def test_replace_failure_with_backups_preserves_all_existing_restore_points(tmp_path, monkeypatch):
    import tools.publish_day_stats as publisher

    source = tmp_path / "source.db"
    current = tmp_path / "current.db"
    _create_publishable_source(source)
    _marker(current, "old-current")
    old_backups = []
    for index in range(3):
        backup = tmp_path / f"current.db.bak.2026010{index}T000000Z-old"
        _marker(backup, str(index))
        old_backups.append(backup)

    real_replace = os.replace

    def fail_only_publish(src, dst):
        if Path(dst) == current:
            raise OSError("replace failed")
        return real_replace(src, dst)

    monkeypatch.setattr(publisher.os, "replace", fail_only_publish)
    with pytest.raises(OSError, match="replace failed"):
        publisher.publish_day_stats(
            source, current, version_id="release-v1", backup_count=2, min_free_bytes=0
        )

    backups = list(tmp_path.glob("current.db.bak.*"))
    assert all(path in backups for path in old_backups)
    assert len(backups) == 4


def test_backup_rotation_retains_only_requested_number(tmp_path):
    from tools.publish_day_stats import publish_day_stats

    source = tmp_path / "source.db"
    current = tmp_path / "current.db"
    _create_publishable_source(source)
    _marker(current, "old-current")
    for index in range(3):
        backup = tmp_path / f"current.db.bak.2026010{index}T000000Z-old"
        _marker(backup, str(index))
        os.utime(backup, (index + 1, index + 1))

    result = publish_day_stats(
        source, current, version_id="release-v1", backup_count=2, min_free_bytes=0
    )

    backups = sorted(tmp_path.glob("current.db.bak.*"))
    assert len(backups) == 2
    assert result["backup_path"] in {str(path) for path in backups}
    assert any(path.read_text(encoding="utf-8") == "old-current" for path in backups)


def test_publish_lock_rejects_a_second_process(tmp_path):
    import tools.publish_day_stats as publisher

    source = tmp_path / "source.db"
    current = tmp_path / "current.db"
    _create_publishable_source(source)
    lock_path = current.with_name(f".{current.name}.publish.lock")
    holder = subprocess.Popen(
        [sys.executable, "-c", (
            "import fcntl,sys,time; "
            "f=open(sys.argv[1],'a+b'); fcntl.flock(f,fcntl.LOCK_EX); "
            "print('locked',flush=True); time.sleep(30)"
        ), str(lock_path)],
        stdout=subprocess.PIPE, text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "locked"
        with pytest.raises(publisher.PublishLockedError, match="already in progress"):
            publisher.publish_day_stats(
                source, current, version_id="release-v1", backup_count=0, min_free_bytes=0
            )
    finally:
        holder.terminate()
        holder.wait(timeout=5)


def test_disk_preflight_failure_does_not_build_or_replace(tmp_path, monkeypatch):
    import tools.publish_day_stats as publisher

    source = tmp_path / "source.db"
    current = tmp_path / "current.db"
    source.write_bytes(b"x" * 100)
    _marker(current, "old-current")
    monkeypatch.setattr(
        publisher.shutil, "disk_usage", lambda _path: SimpleNamespace(total=1000, used=950, free=50)
    )
    built = False

    def should_not_build(*args, **kwargs):
        nonlocal built
        built = True

    monkeypatch.setattr(publisher, "build_day_stats", should_not_build)
    with pytest.raises(publisher.DiskSpaceError):
        publisher.publish_day_stats(
            source, current, estimated_output_bytes=100, min_free_bytes=1
        )

    assert not built
    assert current.read_text(encoding="utf-8") == "old-current"
    assert not list(tmp_path.glob(".current.db.build-*.db"))


def test_dry_run_emits_plan_without_build_or_publish(tmp_path, monkeypatch):
    import tools.publish_day_stats as publisher

    source = tmp_path / "source.db"
    current = tmp_path / "current.db"
    source.write_bytes(b"x" * 100)
    _marker(current, "old-current")
    monkeypatch.setattr(publisher, "_source_cutoff", lambda _path: "2026-09-18T14:02:00")
    monkeypatch.setattr(
        publisher, "build_day_stats", lambda *args, **kwargs: pytest.fail("dry-run built database")
    )

    result = publisher.publish_day_stats(
        source, current, dry_run=True, min_free_bytes=0, estimated_output_bytes=1
    )

    assert result["status"] == "dry-run"
    assert result["plan"]["atomic_replace"] is True
    assert "20260918T140200Z" in result["version_id"]
    assert current.read_text(encoding="utf-8") == "old-current"


@pytest.mark.parametrize("version", ["", "has space", "../escape", "x" * 81])
def test_publish_rejects_unsafe_version_ids(tmp_path, version):
    from tools.publish_day_stats import publish_day_stats

    source = tmp_path / "source.db"
    current = tmp_path / "current.db"
    _create_publishable_source(source)
    with pytest.raises(ValueError, match="version_id"):
        publish_day_stats(source, current, version_id=version, dry_run=True, min_free_bytes=0)
