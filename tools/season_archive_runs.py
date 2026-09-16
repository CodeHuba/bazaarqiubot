#!/usr/bin/env python3
"""Create an empty online runs database for a new Bazaar phase.

The source database is historical data. Its schema and indexes are copied, but
no old runs or run-derived data are carried into the new phase.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
from pathlib import Path

RUN_DATA_TABLES = {
    "runs",
    "run_snapshot_jobs",
    "run_combat_snapshots",
    "run_combat_player_cards",
}
SUPPORTED_TABLES = RUN_DATA_TABLES | {"phases"}


def _schema_rows(conn: sqlite3.Connection, object_type: str):
    return conn.execute(
        """SELECT name, sql FROM sqlite_master
           WHERE type=? AND name NOT LIKE 'sqlite_%' AND sql IS NOT NULL
           ORDER BY name""",
        (object_type,),
    ).fetchall()


def create_empty_online_database(
    source: str | Path,
    destination: str | Path,
    phase_start_utc: str,
    target_season: int,
    target_phase: str,
) -> dict[str, int]:
    source = Path(source)
    destination = Path(destination)
    if destination.exists():
        destination.unlink()

    source_conn = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=60)
    destination_conn = sqlite3.connect(destination, timeout=60)
    try:
        tables = dict(_schema_rows(source_conn, "table"))
        unexpected = set(tables) - SUPPORTED_TABLES
        missing = {"runs"} - set(tables)
        if unexpected or missing:
            raise RuntimeError(
                f"unsupported runs schema: unexpected={sorted(unexpected)}, missing={sorted(missing)}"
            )

        destination_conn.execute("PRAGMA foreign_keys=OFF")
        for _, sql in tables.items():
            destination_conn.execute(sql)
        for _, sql in _schema_rows(source_conn, "index"):
            destination_conn.execute(sql)
        for _, sql in _schema_rows(source_conn, "trigger"):
            destination_conn.execute(sql)
        if "phases" in tables:
            destination_conn.execute(
                "INSERT INTO phases (season, phase, start_time) VALUES (?, ?, ?)",
                (target_season, target_phase, phase_start_utc),
            )
        destination_conn.execute(
            f"PRAGMA user_version={source_conn.execute('PRAGMA user_version').fetchone()[0]}"
        )
        destination_conn.commit()

        quick_check = destination_conn.execute("PRAGMA quick_check(1)").fetchone()[0]
        if quick_check != "ok":
            raise RuntimeError(f"new online database quick_check failed: {quick_check}")
        return {
            table: destination_conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in tables
        }
    except Exception:
        destination_conn.close()
        source_conn.close()
        if destination.exists():
            destination.unlink()
        raise
    finally:
        try:
            destination_conn.close()
        except Exception:
            pass
        try:
            source_conn.close()
        except Exception:
            pass


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--phase-start-utc", required=True)
    parser.add_argument("--target-season", required=True, type=int)
    parser.add_argument("--target-phase", required=True)
    args = parser.parse_args()

    counts = create_empty_online_database(
        args.source,
        args.destination,
        args.phase_start_utc,
        args.target_season,
        args.target_phase,
    )
    print(" ".join(f"{table}={count}" for table, count in sorted(counts.items())))
    print(f"bytes={os.path.getsize(args.destination)}")


if __name__ == "__main__":
    main()
