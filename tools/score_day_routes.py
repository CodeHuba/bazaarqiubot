#!/usr/bin/env python3
"""Read-only evaluator for Day route candidate SQLite databases."""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

WEIGHTS = {"core_richness": 0.30, "route_diversity": 0.30, "core_accuracy": 0.40}
MIN_CORE_SIZE = 2
MIN_CORE_RUNS = 5
MIN_EDGE_RUNS = 5
TARGET_CORES_PER_DAY = 5
TARGET_BRANCHES_PER_PARENT = 4


def wilson_lower(wins: int, total: int, z: float = 1.96) -> float:
    if total <= 0:
        return 0.0
    p = wins / total
    denom = 1 + z * z / total
    centre = p + z * z / (2 * total)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total)
    return max(0.0, (centre - margin) / denom)


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def pct_score(value: float) -> float:
    return round(100.0 * clamp01(value), 3)


def jaccard_distance(a: set[str], b: set[str]) -> float:
    union = a | b
    return 0.0 if not union else 1.0 - len(a & b) / len(union)


def normalized_entropy(counts: list[int]) -> float:
    positive = [int(value) for value in counts if int(value) > 0]
    if len(positive) <= 1:
        return 0.0
    total = sum(positive)
    raw = -sum((value / total) * math.log(value / total) for value in positive)
    return clamp01(raw / math.log(len(positive)))


def weighted_average(rows: list[tuple[float, float]]) -> float:
    total_weight = sum(max(0.0, weight) for _, weight in rows)
    if total_weight <= 0:
        return 0.0
    return sum(value * max(0.0, weight) for value, weight in rows) / total_weight


def placeholders(values) -> str:
    return ",".join("?" for _ in values) or "NULL"


def final_outcomes(conn: sqlite3.Connection, version: str, hero: str) -> dict[str, bool]:
    rows = conn.execute(
        """WITH ranked AS (
               SELECT run_id, LOWER(outcome) AS outcome,
                      ROW_NUMBER() OVER (
                          PARTITION BY run_id ORDER BY day DESC, event_id DESC
                      ) AS rn
               FROM final_compositions
               WHERE version_id=? AND hero=? AND outcome IS NOT NULL
           )
           SELECT run_id, outcome FROM ranked WHERE rn=1""",
        (version, hero),
    ).fetchall()
    return {row["run_id"]: row["outcome"] == "win"
            for row in rows if row["outcome"] in {"win", "loss"}}


