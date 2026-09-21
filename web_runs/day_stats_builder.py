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


_SCHEMA_VERSION = 5
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

CREATE TABLE IF NOT EXISTS daily_archetype_nodes (
    version_id TEXT NOT NULL,
    season INTEGER NOT NULL,
    phase TEXT NOT NULL,
    hero TEXT NOT NULL,
    day INTEGER NOT NULL,
    node_id TEXT NOT NULL,
    rank INTEGER NOT NULL,
    run_count INTEGER NOT NULL,
    day_observable_runs INTEGER NOT NULL,
    day_share REAL NOT NULL,
    core_items TEXT NOT NULL,
    core_support_runs INTEGER NOT NULL,
    core_support_rate REAL NOT NULL,
    representative_items TEXT NOT NULL,
    PRIMARY KEY (version_id, hero, day, node_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS daily_archetype_cards (
    version_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    day INTEGER NOT NULL,
    card_id TEXT NOT NULL,
    role TEXT NOT NULL,
    support_runs INTEGER NOT NULL,
    support_rate REAL NOT NULL,
    PRIMARY KEY (version_id, node_id, card_id, role)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS daily_archetype_members (
    version_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    day INTEGER NOT NULL,
    run_id TEXT NOT NULL,
    PRIMARY KEY (version_id, node_id, day, run_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS daily_archetype_edges (
    version_id TEXT NOT NULL,
    edge_id TEXT NOT NULL,
    parent_day INTEGER NOT NULL,
    parent_node_id TEXT NOT NULL,
    child_day INTEGER NOT NULL,
    child_node_id TEXT NOT NULL,
    run_count INTEGER NOT NULL,
    parent_runs INTEGER NOT NULL,
    parent_observable_runs INTEGER NOT NULL,
    continuation_rate REAL NOT NULL,
    transition_rate REAL NOT NULL,
    stage_gap INTEGER NOT NULL,
    PRIMARY KEY (version_id, edge_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS daily_archetype_edge_members (
    version_id TEXT NOT NULL,
    edge_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    PRIMARY KEY (version_id, edge_id, run_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS daily_archetype_edge_cards (
    version_id TEXT NOT NULL,
    edge_id TEXT NOT NULL,
    change_kind TEXT NOT NULL,
    card_id TEXT NOT NULL,
    run_count INTEGER NOT NULL,
    change_rate REAL NOT NULL,
    PRIMARY KEY (version_id, edge_id, change_kind, card_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS daily_archetype_paths (
    version_id TEXT NOT NULL,
    path_id TEXT NOT NULL,
    early_node_id TEXT NOT NULL,
    middle_node_id TEXT NOT NULL,
    late_node_id TEXT NOT NULL,
    run_count INTEGER NOT NULL,
    complete_path_runs INTEGER NOT NULL,
    path_share REAL NOT NULL,
    PRIMARY KEY (version_id, path_id)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_daily_archetype_nodes_scope
    ON daily_archetype_nodes(version_id, hero, day, rank);
CREATE INDEX IF NOT EXISTS idx_daily_archetype_edges_parent
    ON daily_archetype_edges(version_id, parent_day, parent_node_id, child_day);
CREATE INDEX IF NOT EXISTS idx_daily_archetype_members_day_run
    ON daily_archetype_members(version_id, day, run_id, node_id);

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


def _build_daily_route_direction_details(
    direction: dict[str, Any], edge_members: dict[str, set[str]],
    run_days: dict[str, dict[int, frozenset[str] | set[str]]]
) -> dict[str, Any]:
    """Choose a real medoid-like board and rank non-core cards for one direction."""
    observations: list[tuple[str, int, frozenset[str]]] = []
    seen: set[tuple[str, int]] = set()
    for variant in direction["variants"]:
        day = int(variant["child_day"])
        for run_id in sorted(edge_members.get(str(variant["edge_id"]), set())):
            key = (run_id, day)
            if key in seen or day not in run_days.get(run_id, {}):
                continue
            seen.add(key)
            observations.append((run_id, day, frozenset(run_days[run_id][day])))
    core = set(direction["target_core_items"])
    card_counts = Counter(card for _run, _day, items in observations
                          for card in items if card not in core)
    total = len(observations)
    associated = [
        {"card_id": card, "support_runs": count, "support_rate": count / total}
        for card, count in sorted(card_counts.items(), key=lambda row: (-row[1], row[0]))
        if count >= max(3, math.ceil(total * 0.10))
    ][:8]
    frequent = {row["card_id"] for row in associated}
    representative = None
    if observations:
        representative = max(
            observations,
            key=lambda row: (
                len(row[2] & frequent),
                -len(row[2] - core - frequent),
                len(row[2]),
                -row[1],
                row[0],
            ),
        )
    return {
        "representative_run_id": representative[0] if representative else None,
        "representative_day": representative[1] if representative else None,
        "representative_items": sorted(representative[2]) if representative else [],
        "associated_cards": associated,
    }


def _build_daily_node_rankings(
    core_items: list[str], snapshots: dict[str, frozenset[str] | set[str]]
) -> dict[str, Any]:
    """Rank real node-member snapshots without allowing similarity chain merges."""
    core = frozenset(core_items)
    total = len(snapshots)
    signature_runs: dict[frozenset[str], set[str]] = defaultdict(set)
    for run_id, raw_items in snapshots.items():
        items = frozenset(raw_items)
        if core.issubset(items):
            signature_runs[items].add(run_id)
    ordered = sorted(signature_runs, key=lambda items: (
        -len(signature_runs[items]), len(items - core), tuple(sorted(items))
    ))
    clusters: list[dict[str, Any]] = []
    for items in ordered:
        non_core = items - core
        target = None
        for cluster in clusters:
            representative = cluster["representative"] - core
            union = non_core | representative
            similarity = len(non_core & representative) / len(union) if union else 1.0
            if similarity >= 0.60:
                target = cluster
                break
        if target is None:
            target = {"representative": items, "runs": set(), "signatures": []}
            clusters.append(target)
        target["runs"].update(signature_runs[items])
        target["signatures"].append(items)

    recommendations = []
    for cluster in clusters:
        representative = min(
            cluster["signatures"],
            key=lambda items: (-len(signature_runs[items]), tuple(sorted(items))),
        )
        run_count = len(cluster["runs"])
        recommendations.append({
            "items": sorted(representative), "run_count": run_count,
            "rate": run_count / total if total else 0.0,
        })
    recommendations.sort(key=lambda row: (-row["run_count"], tuple(row["items"])))

    association_counts = Counter(
        card for items in snapshots.values() for card in set(items) - core
    )
    associated = [
        {"card_id": card, "support_runs": count,
         "support_rate": count / total if total else 0.0}
        for card, count in sorted(association_counts.items(), key=lambda row: (-row[1], row[0]))
    ]
    return {
        "node_global_runs": total,
        "recommended_compositions": recommendations[:3],
        "associated_cards": associated[:8],
    }


def _build_daily_route_directions(
    nodes: list[dict[str, Any]], edges: list[dict[str, Any]], max_directions: int = 3
) -> dict[str, list[dict[str, Any]]]:
    """Reduce raw next-observation edges to a few distinct target-core directions."""
    node_lookup = {(int(row["day"]), str(row["node_id"])): row for row in nodes}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in edges:
        grouped[str(edge["parent_node_id"])].append(edge)

    result: dict[str, list[dict[str, Any]]] = {}
    for parent_node, parent_edges in grouped.items():
        observable = max(int(row["parent_observable_runs"]) for row in parent_edges)
        threshold = min(15, max(3, math.ceil(observable * 0.03)))
        candidates = []
        for edge in parent_edges:
            if int(edge["run_count"]) < threshold:
                continue
            child = node_lookup.get((int(edge["child_day"]), str(edge["child_node_id"])))
            if not child:
                continue
            raw_core = child.get("core_items", [])
            core = tuple(json.loads(raw_core) if isinstance(raw_core, str) else raw_core)
            if not core:
                continue
            candidates.append({"edge": edge, "core": core})
        candidates.sort(key=lambda row: (-int(row["edge"]["run_count"]), row["core"],
                                         int(row["edge"]["child_day"])))

        clusters: list[dict[str, Any]] = []
        for candidate in candidates:
            core_set = set(candidate["core"])
            target = None
            for cluster in clusters:
                representative = set(cluster["representative_core"])
                containment = len(core_set & representative) / min(len(core_set), len(representative))
                if containment >= 0.80:
                    target = cluster
                    break
            if target is None:
                target = {"representative_core": candidate["core"], "members": []}
                clusters.append(target)
            target["members"].append(candidate)

        directions = []
        for cluster in clusters:
            members = cluster["members"]
            run_count = sum(int(row["edge"]["run_count"]) for row in members)
            day_counts = Counter()
            for row in members:
                day_counts[int(row["edge"]["child_day"])] += int(row["edge"]["run_count"])
            directions.append({
                "parent_node_id": parent_node,
                "target_core_items": list(cluster["representative_core"]),
                "run_count": run_count,
                "parent_observable_runs": observable,
                "transition_rate": run_count / observable if observable else 0.0,
                "display_threshold": threshold,
                "variant_count": len(members),
                "arrival_days": [
                    {"day": day, "run_count": count, "rate": count / run_count}
                    for day, count in sorted(day_counts.items())
                ],
                "variants": [dict(row["edge"]) for row in members],
            })
        directions.sort(key=lambda row: (-row["run_count"], row["target_core_items"]))
        selected = directions[:max_directions]
        coverage = sum(row["run_count"] for row in selected) / observable if observable else 0.0
        for direction in selected:
            direction["coverage_rate"] = coverage
        result[parent_node] = selected
    return result


def _build_daily_archetype_routes(
    version_id: str, facts: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]],
           list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Build mutually-exclusive per-Day composition archetypes and observed transitions."""
    states: dict[tuple[int, str, str], dict[str, dict[int, frozenset[str]]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    for fact in facts:
        snapshot = fact["snapshot"]
        scope = (int(snapshot["season"]), str(snapshot["phase"]), str(snapshot["hero"] or ""))
        states[scope][str(snapshot["run_id"])][int(snapshot["day"])] = frozenset(
            json.loads(fact["item_signature"])
        )

    nodes: list[dict[str, Any]] = []
    node_cards: list[dict[str, Any]] = []
    members: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    edge_cards: list[dict[str, Any]] = []
    edge_member_rows: list[dict[str, Any]] = []
    edge_members: dict[tuple[int, str, int, str], set[str]] = defaultdict(set)

    def entropy(p: float) -> float:
        if p <= 0.0 or p >= 1.0:
            return 0.0
        return -p * math.log2(p) - (1.0 - p) * math.log2(1.0 - p)

    for (season, phase, hero), run_days in sorted(states.items()):
        assignment: dict[tuple[str, int], str] = {}
        node_core_items: dict[str, frozenset[str]] = {}
        all_days = sorted({day for timeline in run_days.values() for day in timeline})
        for day in all_days:
            day_items = {run: timeline[day] for run, timeline in run_days.items() if day in timeline}
            run_ids = sorted(day_items)
            n = len(run_ids)
            min_leaf = max(2, math.ceil(n * 0.08))
            counts = Counter(card for cards in day_items.values() for card in cards)
            candidates = [card for card, count in counts.items()
                          if count >= min_leaf and n - count >= min_leaf]
            candidates.sort(key=lambda card: (-entropy(counts[card] / n), -counts[card], card))
            candidates = candidates[:32]
            leaves = [{"runs": run_ids, "present": tuple(), "absent": tuple(), "depth": 0}]
            while len(leaves) < min(6, max(1, n // min_leaf)):
                best = None
                for leaf_index, leaf in enumerate(leaves):
                    leaf_runs = leaf["runs"]
                    if leaf["depth"] >= 3 or len(leaf_runs) < min_leaf * 2:
                        continue
                    available = [card for card in candidates
                                 if card not in leaf["present"] and card not in leaf["absent"]]
                    if not available:
                        continue
                    base = sum(entropy(sum(card in day_items[run] for run in leaf_runs) / len(leaf_runs))
                               for card in candidates) / max(1, len(candidates))
                    for card in available:
                        yes = [run for run in leaf_runs if card in day_items[run]]
                        no = [run for run in leaf_runs if card not in day_items[run]]
                        if len(yes) < min_leaf or len(no) < min_leaf:
                            continue
                        child_impurity = sum(
                            len(child) / len(leaf_runs) *
                            (sum(entropy(sum(feature in day_items[run] for run in child) / len(child))
                                 for feature in candidates) / max(1, len(candidates)))
                            for child in (yes, no)
                        )
                        candidate = (base - child_impurity, min(len(yes), len(no)), card,
                                     leaf_index, yes, no)
                        if candidate[0] >= 0.04 and (best is None or candidate[:3] > best[:3]):
                            best = candidate
                if best is None:
                    break
                _gain, _balance, card, leaf_index, yes, no = best
                parent = leaves.pop(leaf_index)
                leaves.extend([
                    {"runs": yes, "present": tuple(sorted((*parent["present"], card))),
                     "absent": parent["absent"], "depth": parent["depth"] + 1},
                    {"runs": no, "present": parent["present"],
                     "absent": tuple(sorted((*parent["absent"], card))),
                     "depth": parent["depth"] + 1},
                ])

            leaves.sort(key=lambda leaf: (-len(leaf["runs"]), leaf["present"], leaf["absent"]))
            for rank, leaf in enumerate(leaves, start=1):
                leaf_runs = sorted(leaf["runs"])
                leaf_count = len(leaf_runs)
                card_counts = Counter(card for run in leaf_runs for card in day_items[run])
                # Name each archetype with cards that actually co-occur in the same
                # runs. Independent marginal frequencies must never be concatenated
                # into a fake multi-card "core".
                feature_pool = [card for card, count in sorted(
                    card_counts.items(), key=lambda row: (-row[1], row[0])
                ) if count / leaf_count >= 0.20][:16]
                best_core: tuple[tuple[str, ...], int] | None = None
                max_size = min(5, len(feature_pool))
                for size in range(max_size, 1, -1):
                    candidates_for_size = []
                    for combo in combinations(feature_pool, size):
                        support_runs = sum(
                            set(combo).issubset(day_items[run]) for run in leaf_runs
                        )
                        support_rate = support_runs / leaf_count
                        if support_rate >= 0.50:
                            candidates_for_size.append((support_runs, combo))
                    if candidates_for_size:
                        support_runs, combo = max(
                            candidates_for_size,
                            key=lambda row: (row[0], tuple(reversed(row[1]))),
                        )
                        best_core = (tuple(combo), support_runs)
                        break
                if best_core is None:
                    singles = [(count, card) for card, count in card_counts.items()
                               if count / leaf_count >= 0.50]
                    if singles:
                        support_runs, card = max(singles, key=lambda row: (row[0], row[1]))
                        best_core = ((card,), support_runs)
                core_items = list(best_core[0]) if best_core else []
                core_support_runs = best_core[1] if best_core else 0
                core_support_rate = core_support_runs / leaf_count if leaf_count else 0.0
                representative = [card for card, count in sorted(card_counts.items(), key=lambda row: (-row[1], row[0]))
                                  if count / leaf_count >= 0.35][:8]
                identity = json.dumps([version_id, season, phase, hero, day,
                                       leaf["present"], leaf["absent"]],
                                      ensure_ascii=False, separators=(",", ":"))
                node_id = hashlib.sha256(identity.encode()).hexdigest()[:24]
                node_core_items[node_id] = frozenset(core_items)
                nodes.append({
                    "version_id": version_id, "season": season, "phase": phase,
                    "hero": hero, "day": day, "day_stage": day_stage(day),
                    "node_id": node_id, "rank": rank, "run_count": leaf_count,
                    "day_observable_runs": n, "day_share": leaf_count / n,
                    "core_items": json.dumps(core_items, separators=(",", ":")),
                    "core_support_runs": core_support_runs,
                    "core_support_rate": core_support_rate,
                    "representative_items": json.dumps(representative, separators=(",", ":")),
                })
                for card, count in sorted(card_counts.items(), key=lambda row: (-row[1], row[0])):
                    rate = count / leaf_count
                    if card in core_items:
                        role = "core"
                    elif rate >= 0.35:
                        role = "common"
                    elif rate >= 0.20:
                        role = "variant"
                    else:
                        continue
                    node_cards.append({"version_id": version_id, "node_id": node_id,
                                       "day": day, "card_id": card, "role": role,
                                       "support_runs": count, "support_rate": rate})
                for run in leaf_runs:
                    assignment[(run, day)] = node_id
                    members.append({"version_id": version_id, "node_id": node_id,
                                    "day": day, "run_id": run})

        edge_members.clear()
        for run, timeline in run_days.items():
            observed = sorted(day for day in timeline if (run, day) in assignment)
            for parent_day, child_day in zip(observed, observed[1:]):
                if child_day != parent_day + 1:
                    continue
                edge_members[(parent_day, assignment[(run, parent_day)],
                              child_day, assignment[(run, child_day)])].add(run)
        node_counts = Counter((row["day"], row["node_id"]) for row in members)
        # All actual next observations from one parent node share one denominator,
        # even when missing snapshots mean that the next observed Day differs.
        parent_next_runs: dict[tuple[int, str], set[str]] = defaultdict(set)
        for (parent_day, parent_node, _child_day, _child_node), run_set in edge_members.items():
            parent_next_runs[(parent_day, parent_node)].update(run_set)
        for key, run_set in sorted(edge_members.items()):
            parent_day, parent_node, child_day, child_node = key
            observable = len(parent_next_runs[(parent_day, parent_node)])
            edge_id = hashlib.sha256(json.dumps([version_id, *key], separators=(",", ":")).encode()).hexdigest()[:24]
            edges.append({"version_id": version_id, "edge_id": edge_id,
                          "parent_day": parent_day, "parent_node_id": parent_node,
                          "child_day": child_day, "child_node_id": child_node,
                          "run_count": len(run_set),
                          "parent_runs": node_counts[(parent_day, parent_node)],
                          "parent_observable_runs": observable,
                          "continuation_rate": observable / node_counts[(parent_day, parent_node)],
                          "transition_rate": len(run_set) / observable,
                          "stage_gap": child_day - parent_day - 1})
            edge_member_rows.extend({"version_id": version_id, "edge_id": edge_id,
                                     "run_id": run_id}
                                    for run_id in sorted(run_set))
            changes = {"retained": Counter(), "added": Counter(), "removed": Counter()}
            for run in run_set:
                before, after = run_days[run][parent_day], run_days[run][child_day]
                changes["retained"].update(before & after)
                changes["added"].update(after - before)
                changes["removed"].update(before - after)
            for kind, card_counts in changes.items():
                for card, count in card_counts.items():
                    rate = count / len(run_set)
                    if count < 2 or rate < 0.20:
                        continue
                    if kind == "retained" and card in node_core_items[parent_node] and card in node_core_items[child_node]:
                        change_kind = "retained_core"
                    elif kind == "added" and card in node_core_items[child_node]:
                        change_kind = "added_core"
                    elif kind == "removed" and card in node_core_items[parent_node]:
                        change_kind = "removed_core"
                    else:
                        change_kind = kind
                    edge_cards.append({"version_id": version_id, "edge_id": edge_id,
                                       "change_kind": change_kind, "card_id": card,
                                       "run_count": count, "change_rate": rate})
    return nodes, node_cards, members, edges, edge_cards, edge_member_rows


def _build_stage_archetype_routes(
    version_id: str, facts: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]],
           list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Build mutually-exclusive early/middle/late archetypes and real run paths.

    Each run contributes its last observed composition in each stage.  A small,
    deterministic decision tree groups similar compositions by card presence;
    every observed run belongs to exactly one archetype and no anonymous OTHER
    bucket is created.
    """
    stage_order = {"early": 0, "middle": 1, "late": 2}

    def stage_for(day: int) -> str:
        if day <= 3:
            return "early"
        if day <= 7:
            return "middle"
        return "late"

    # scope -> run -> stage -> (day, items); overwrite only with a later Day.
    states: dict[tuple[int, str, str], dict[str, dict[str, tuple[int, frozenset[str]]]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    for fact in facts:
        snapshot = fact["snapshot"]
        scope = (int(snapshot["season"]), str(snapshot["phase"]), str(snapshot["hero"] or ""))
        run_id = str(snapshot["run_id"])
        day = int(snapshot["day"])
        stage = stage_for(day)
        items = frozenset(json.loads(fact["item_signature"]))
        previous = states[scope][run_id].get(stage)
        if previous is None or day > previous[0]:
            states[scope][run_id][stage] = (day, items)

    nodes: list[dict[str, Any]] = []
    node_cards: list[dict[str, Any]] = []
    members: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    edge_cards: list[dict[str, Any]] = []
    paths: list[dict[str, Any]] = []

    def entropy(p: float) -> float:
        if p <= 0.0 or p >= 1.0:
            return 0.0
        return -p * math.log2(p) - (1.0 - p) * math.log2(1.0 - p)

    for scope, run_timeline in sorted(states.items()):
        season, phase, hero = scope
        assignment: dict[tuple[str, str], str] = {}
        state_items: dict[tuple[str, str], frozenset[str]] = {}
        node_core_items: dict[str, frozenset[str]] = {}

        for stage in ("early", "middle", "late"):
            stage_states = {
                run_id: timeline[stage]
                for run_id, timeline in run_timeline.items() if stage in timeline
            }
            if not stage_states:
                continue
            run_ids = sorted(stage_states)
            stage_items = {run_id: stage_states[run_id][1] for run_id in run_ids}
            n = len(run_ids)
            min_leaf = max(3, math.ceil(n * 0.08))
            counts = Counter(card for item_set in stage_items.values() for card in item_set)
            candidates = [
                card for card, count in counts.items()
                if count >= min_leaf and n - count >= min_leaf
            ]
            candidates.sort(key=lambda card: (-entropy(counts[card] / n), -counts[card], card))
            candidates = candidates[:32]

            leaves = [{"runs": run_ids, "present": tuple(), "absent": tuple(), "depth": 0}]
            while len(leaves) < min(6, max(1, n // min_leaf)):
                best = None
                for leaf_index, leaf in enumerate(leaves):
                    leaf_runs = leaf["runs"]
                    if leaf["depth"] >= 3 or len(leaf_runs) < min_leaf * 2:
                        continue
                    available = [c for c in candidates if c not in leaf["present"] and c not in leaf["absent"]]
                    if not available:
                        continue
                    base = sum(entropy(sum(c in stage_items[r] for r in leaf_runs) / len(leaf_runs))
                               for c in candidates) / max(1, len(candidates))
                    for card in available:
                        yes = [r for r in leaf_runs if card in stage_items[r]]
                        no = [r for r in leaf_runs if card not in stage_items[r]]
                        if len(yes) < min_leaf or len(no) < min_leaf:
                            continue
                        child_impurity = 0.0
                        for child in (yes, no):
                            child_impurity += len(child) / len(leaf_runs) * (
                                sum(entropy(sum(c in stage_items[r] for r in child) / len(child))
                                    for c in candidates) / max(1, len(candidates))
                            )
                        gain = base - child_impurity
                        candidate = (gain, min(len(yes), len(no)), card, leaf_index, yes, no)
                        if gain >= 0.04 and (best is None or candidate[:3] > best[:3]):
                            best = candidate
                if best is None:
                    break
                _gain, _balance, card, leaf_index, yes, no = best
                parent = leaves.pop(leaf_index)
                leaves.extend([
                    {"runs": yes, "present": tuple(sorted((*parent["present"], card))),
                     "absent": parent["absent"], "depth": parent["depth"] + 1},
                    {"runs": no, "present": parent["present"],
                     "absent": tuple(sorted((*parent["absent"], card))), "depth": parent["depth"] + 1},
                ])

            leaves.sort(key=lambda leaf: (-len(leaf["runs"]), leaf["present"], leaf["absent"]))
            for rank, leaf in enumerate(leaves, start=1):
                leaf_runs = sorted(leaf["runs"])
                leaf_count = len(leaf_runs)
                card_counts = Counter(card for run_id in leaf_runs for card in stage_items[run_id])
                representative = [card for card, count in sorted(
                    card_counts.items(), key=lambda row: (-row[1], row[0])
                ) if count / leaf_count >= 0.50][:8]
                # Route identity is expressed by a compact set of stable co-occurring
                # core cards. Temporary/variant cards remain supporting context only.
                stable_cards = [card for card, count in sorted(
                    card_counts.items(), key=lambda row: (-row[1], row[0])
                ) if count / leaf_count >= 0.70]
                core_items = list(dict.fromkeys([*leaf["present"], *stable_cards]))[:5]
                identity = json.dumps(
                    [version_id, season, phase, hero, stage, leaf["present"], leaf["absent"]],
                    ensure_ascii=False, separators=(",", ":"),
                )
                node_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
                node_core_items[node_id] = frozenset(core_items)
                snapshot_days = sorted(stage_states[run_id][0] for run_id in leaf_runs)
                median_day = snapshot_days[(len(snapshot_days) - 1) // 2]
                nodes.append({
                    "version_id": version_id, "season": season, "phase": phase,
                    "hero": hero, "stage": stage, "node_id": node_id, "rank": rank,
                    "run_count": leaf_count, "stage_observable_runs": n,
                    "stage_share": leaf_count / n,
                    "min_snapshot_day": snapshot_days[0], "median_snapshot_day": median_day,
                    "max_snapshot_day": snapshot_days[-1],
                    "defining_present": json.dumps(list(leaf["present"]), separators=(",", ":")),
                    "defining_absent": json.dumps(list(leaf["absent"]), separators=(",", ":")),
                    "core_items": json.dumps(core_items, separators=(",", ":")),
                    "representative_items": json.dumps(representative, separators=(",", ":")),
                })
                for card, count in sorted(card_counts.items(), key=lambda row: (-row[1], row[0])):
                    rate = count / leaf_count
                    if card in leaf["present"]:
                        role = "defining"
                    elif rate >= 0.70:
                        role = "stable"
                    elif rate >= 0.35:
                        role = "common"
                    elif rate >= 0.20:
                        role = "variant"
                    else:
                        continue
                    node_cards.append({
                        "version_id": version_id, "node_id": node_id, "stage": stage,
                        "card_id": card, "role": role, "support_runs": count,
                        "support_rate": rate,
                    })
                for run_id in leaf_runs:
                    assignment[(run_id, stage)] = node_id
                    state_items[(run_id, stage)] = stage_items[run_id]
                    members.append({
                        "version_id": version_id, "node_id": node_id, "stage": stage,
                        "run_id": run_id, "snapshot_day": stage_states[run_id][0],
                    })

        edge_members: dict[tuple[str, str, str, str], set[str]] = defaultdict(set)
        path_members: dict[tuple[str, str, str], set[str]] = defaultdict(set)
        for run_id, timeline in run_timeline.items():
            observed = [stage for stage in ("early", "middle", "late") if (run_id, stage) in assignment]
            for parent_stage, child_stage in zip(observed, observed[1:]):
                edge_members[(parent_stage, assignment[(run_id, parent_stage)],
                              child_stage, assignment[(run_id, child_stage)])].add(run_id)
            if len(observed) == 3:
                path_members[tuple(assignment[(run_id, stage)] for stage in observed)].add(run_id)

        parent_runs = Counter((row["stage"], row["node_id"]) for row in members
                              if any(row["run_id"] == run for run in run_timeline))
        for key, run_set in sorted(edge_members.items()):
            parent_stage, parent_node, child_stage, child_node = key
            observable = sum(len(value) for candidate, value in edge_members.items()
                             if candidate[0] == parent_stage and candidate[1] == parent_node
                             and candidate[2] == child_stage)
            edge_id = hashlib.sha256(json.dumps(
                [version_id, *key], separators=(",", ":")
            ).encode()).hexdigest()[:24]
            edges.append({
                "version_id": version_id, "edge_id": edge_id,
                "parent_stage": parent_stage, "parent_node_id": parent_node,
                "child_stage": child_stage, "child_node_id": child_node,
                "run_count": len(run_set), "parent_runs": parent_runs[(parent_stage, parent_node)],
                "parent_observable_runs": observable,
                "continuation_rate": observable / parent_runs[(parent_stage, parent_node)],
                "transition_rate": len(run_set) / observable,
                "stage_gap": stage_order[child_stage] - stage_order[parent_stage] - 1,
            })
            changes = {"retained": Counter(), "added": Counter(), "removed": Counter()}
            parent_core = node_core_items[parent_node]
            child_core = node_core_items[child_node]
            for run_id in run_set:
                before = state_items[(run_id, parent_stage)]
                after = state_items[(run_id, child_stage)]
                changes["retained"].update(before & after)
                changes["added"].update(after - before)
                changes["removed"].update(before - after)
            for kind, card_counts in changes.items():
                for card, count in card_counts.items():
                    rate = count / len(run_set)
                    if count >= 3 and rate >= 0.20:
                        if kind == "retained" and card in parent_core and card in child_core:
                            change_kind = "retained_core"
                        elif kind == "added" and card in child_core:
                            change_kind = "added_core"
                        elif kind == "removed" and card in parent_core:
                            change_kind = "removed_core"
                        else:
                            change_kind = kind
                        edge_cards.append({
                            "version_id": version_id, "edge_id": edge_id,
                            "change_kind": change_kind, "card_id": card,
                            "run_count": count, "change_rate": rate,
                        })

        complete_runs = sum(len(value) for value in path_members.values())
        for node_ids, run_set in sorted(path_members.items(), key=lambda row: (-len(row[1]), row[0])):
            path_id = hashlib.sha256(json.dumps(
                [version_id, season, phase, hero, *node_ids], separators=(",", ":")
            ).encode()).hexdigest()[:24]
            paths.append({
                "version_id": version_id, "season": season, "phase": phase, "hero": hero,
                "path_id": path_id, "early_node_id": node_ids[0],
                "middle_node_id": node_ids[1], "late_node_id": node_ids[2],
                "run_count": len(run_set), "complete_path_runs": complete_runs,
                "path_share": len(run_set) / complete_runs,
            })

    return nodes, node_cards, members, edges, edge_cards, paths


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
    """Apply additive migrations before appending a new immutable version."""
    columns = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(final_compositions)")
    }
    for column in ("player_board_json", "skills_json"):
        if column in columns:
            conn.execute(f"ALTER TABLE final_compositions DROP COLUMN {column}")

    daily_columns = {
        str(row[1]) for row in conn.execute("PRAGMA table_info(daily_archetype_nodes)")
    }
    if daily_columns and "core_support_runs" not in daily_columns:
        conn.execute(
            "ALTER TABLE daily_archetype_nodes ADD COLUMN core_support_runs INTEGER NOT NULL DEFAULT 0"
        )
    if daily_columns and "core_support_rate" not in daily_columns:
        conn.execute(
            "ALTER TABLE daily_archetype_nodes ADD COLUMN core_support_rate REAL NOT NULL DEFAULT 0"
        )


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
    source_scopes = {
        (int(fact["snapshot"]["season"]), str(fact["snapshot"]["phase"]))
        for fact in facts
    }
    if len(source_scopes) != 1:
        raise ValueError(
            "one statistics version must contain exactly one season/phase; "
            f"found {sorted(source_scopes)}"
        )
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
        daily_nodes, daily_cards, daily_members, daily_edges, daily_edge_cards, daily_edge_members = _build_daily_archetype_routes(
            version_id, facts
        )
        conn.executemany(
            """INSERT INTO daily_archetype_nodes
               (version_id, season, phase, hero, day, node_id, rank, run_count,
                day_observable_runs, day_share, core_items, core_support_runs,
                core_support_rate, representative_items)
               VALUES (:version_id, :season, :phase, :hero, :day, :node_id, :rank,
                       :run_count, :day_observable_runs, :day_share, :core_items,
                       :core_support_runs, :core_support_rate, :representative_items)""",
            daily_nodes,
        )
        conn.executemany(
            """INSERT INTO daily_archetype_cards
               (version_id, node_id, day, card_id, role, support_runs, support_rate)
               VALUES (:version_id, :node_id, :day, :card_id, :role,
                       :support_runs, :support_rate)""",
            daily_cards,
        )
        conn.executemany(
            """INSERT INTO daily_archetype_members
               (version_id, node_id, day, run_id)
               VALUES (:version_id, :node_id, :day, :run_id)""",
            daily_members,
        )
        conn.executemany(
            """INSERT INTO daily_archetype_edges
               (version_id, edge_id, parent_day, parent_node_id, child_day,
                child_node_id, run_count, parent_runs, parent_observable_runs,
                continuation_rate, transition_rate, stage_gap)
               VALUES (:version_id, :edge_id, :parent_day, :parent_node_id,
                       :child_day, :child_node_id, :run_count, :parent_runs,
                       :parent_observable_runs, :continuation_rate,
                       :transition_rate, :stage_gap)""",
            daily_edges,
        )
        conn.executemany(
            """INSERT INTO daily_archetype_edge_members
               (version_id, edge_id, run_id)
               VALUES (:version_id, :edge_id, :run_id)""",
            daily_edge_members,
        )
        conn.executemany(
            """INSERT INTO daily_archetype_edge_cards
               (version_id, edge_id, change_kind, card_id, run_count, change_rate)
               VALUES (:version_id, :edge_id, :change_kind, :card_id,
                       :run_count, :change_rate)""",
            daily_edge_cards,
        )
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
