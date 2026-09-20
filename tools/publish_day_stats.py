"""Safely build, validate, and atomically publish the Day statistics database."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Support both ``python -m tools.publish_day_stats`` and direct script execution.
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from web_runs.day_stats_builder import build_day_stats

DEFAULT_SOURCE = Path("/opt/qiubot/data/bazaar_day_snapshots.db")
DEFAULT_CURRENT = Path("/opt/qiubot/data/bazaar_day_stats.db")
DEFAULT_MIN_FREE_BYTES = 512 * 1024 * 1024
DEFAULT_BACKUP_COUNT = 2
MIN_SCHEMA_VERSION = 5
_VERSION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}\Z")


class PublishError(RuntimeError):
    """Base class for safe-publish failures."""


class DiskSpaceError(PublishError):
    """The destination filesystem cannot safely hold the build."""


class ValidationError(PublishError):
    """The newly built database failed its publication checks."""


class PublishLockedError(PublishError):
    """Another publisher already owns the destination lock."""


def _validate_version_id(version_id: str) -> str:
    if not isinstance(version_id, str) or not _VERSION_RE.fullmatch(version_id):
        raise ValueError("version_id must be 1-80 safe characters: A-Z a-z 0-9 _ . -")
    return version_id


def _readonly_connection(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def _source_cutoff(source: Path) -> str | None:
    with _readonly_connection(source) as connection:
        row = connection.execute(
            """SELECT MAX(s.fetched_at)
               FROM combat_snapshots s
               JOIN snapshot_jobs j ON j.run_id=s.run_id
               WHERE j.status='success'"""
        ).fetchone()
    return row[0] if row else None


def _compact_utc(value: str | None) -> str:
    if not value:
        return "nocutoff"
    candidate = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    except ValueError:
        return re.sub(r"[^0-9A-Za-z]+", "", value)[:32] or "nocutoff"


def generate_version_id(source_cutoff: str | None, now: datetime | None = None) -> str:
    built = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return f"day-{built.strftime('%Y%m%dT%H%M%S%fZ')}-cutoff-{_compact_utc(source_cutoff)}"


def _preflight_disk_space(
    source: Path,
    destination_directory: Path,
    *,
    estimated_output_bytes: int | None,
    min_free_bytes: int,
) -> dict[str, int]:
    if min_free_bytes < 0:
        raise ValueError("min_free_bytes must be non-negative")
    source_bytes = source.stat().st_size
    estimate = estimated_output_bytes if estimated_output_bytes is not None else max(source_bytes, 1)
    if estimate < 0:
        raise ValueError("estimated_output_bytes must be non-negative")
    free_bytes = shutil.disk_usage(destination_directory).free
    required_bytes = source_bytes + estimate + min_free_bytes
    if free_bytes < required_bytes:
        raise DiskSpaceError(
            f"insufficient disk space: free={free_bytes}, required={required_bytes} "
            f"(source={source_bytes}, estimated_output={estimate}, margin={min_free_bytes})"
        )
    return {
        "source_bytes": source_bytes,
        "estimated_output_bytes": estimate,
        "min_free_bytes": min_free_bytes,
        "free_bytes": free_bytes,
        "required_bytes": required_bytes,
    }


def _scalar(connection: sqlite3.Connection, sql: str, parameters: tuple[Any, ...] = ()) -> int:
    return int(connection.execute(sql, parameters).fetchone()[0])


def validate_day_stats(path: str | Path, *, expected_version: str) -> dict[str, Any]:
    """Validate a self-contained, single-version schema-v3-or-newer stats DB."""
    database = Path(path)
    if not database.is_file() or database.stat().st_size <= 0:
        raise ValidationError("database file is missing or empty")

    try:
        with _readonly_connection(database) as connection:
            integrity_rows = [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]
            if integrity_rows != ["ok"]:
                raise ValidationError(f"integrity_check failed: {integrity_rows}")
            foreign_key_errors = [tuple(row) for row in connection.execute("PRAGMA foreign_key_check")]
            if foreign_key_errors:
                raise ValidationError(f"foreign key violations: {foreign_key_errors[:5]}")

            versions = connection.execute("SELECT * FROM build_versions").fetchall()
            if len(versions) != 1:
                raise ValidationError(f"build_versions must contain exactly one row, found {len(versions)}")
            metadata = dict(versions[0])
            if metadata["version_id"] != expected_version:
                raise ValidationError(
                    f"version mismatch: expected {expected_version!r}, found {metadata['version_id']!r}"
                )
            if int(metadata["schema_version"]) < MIN_SCHEMA_VERSION:
                raise ValidationError(f"schema version is below {MIN_SCHEMA_VERSION}")

            counts = {
                "final_compositions": _scalar(connection, "SELECT COUNT(*) FROM final_compositions"),
                "final_composition_cards": _scalar(connection, "SELECT COUNT(*) FROM final_composition_cards"),
                "cores": _scalar(connection, "SELECT COUNT(*) FROM early_day_item_cores"),
                "nodes": _scalar(connection, "SELECT COUNT(*) FROM item_core_route_nodes"),
                "edges": _scalar(connection, "SELECT COUNT(*) FROM item_core_route_edges"),
                "daily_nodes": _scalar(connection, "SELECT COUNT(*) FROM daily_archetype_nodes"),
                "daily_cards": _scalar(connection, "SELECT COUNT(*) FROM daily_archetype_cards"),
                "daily_edges": _scalar(connection, "SELECT COUNT(*) FROM daily_archetype_edges"),
                "daily_edge_members": _scalar(connection, "SELECT COUNT(*) FROM daily_archetype_edge_members"),
            }
            if counts["final_compositions"] <= 0:
                raise ValidationError("final_compositions must not be empty")
            if counts["cores"] <= 0:
                raise ValidationError("early_day_item_cores must not be empty")
            if counts["nodes"] < counts["cores"]:
                raise ValidationError("every core must have at least one route node")
            if any(counts[name] <= 0 for name in (
                "daily_nodes", "daily_cards", "daily_edges", "daily_edge_members"
            )):
                raise ValidationError("daily route tables must not be empty")
            if int(metadata["final_composition_count"]) != counts["final_compositions"]:
                raise ValidationError("metadata final_composition_count is inconsistent")
            if int(metadata["source_snapshot_count"]) < counts["final_compositions"]:
                raise ValidationError("metadata source_snapshot_count is inconsistent")

            orphan_cores = _scalar(
                connection,
                """SELECT COUNT(*) FROM early_day_item_cores c
                   LEFT JOIN item_core_route_nodes n
                     ON n.version_id=c.version_id AND n.core_id=c.core_id
                   WHERE n.core_id IS NULL""",
            )
            invalid_nodes = _scalar(
                connection,
                """SELECT COUNT(*) FROM item_core_route_nodes
                   WHERE run_count <= 0 OR observable_runs <= 0
                      OR run_count > observable_runs
                      OR parent_rate < 0 OR parent_rate > 1
                      OR start_rate < 0 OR start_rate > 1""",
            )
            invalid_edges = _scalar(
                connection,
                """SELECT COUNT(*) FROM item_core_route_edges
                   WHERE run_count <= 0 OR observable_runs <= 0
                      OR run_count > observable_runs OR child_day <= parent_day
                      OR transition_rate < 0 OR transition_rate > 1""",
            )
            invalid_daily_edges = _scalar(
                connection,
                """SELECT COUNT(*) FROM daily_archetype_edges
                   WHERE run_count <= 0 OR parent_runs <= 0 OR parent_observable_runs <= 0
                      OR run_count > parent_observable_runs OR child_day <= parent_day
                      OR transition_rate < 0 OR transition_rate > 1
                      OR continuation_rate < 0 OR continuation_rate > 1""",
            )
            mismatched_daily_members = _scalar(
                connection,
                """SELECT COUNT(*) FROM (
                     SELECT e.edge_id, e.run_count, COUNT(m.run_id) AS member_count
                     FROM daily_archetype_edges e
                     LEFT JOIN daily_archetype_edge_members m
                       ON m.version_id=e.version_id AND m.edge_id=e.edge_id
                     GROUP BY e.version_id, e.edge_id
                     HAVING e.run_count <> member_count
                   )""",
            )
            wrong_versions = sum(
                _scalar(connection, f"SELECT COUNT(*) FROM {table} WHERE version_id <> ?", (expected_version,))
                for table in (
                    "final_compositions", "final_composition_cards", "early_day_item_cores",
                    "item_core_route_nodes", "item_core_route_edges",
                    "item_core_route_node_runs", "item_core_route_edge_runs",
                    "daily_archetype_nodes", "daily_archetype_cards",
                    "daily_archetype_members", "daily_archetype_edges",
                    "daily_archetype_edge_members", "daily_archetype_edge_cards",
                )
            )
            if (orphan_cores or invalid_nodes or invalid_edges or invalid_daily_edges
                    or mismatched_daily_members or wrong_versions):
                raise ValidationError(
                    "relational checks failed: "
                    f"orphan_cores={orphan_cores}, invalid_nodes={invalid_nodes}, "
                    f"invalid_edges={invalid_edges}, invalid_daily_edges={invalid_daily_edges}, "
                    f"mismatched_daily_members={mismatched_daily_members}, "
                    f"wrong_versions={wrong_versions}"
                )
    except ValidationError:
        raise
    except (sqlite3.Error, OSError, KeyError, TypeError, ValueError) as exc:
        raise ValidationError(f"database validation failed: {exc}") from exc

    return {
        "integrity_check": "ok",
        "schema_version": int(metadata["schema_version"]),
        "file_bytes": database.stat().st_size,
        "counts": counts,
        "metadata": metadata,
    }


def _fsync_file(path: Path) -> None:
    with path.open("rb") as handle:
        os.fsync(handle.fileno())


def _fsync_directory(directory: Path) -> None:
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _backup_current(current: Path, version_id: str, backup_count: int) -> Path | None:
    if backup_count <= 0 or not current.is_file():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    safe_version = re.sub(r"[^0-9A-Za-z_.-]", "_", version_id)[:80]
    backup = current.with_name(f"{current.name}.bak.{stamp}-{safe_version}")
    temporary = backup.with_name(f".{backup.name}.tmp")
    try:
        shutil.copy2(current, temporary)
        _fsync_file(temporary)
        os.replace(temporary, backup)
        _fsync_directory(current.parent)
    finally:
        temporary.unlink(missing_ok=True)

    return backup


def _rotate_backups(current: Path, backup_count: int) -> None:
    backups = sorted(
        current.parent.glob(f"{current.name}.bak.*"),
        key=lambda item: (item.stat().st_mtime_ns, item.name),
        reverse=True,
    )
    for expired in backups[backup_count:]:
        expired.unlink(missing_ok=True)


def publish_day_stats(
    source_path: str | Path = DEFAULT_SOURCE,
    current_path: str | Path = DEFAULT_CURRENT,
    *,
    version_id: str | None = None,
    dry_run: bool = False,
    backup_count: int = DEFAULT_BACKUP_COUNT,
    estimated_output_bytes: int | None = None,
    min_free_bytes: int = DEFAULT_MIN_FREE_BYTES,
) -> dict[str, Any]:
    source = Path(source_path)
    current = Path(current_path)
    if not source.is_file():
        raise FileNotFoundError(source)
    if source.resolve() == current.resolve():
        raise ValueError("source and current databases must be separate files")
    if backup_count < 0:
        raise ValueError("backup_count must be non-negative")
    current.parent.mkdir(parents=True, exist_ok=True)

    disk = _preflight_disk_space(
        source, current.parent,
        estimated_output_bytes=estimated_output_bytes,
        min_free_bytes=min_free_bytes,
    )
    cutoff = _source_cutoff(source)
    selected_version = _validate_version_id(
        generate_version_id(cutoff) if version_id is None else version_id
    )
    plan = {
        "source": str(source.resolve()),
        "current": str(current.resolve()),
        "same_directory_temporary": True,
        "atomic_replace": True,
        "backup_count": backup_count,
        "disk": disk,
    }
    if dry_run:
        return {
            "status": "dry-run", "version_id": selected_version,
            "source_cutoff": cutoff, "plan": plan,
        }

    lock_path = current.with_name(f".{current.name}.publish.lock")
    with lock_path.open("a+b") as lock_handle:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise PublishLockedError(f"publication already in progress for {current}") from exc
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{current.name}.build-", suffix=".db", dir=current.parent
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        backup: Path | None = None
        try:
            # The fresh empty file guarantees this current contains one version only;
            # unlike the low-level builder, publication never appends to current.
            temporary.unlink()
            build_summary = build_day_stats(source, temporary, version_id=selected_version)
            validation = validate_day_stats(temporary, expected_version=selected_version)
            _fsync_file(temporary)
            backup = _backup_current(current, selected_version, backup_count)
            os.replace(temporary, current)
            _fsync_directory(current.parent)
            _rotate_backups(current, backup_count)
            return {
                "status": "published",
                "version_id": selected_version,
                "source_cutoff": cutoff,
                "current": str(current.resolve()),
                "backup_path": str(backup) if backup else None,
                "build": build_summary,
                "validation": validation,
                "plan": plan,
            }
        finally:
            temporary.unlink(missing_ok=True)
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=str(DEFAULT_SOURCE))
    parser.add_argument("--current", default=str(DEFAULT_CURRENT))
    parser.add_argument("--version", dest="version_id")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--backup-count", type=int, default=DEFAULT_BACKUP_COUNT)
    parser.add_argument("--estimated-output-bytes", type=int)
    parser.add_argument("--min-free-bytes", type=int, default=DEFAULT_MIN_FREE_BYTES)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        result = publish_day_stats(
            args.source, args.current, version_id=args.version_id,
            dry_run=args.dry_run, backup_count=args.backup_count,
            estimated_output_bytes=args.estimated_output_bytes,
            min_free_bytes=args.min_free_bytes,
        )
    except Exception as exc:
        print(json.dumps({
            "status": "error", "error_type": type(exc).__name__, "error": str(exc),
        }, ensure_ascii=False, sort_keys=True))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