def evaluate(path: str) -> dict:
    conn = sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro&immutable=1", uri=True)
    conn.row_factory = sqlite3.Row
    metadata = dict(conn.execute(
        "SELECT * FROM build_versions ORDER BY built_at DESC LIMIT 1"
    ).fetchone())
    version = metadata["version_id"]

    nodes = [dict(row) for row in conn.execute(
        "SELECT * FROM daily_archetype_nodes WHERE version_id=?", (version,)
    )]
    edges = [dict(row) for row in conn.execute(
        "SELECT * FROM daily_archetype_edges WHERE version_id=?", (version,)
    )]
    node_by_id = {row["node_id"]: row for row in nodes}
    node_core = {}
    valid_nodes = []
    filtered_empty = filtered_single = filtered_low_support = 0
    for row in nodes:
        core = set(json.loads(row["core_items"] or "[]"))
        node_core[(row["day"], row["node_id"])] = core
        if not core:
            filtered_empty += 1
        elif len(core) < MIN_CORE_SIZE:
            filtered_single += 1
        elif int(row["run_count"]) < MIN_CORE_RUNS:
            filtered_low_support += 1
        else:
            valid_nodes.append(row)

    members: dict[tuple[int, str], set[str]] = defaultdict(set)
    for row in conn.execute(
        "SELECT day,node_id,run_id FROM daily_archetype_members WHERE version_id=?",
        (version,),
    ):
        members[(int(row["day"]), row["node_id"])].add(row["run_id"])

    edge_members: dict[str, set[str]] = defaultdict(set)
    for row in conn.execute(
        "SELECT edge_id,run_id FROM daily_archetype_edge_members WHERE version_id=?",
        (version,),
    ):
        edge_members[row["edge_id"]].add(row["run_id"])

    heroes = [row[0] for row in conn.execute(
        "SELECT DISTINCT hero FROM final_compositions "
        "WHERE version_id=? AND hero IS NOT NULL ORDER BY hero", (version,)
    )]
    result = {
        "report_version": "route-score-v2",
        "status": "EVALUATED",
        "version_id": version,
        "weights": WEIGHTS,
        "components": {
            "core_richness": {"daily_breadth": 0.40, "run_coverage": 0.30,
                              "same_day_core_diversity": 0.30},
            "route_diversity": {"branch_breadth": 0.25, "normalized_entropy": 0.30,
                                "transition_coverage": 0.25, "edge_stability": 0.20},
            "core_accuracy": {"node_usage": 0.30, "node_win_wilson": 0.30,
                              "node_win_lift": 0.25, "core_support": 0.15},
        },
        "hard_gates": {"min_core_size": MIN_CORE_SIZE, "min_core_runs": MIN_CORE_RUNS,
                       "min_edge_runs": MIN_EDGE_RUNS},
        "global_quality": {
            "filtered_empty_nodes": filtered_empty,
            "filtered_single_card_nodes": filtered_single,
            "filtered_low_support_nodes": filtered_low_support,
        },
        "heroes": {},
    }

    valid_node_ids = {row["node_id"] for row in valid_nodes}
    for hero in heroes:
        hero_nodes = [row for row in valid_nodes if row["hero"] == hero]
        hero_node_ids = {row["node_id"] for row in hero_nodes}
        hero_node_keys = {(int(row["day"]), row["node_id"]) for row in hero_nodes}
        outcomes = final_outcomes(conn, version, hero)
        baseline_wins = sum(outcomes.values())
        baseline_rate = baseline_wins / len(outcomes) if outcomes else 0.0
        effective_runs = conn.execute(
            "SELECT COUNT(DISTINCT run_id) FROM final_compositions WHERE version_id=? AND hero=?",
            (version, hero),
        ).fetchone()[0]
        valid_core_run_ids = set().union(
            *(members.get(key, set()) for key in hero_node_keys)
        ) if hero_node_keys else set()
        coverage = len(valid_core_run_ids) / effective_runs if effective_runs else 0.0

        nodes_by_day: dict[int, list[dict]] = defaultdict(list)
        for row in hero_nodes:
            nodes_by_day[int(row["day"])].append(row)
        daily_breadth = (sum(min(1.0, len(rows) / TARGET_CORES_PER_DAY)
                             for rows in nodes_by_day.values()) / len(nodes_by_day)
                         if nodes_by_day else 0.0)
        same_day_distances = []
        for day_rows in nodes_by_day.values():
            cores = [set(json.loads(row["core_items"])) for row in day_rows]
            distances = [jaccard_distance(cores[i], cores[j])
                         for i in range(len(cores)) for j in range(i + 1, len(cores))]
            if distances:
                same_day_distances.append(sum(distances) / len(distances))
        same_day_core_diversity = (sum(same_day_distances) / len(same_day_distances)
                                   if same_day_distances else 0.0)
        richness = (0.40 * daily_breadth + 0.30 * clamp01(coverage)
                    + 0.30 * same_day_core_diversity)

        hero_edges = [row for row in edges
                      if row["parent_node_id"] in hero_node_ids
                      and row["child_node_id"] in valid_node_ids]
        qualified_edges = []
        for row in hero_edges:
            dynamic_threshold = min(15, max(3, math.ceil(int(row["parent_observable_runs"]) * 0.03)))
            if int(row["run_count"]) >= max(MIN_EDGE_RUNS, dynamic_threshold):
                qualified_edges.append(row)
        raw_transition_runs = set().union(
            *(edge_members.get(row["edge_id"], set()) for row in hero_edges)
        ) if hero_edges else set()
        qualified_transition_runs = set().union(
            *(edge_members.get(row["edge_id"], set()) for row in qualified_edges)
        ) if qualified_edges else set()
        transition_coverage = (len(qualified_transition_runs) / len(raw_transition_runs)
                               if raw_transition_runs else 0.0)

        edges_by_parent: dict[str, list[dict]] = defaultdict(list)
        for row in qualified_edges:
            edges_by_parent[row["parent_node_id"]].append(row)
        branch_breadth_rows = []
        entropy_rows = []
        stability_rows = []
        for parent_id, rows in edges_by_parent.items():
            observable = max(int(row["parent_observable_runs"]) for row in rows)
            branch_breadth_rows.append((
                min(1.0, max(0, len(rows) - 1) / TARGET_BRANCHES_PER_PARENT), observable
            ))
            entropy_rows.append((normalized_entropy([int(row["run_count"]) for row in rows]), observable))
            for row in rows:
                lower = wilson_lower(int(row["run_count"]), int(row["parent_observable_runs"]))
                stability_rows.append((min(1.0, lower / 0.20), int(row["run_count"])))
        branch_breadth = weighted_average(branch_breadth_rows)
        branch_entropy = weighted_average(entropy_rows)
        edge_stability = weighted_average(stability_rows)
        route_diversity = (0.25 * branch_breadth + 0.30 * branch_entropy
                           + 0.25 * transition_coverage + 0.20 * edge_stability)

        usage_rows = []
        win_rows = []
        lift_rows = []
        support_rows = []
        for row in hero_nodes:
            key = (int(row["day"]), row["node_id"])
            run_ids = members.get(key, set())
            resolved = [outcomes[run_id] for run_id in run_ids if run_id in outcomes]
            wins = sum(resolved)
            total = len(resolved)
            usage_score = min(1.0, float(row["day_share"]) / 0.20)
            node_rate = wins / total if total else 0.0
            lift_score = clamp01(0.5 + (node_rate - baseline_rate) / 0.40)
            weight = max(1, int(row["run_count"]))
            usage_rows.append((usage_score, weight))
            win_rows.append((wilson_lower(wins, total), weight))
            lift_rows.append((lift_score, weight))
            support_rows.append((clamp01(float(row["core_support_rate"])), weight))
        node_usage = weighted_average(usage_rows)
        node_win_wilson = weighted_average(win_rows)
        node_win_lift = weighted_average(lift_rows)
        core_support = weighted_average(support_rows)
        accuracy = (0.30 * node_usage + 0.30 * node_win_wilson
                    + 0.25 * node_win_lift + 0.15 * core_support)

        total_score = 100.0 * (WEIGHTS["core_richness"] * richness
                               + WEIGHTS["route_diversity"] * route_diversity
                               + WEIGHTS["core_accuracy"] * accuracy)
        result["heroes"][hero] = {
            "core_richness": pct_score(richness),
            "route_diversity": pct_score(route_diversity),
            "core_accuracy": pct_score(accuracy),
            "route_score": round(total_score, 3),
            "metrics": {
                "effective_runs": effective_runs,
                "valid_core_nodes": len(hero_nodes),
                "valid_core_run_coverage": round(coverage, 6),
                "daily_core_breadth": round(daily_breadth, 6),
                "same_day_core_diversity": round(same_day_core_diversity, 6),
                "qualified_edges": len(qualified_edges),
                "branch_breadth": round(branch_breadth, 6),
                "normalized_branch_entropy": round(branch_entropy, 6),
                "transition_coverage": round(transition_coverage, 6),
                "edge_stability": round(edge_stability, 6),
                "baseline_win_rate": round(baseline_rate, 6),
                "weighted_node_usage": round(node_usage, 6),
                "weighted_node_win_wilson": round(node_win_wilson, 6),
                "weighted_node_win_lift_score": round(node_win_lift, 6),
                "weighted_core_support": round(core_support, 6),
            },
            "gate": "PASS" if not (filtered_empty or filtered_single) else "FAIL",
        }
    conn.close()
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("database")
    parser.add_argument("--json-out")
    args = parser.parse_args()
    report = evaluate(args.database)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.json_out:
        Path(args.json_out).write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
