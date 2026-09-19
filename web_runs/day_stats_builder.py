"""Offline builder for versioned final Day-composition facts.

The source snapshot database is opened read-only and read inside one transaction.
Each build is appended to a separate SQLite database under an immutable version ID.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Any


_SCHEMA_VERSION = 3
_ROUTE_OTHER_SIGNATURE = "__OTHER__"
_ROUTE_MIN_SUPPORT_RATE = 0.05
_ROUTE_MAX_MAJOR_SIGNATURES_PER_DAY = 12


_OUTPUT_SCHEMA = """
CREATE TABLE IF NOT EXISTS build_versions (
    version_id TEXT PRIMARY KEY,
    schema_version INTEGER NOT NULL,
    built_at TEXT NOT NULL,
    source_path TEXT NOT NULL,
    source_cutoff TEXT,
    source_success_run_count INTEGER NOT NULL,
    source_snapshot_count INTEGER NOT NULL,
    final_composition_count INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS final_compositions (
    version_id TEXT NOT NULL REFERENCES build_versions(version_id),
    run_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    season INTEGER NOT NULL,
    phase TEXT NOT NULL,
    hero TEXT,
    player_rank TEXT NOT NULL,
    day INTEGER NOT NULL,
    day_stage TEXT NOT NULL,
    hour INTEGER,
    captured_at TEXT,
    fetched_at TEXT NOT NULL,
    player_level INTEGER,
    outcome TEXT,
    is_pvp INTEGER,
    opponent_hero TEXT,
    opponent_name TEXT,
    item_signature TEXT NOT NULL,
    PRIMARY KEY (version_id, run_id, day)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_final_compositions_scope
    ON final_compositions(version_id, season, phase, hero, day, run_id);
CREATE INDEX IF NOT EXISTS idx_final_compositions_signature
    ON final_compositions(version_id, season, phase, hero, day, item_signature);

CREATE TABLE IF NOT EXISTS final_composition_cards (
    version_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    day INTEGER NOT NULL,
    event_id TEXT NOT NULL,
    card_id TEXT NOT NULL,
    card_kind TEXT NOT NULL,
    tier TEXT,
    enchantment TEXT,
    slot_position INTEGER NOT NULL,
    PRIMARY KEY (
        version_id, run_id, day, card_id, card_kind, slot_position
    ),
    FOREIGN KEY (version_id, run_id, day)
        REFERENCES final_compositions(version_id, run_id, day)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_final_cards_card
    ON final_composition_cards(version_id, card_kind, card_id, day, run_id);

CREATE TABLE IF NOT EXISTS early_day_item_cores (
    version_id TEXT NOT NULL REFERENCES build_versions(version_id),
    season INTEGER NOT NULL,
    phase TEXT NOT NULL,
    hero TEXT NOT NULL,
    day INTEGER NOT NULL,
    core_id TEXT NOT NULL,
    card_ids TEXT NOT NULL,
    support_runs INTEGER NOT NULL,
    observable_runs INTEGER NOT NULL,
    coverage REAL NOT NULL,
    rank INTEGER NOT NULL,
    min_support INTEGER NOT NULL,
    PRIMARY KEY (version_id, season, phase, hero, day, core_id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_early_day_item_cores_rank
    ON early_day_item_cores(version_id, season, phase, hero, day, rank);
CREATE UNIQUE INDEX IF NOT EXISTS idx_early_day_item_cores_identity
    ON early_day_item_cores(version_id, core_id);

CREATE TABLE IF NOT EXISTS item_core_route_nodes (
    version_id TEXT NOT NULL,
    core_id TEXT NOT NULL,
    day INTEGER NOT NULL,
    signature TEXT NOT NULL,
    run_count INTEGER NOT NULL,
    observable_runs INTEGER NOT NULL,
    parent_rate REAL NOT NULL,
    start_rate REAL NOT NULL,
    added TEXT NOT NULL,
    removed TEXT NOT NULL,
    retained TEXT NOT NULL,
    PRIMARY KEY (version_id, core_id, day, signature),
    FOREIGN KEY (version_id, core_id)
        REFERENCES early_day_item_cores(version_id, core_id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_item_core_route_nodes_day
    ON item_core_route_nodes(version_id, core_id, day, run_count);

CREATE TABLE IF NOT EXISTS item_core_route_edges (
    version_id TEXT NOT NULL,
    core_id TEXT NOT NULL,
    parent_day INTEGER NOT NULL,
    parent_signature TEXT NOT NULL,
    child_day INTEGER NOT NULL,
    child_signature TEXT NOT NULL,
    run_count INTEGER NOT NULL,
    observable_runs INTEGER NOT NULL,
    transition_rate REAL NOT NULL,
    PRIMARY KEY (
        version_id, core_id, parent_day, parent_signature,
        child_day, child_signature
    ),
    FOREIGN KEY (version_id, core_id, parent_day, parent_signature)
        REFERENCES item_core_route_nodes(version_id, core_id, day, signature),
    FOREIGN KEY (version_id, core_id, child_day, child_signature)
        REFERENCES item_core_route_nodes(version_id, core_id, day, signature)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_item_core_route_edges_parent
    ON item_core_route_edges(version_id, core_id, parent_day, parent_signature);

CREATE TABLE IF NOT EXISTS item_core_route_node_runs (
    version_id TEXT NOT NULL,
    core_id TEXT NOT NULL,
    day INTEGER NOT NULL,
    signature TEXT NOT NULL,
    run_id TEXT NOT NULL,
    PRIMARY KEY (version_id, core_id, day, signature, run_id),
    FOREIGN KEY (version_id, core_id, day, signature)
        REFERENCES item_core_route_nodes(version_id, core_id, day, signature)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS item_core_route_edge_runs (
    version_id TEXT NOT NULL,
    core_id TEXT NOT NULL,
    parent_day INTEGER NOT NULL,
    parent_signature TEXT NOT NULL,
    child_day INTEGER NOT NULL,
    child_signature TEXT NOT NULL,
    run_id TEXT NOT NULL,
    PRIMARY KEY (
        version_id, core_id, parent_day, parent_signature,
        child_day, child_signature, run_id
    ),
    FOREIGN KEY (
        version_id, core_id, parent_day, parent_signature,
        child_day, child_signature
    ) REFERENCES item_core_route_edges(
        version_id, core_id, parent_day, parent_signature,
        child_day, child_signature
    )
) WITHOUT ROWID;
"""


def day_stage(day: int) -> str:
    if day <= 3:
        return "early"
    if day <= 7:
        return "middle"
    if day <= 11:
        return "late"
    return "very_late"


def _readonly_connection(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _read_source(source_path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Read counts, final snapshots and their cards from one source transaction."""
    conn = _readonly_connection(source_path)
    try:
        conn.execute("BEGIN")
        job_columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(snapshot_jobs)")}
        wins_filter = " AND j.stat_wins=10" if "stat_wins" in job_columns else ""
        eligible_where = "j.status='success' AND s.player_rank='Legendary'" + wins_filter
        success_run_count = conn.execute(
            f"""SELECT COUNT(DISTINCT j.run_id)
                FROM snapshot_jobs j
                JOIN combat_snapshots s ON s.run_id=j.run_id
                WHERE {eligible_where}"""
        ).fetchone()[0]
        snapshot_count, source_cutoff = conn.execute(
            """SELECT COUNT(*), MAX(s.fetched_at)
               FROM combat_snapshots s
               JOIN snapshot_jobs j ON j.run_id=s.run_id
               WHERE """ + eligible_where
        ).fetchone()
        snapshots = conn.execute(
            """WITH ranked AS (
                   SELECT s.run_id, s.event_id, s.season, s.phase, s.hero,
                          s.player_rank, s.day, s.hour, s.captured_at,
                          s.player_level, s.outcome, s.is_pvp, s.opponent_hero,
                          s.opponent_name, s.fetched_at,
                          ROW_NUMBER() OVER (
                              PARTITION BY s.run_id, s.day
                              ORDER BY COALESCE(s.hour, -1) DESC,
                                       COALESCE(s.captured_at, '') DESC,
                                       s.event_id DESC
                          ) AS final_rank
                   FROM combat_snapshots s
                   JOIN snapshot_jobs j ON j.run_id=s.run_id
                   WHERE """ + eligible_where + """
               )
               SELECT * FROM ranked WHERE final_rank=1
               ORDER BY run_id, day"""
        ).fetchall()

        facts: list[dict[str, Any]] = []
        for snapshot in snapshots:
            cards = conn.execute(
                """SELECT card_id, card_kind, tier, enchantment, slot_position
                   FROM combat_player_cards
                   WHERE run_id=? AND event_id=?
                   ORDER BY card_kind, card_id, slot_position""",
                (snapshot["run_id"], snapshot["event_id"]),
            ).fetchall()
            item_ids = sorted({row["card_id"] for row in cards if row["card_kind"] == "item"})
            facts.append({
                "snapshot": dict(snapshot),
                "cards": [dict(row) for row in cards],
                # Canonical JSON makes tier/enchantment/slot changes irrelevant to identity.
                "item_signature": json.dumps(item_ids, ensure_ascii=False, separators=(",", ":")),
            })
        conn.commit()
        return ({
            "source_success_run_count": int(success_run_count),
            "source_snapshot_count": int(snapshot_count),
            "source_cutoff": source_cutoff,
        }, facts)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _minimum_support(observable_runs: int) -> int:
    """Require at least two runs and 20% of the observable run population."""
    return max(2, math.ceil(observable_runs * 0.20))


def _discover_early_day_item_cores(
    version_id: str, facts: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Mine closed frequent itemsets from canonical item signatures for Days 1-3."""
    groups: dict[tuple[int, str, str, int], dict[str, frozenset[str]]] = defaultdict(dict)
    for fact in facts:
        snapshot = fact["snapshot"]
        day = int(snapshot["day"])
        if day not in (1, 2, 3):
            continue
        scope = (
            int(snapshot["season"]), str(snapshot["phase"]),
            str(snapshot["hero"] or ""), day,
        )
        # Dict-by-run makes support and the denominator explicitly run-distinct.
        groups[scope][str(snapshot["run_id"])] = frozenset(json.loads(fact["item_signature"]))

    result: list[dict[str, Any]] = []
    for scope, run_items in groups.items():
        observable_runs = len(run_items)
        min_support = _minimum_support(observable_runs)
        support: Counter[frozenset[str]] = Counter()
        for item_ids in run_items.values():
            ordered = sorted(item_ids)
            for size in range(1, len(ordered) + 1):
                support.update(frozenset(choice) for choice in combinations(ordered, size))

        frequent = {items: count for items, count in support.items() if count >= min_support}
        closed = [
            (items, count)
            for items, count in frequent.items()
            if not any(items < other and count == other_count
                       for other, other_count in frequent.items())
        ]
        closed.sort(key=lambda row: (-row[1], -len(row[0]), tuple(sorted(row[0]))))
        season, phase, hero, day = scope
        for rank, (items, count) in enumerate(closed, start=1):
            card_ids = json.dumps(sorted(items), ensure_ascii=False, separators=(",", ":"))
            identity = json.dumps(
                [version_id, season, phase, hero, day, sorted(items)],
                ensure_ascii=False, separators=(",", ":"),
            )
            result.append({
                "version_id": version_id, "season": season, "phase": phase,
                "hero": hero, "day": day,
                "core_id": hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24],
                "card_ids": card_ids, "support_runs": count,
                "observable_runs": observable_runs, "coverage": count / observable_runs,
                "rank": rank, "min_support": min_support,
            })
    return result


def _build_item_core_routes(
    cores: list[dict[str, Any]], facts: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]],
           list[dict[str, Any]], list[dict[str, Any]]]:
    """Build exact-Day route nodes and next-observed-snapshot edges per core.

    A cohort is locked by membership in the core on its starting Day. Each run then
    contributes at most one final item signature per observed Day. Edges connect
    consecutive observed snapshots within that run, so a missing calendar Day is
    neither imputed nor counted as a failed transition.
    """
    by_scope_run: dict[tuple[int, str, str], dict[str, dict[int, str]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    for fact in facts:
        snapshot = fact["snapshot"]
        scope = (
            int(snapshot["season"]), str(snapshot["phase"]),
            str(snapshot["hero"] or ""),
        )
        by_scope_run[scope][str(snapshot["run_id"])][int(snapshot["day"])] = str(
            fact["item_signature"]
        )

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    node_members: list[dict[str, Any]] = []
    # Edge membership is intentionally not materialized: it is derivable by joining
    # consecutive facts (and major-node membership) and was one of the largest tables.
    edge_members: list[dict[str, Any]] = []
    for core in cores:
        version_id = str(core["version_id"])
        core_id = str(core["core_id"])
        start_day = int(core["day"])
        core_items = frozenset(json.loads(core["card_ids"]))
        scoped_runs = by_scope_run.get((
            int(core["season"]), str(core["phase"]), str(core["hero"]),
        ), {})
        cohort = {
            run_id: day_signatures
            for run_id, day_signatures in scoped_runs.items()
            if start_day in day_signatures
            and core_items.issubset(json.loads(day_signatures[start_day]))
        }
        start_runs = len(cohort)
        exact_node_runs: dict[tuple[int, str], set[str]] = defaultdict(set)
        observable_by_day: Counter[int] = Counter()

        for run_id, day_signatures in cohort.items():
            for day, signature in day_signatures.items():
                if day >= start_day:
                    exact_node_runs[(day, signature)].add(run_id)
                    observable_by_day[day] += 1

        # A route is a summary of major development paths, not an index of every
        # unique full composition. Per core/day retain signatures supported by at
        # least max(2, ceil(observable * 5%)), capped at top 12. The remainder is
        # represented by one aggregate OTHER node.
        kept_by_day: dict[int, set[str]] = {}
        for day, observable_runs in observable_by_day.items():
            threshold = max(2, math.ceil(observable_runs * _ROUTE_MIN_SUPPORT_RATE))
            ranked = sorted(
                (
                    (signature, len(members))
                    for (candidate_day, signature), members in exact_node_runs.items()
                    if candidate_day == day and len(members) >= threshold
                ),
                key=lambda row: (-row[1], row[0]),
            )
            kept_by_day[day] = {
                signature for signature, _count
                in ranked[:_ROUTE_MAX_MAJOR_SIGNATURES_PER_DAY]
            }

        def route_signature(day: int, signature: str) -> str:
            if signature in kept_by_day.get(day, set()):
                return signature
            return _ROUTE_OTHER_SIGNATURE

        node_runs: dict[tuple[int, str], set[str]] = defaultdict(set)
        edge_runs: dict[tuple[int, str, int, str], set[str]] = defaultdict(set)
        for run_id, day_signatures in cohort.items():
            timeline = sorted(
                (day, route_signature(day, signature))
                for day, signature in day_signatures.items() if day >= start_day
            )
            for day, signature in timeline:
                node_runs[(day, signature)].add(run_id)
            for parent, child in zip(timeline, timeline[1:]):
                edge_runs[(parent[0], parent[1], child[0], child[1])].add(run_id)

        for (day, signature), members in sorted(node_runs.items()):
            if signature == _ROUTE_OTHER_SIGNATURE:
                added = removed = retained = "[]"
            else:
                signature_items = frozenset(json.loads(signature))
                added = json.dumps(sorted(signature_items - core_items), separators=(",", ":"))
                removed = json.dumps(sorted(core_items - signature_items), separators=(",", ":"))
                retained = json.dumps(sorted(core_items & signature_items), separators=(",", ":"))
            observable_runs = observable_by_day[day]
            row = {
                "version_id": version_id, "core_id": core_id, "day": day,
                "signature": signature, "run_count": len(members),
                "observable_runs": observable_runs,
                "day_observable_rate": len(members) / observable_runs,
                "start_rate": len(members) / start_runs,
                "added": added, "removed": removed, "retained": retained,
            }
            nodes.append(row)
            if signature != _ROUTE_OTHER_SIGNATURE:
                node_members.extend({
                    "version_id": version_id, "core_id": core_id, "day": day,
                    "signature": signature, "run_id": run_id,
                } for run_id in sorted(members))

        for key, members in sorted(edge_runs.items()):
            parent_day, parent_signature, child_day, child_signature = key
            observable_runs = sum(
                len(edge_member_ids)
                for (candidate_parent_day, candidate_parent_signature,
                     candidate_child_day, _candidate_child_signature), edge_member_ids
                in edge_runs.items()
                if candidate_parent_day == parent_day
                and candidate_parent_signature == parent_signature
                and candidate_child_day == child_day
            )
            edges.append({
                "version_id": version_id, "core_id": core_id,
                "parent_day": parent_day, "parent_signature": parent_signature,
                "child_day": child_day, "child_signature": child_signature,
                "run_count": len(members), "observable_runs": observable_runs,
                "transition_rate": len(members) / observable_runs,
            })

    return nodes, edges, node_members, edge_members


def _migrate_output_schema(conn: sqlite3.Connection) -> None:
    """Remove legacy duplicated snapshot JSON when appending to a v2 stats DB."""
    columns = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(final_compositions)")
    }
    for column in ("player_board_json", "skills_json"):
        if column in columns:
            conn.execute(f"ALTER TABLE final_compositions DROP COLUMN {column}")


def build_day_stats(
    source_path: str | Path,
    output_path: str | Path,
    *,
    version_id: str,
    built_at: str | None = None,
) -> dict[str, Any]:
    """Append one immutable version of per-run/per-Day final composition facts."""
    if not isinstance(version_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", version_id):
        raise ValueError("version_id must be 1-80 safe characters: A-Z a-z 0-9 _ . -")

    source = Path(source_path)
    output = Path(output_path)
    if not source.is_file():
        raise FileNotFoundError(source)
    if source.resolve() == output.resolve():
        raise ValueError("source and output databases must be separate files")

    built_at = built_at or datetime.now(timezone.utc).isoformat()
    metadata, facts = _read_source(source)
    output.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(output, timeout=30)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.executescript(_OUTPUT_SCHEMA)
        _migrate_output_schema(conn)
        # DDL/migration is deliberately completed before the facts transaction:
        # executescript may commit implicitly, while version rows and all derived
        # facts below must roll back together on any later failure.
        conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """INSERT INTO build_versions
               (version_id, schema_version, built_at, source_path, source_cutoff,
                source_success_run_count, source_snapshot_count, final_composition_count)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                version_id, _SCHEMA_VERSION, built_at, str(source.resolve()),
                metadata["source_cutoff"], metadata["source_success_run_count"],
                metadata["source_snapshot_count"], len(facts),
            ),
        )
        for fact in facts:
            snapshot = fact["snapshot"]
            conn.execute(
                """INSERT INTO final_compositions
                   (version_id, run_id, event_id, season, phase, hero, player_rank,
                    day, day_stage, hour, captured_at, fetched_at, player_level,
                    outcome, is_pvp, opponent_hero, opponent_name, item_signature)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    version_id, snapshot["run_id"], snapshot["event_id"],
                    snapshot["season"], snapshot["phase"], snapshot["hero"],
                    snapshot["player_rank"], snapshot["day"], day_stage(snapshot["day"]),
                    snapshot["hour"], snapshot["captured_at"], snapshot["fetched_at"],
                    snapshot["player_level"], snapshot["outcome"], snapshot["is_pvp"],
                    snapshot["opponent_hero"], snapshot["opponent_name"],
                    fact["item_signature"],
                ),
            )
            conn.executemany(
                """INSERT INTO final_composition_cards
                   (version_id, run_id, day, event_id, card_id, card_kind,
                    tier, enchantment, slot_position)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [(
                    version_id, snapshot["run_id"], snapshot["day"], snapshot["event_id"],
                    card["card_id"], card["card_kind"], card["tier"],
                    card["enchantment"], card["slot_position"],
                ) for card in fact["cards"]],
            )
        cores = _discover_early_day_item_cores(version_id, facts)
        conn.executemany(
            """INSERT INTO early_day_item_cores
               (version_id, season, phase, hero, day, core_id, card_ids,
                support_runs, observable_runs, coverage, rank, min_support)
               VALUES (:version_id, :season, :phase, :hero, :day, :core_id, :card_ids,
                       :support_runs, :observable_runs, :coverage, :rank, :min_support)""",
            cores,
        )
        routes = _build_item_core_routes(cores, facts)
        route_nodes, route_edges, node_members, edge_members = routes
        conn.executemany(
            """INSERT INTO item_core_route_nodes
               (version_id, core_id, day, signature, run_count, observable_runs,
                parent_rate, start_rate, added, removed, retained)
               VALUES (:version_id, :core_id, :day, :signature, :run_count,
                       :observable_runs, :day_observable_rate, :start_rate, :added,
                       :removed, :retained)""",
            route_nodes,
        )
        conn.executemany(
            """INSERT INTO item_core_route_edges
               (version_id, core_id, parent_day, parent_signature, child_day,
                child_signature, run_count, observable_runs, transition_rate)
               VALUES (:version_id, :core_id, :parent_day, :parent_signature,
                       :child_day, :child_signature, :run_count,
                       :observable_runs, :transition_rate)""",
            route_edges,
        )
        conn.executemany(
            """INSERT INTO item_core_route_node_runs
               (version_id, core_id, day, signature, run_id)
               VALUES (:version_id, :core_id, :day, :signature, :run_id)""",
            node_members,
        )
        conn.executemany(
            """INSERT INTO item_core_route_edge_runs
               (version_id, core_id, parent_day, parent_signature, child_day,
                child_signature, run_id)
               VALUES (:version_id, :core_id, :parent_day, :parent_signature,
                       :child_day, :child_signature, :run_id)""",
            edge_members,
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {
        "version_id": version_id,
        "source_cutoff": metadata["source_cutoff"],
        "source_success_run_count": metadata["source_success_run_count"],
        "source_snapshot_count": metadata["source_snapshot_count"],
        "final_composition_count": len(facts),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build versioned Day final-composition facts")
    parser.add_argument("source", help="source Day snapshot SQLite database")
    parser.add_argument("output", help="separate versioned statistics SQLite database")
    parser.add_argument("--version", required=True, dest="version_id", help="immutable build version ID")
    parser.add_argument("--built-at", help="optional ISO-8601 build timestamp")
    args = parser.parse_args()
    result = build_day_stats(
        args.source, args.output, version_id=args.version_id, built_at=args.built_at
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
