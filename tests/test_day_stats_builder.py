import json
import sqlite3

import pytest


def _create_source(path):
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE snapshot_jobs (
                run_id TEXT PRIMARY KEY,
                status TEXT NOT NULL
            );
            CREATE TABLE combat_snapshots (
                run_id TEXT NOT NULL,
                event_id TEXT NOT NULL,
                season INTEGER NOT NULL,
                phase TEXT NOT NULL,
                hero TEXT,
                player_rank TEXT NOT NULL,
                day INTEGER NOT NULL,
                hour INTEGER,
                captured_at TEXT,
                player_level INTEGER,
                outcome TEXT,
                is_pvp INTEGER,
                opponent_hero TEXT,
                opponent_name TEXT,
                player_board_json TEXT NOT NULL,
                player_skills_json TEXT NOT NULL,
                opponent_board_json TEXT NOT NULL,
                opponent_skills_json TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                PRIMARY KEY (run_id, event_id)
            );
            CREATE TABLE combat_player_cards (
                run_id TEXT NOT NULL,
                event_id TEXT NOT NULL,
                card_id TEXT NOT NULL,
                card_kind TEXT NOT NULL,
                tier TEXT,
                enchantment TEXT,
                slot_position INTEGER NOT NULL DEFAULT -1,
                PRIMARY KEY (run_id, event_id, card_id, card_kind, slot_position)
            );
            """
        )
        conn.executemany("INSERT INTO snapshot_jobs VALUES (?, ?)", [
            ("success-run", "success"),
            ("unfinished-run", "processing"),
        ])
        snapshots = [
            # Same hour: captured_at, then event_id, decide the final snapshot.
            ("success-run", "older-hour", 3, 7, "2026-09-18T09:00:00", "2026-09-18T10:00:00"),
            ("success-run", "same-hour-a", 3, 8, "2026-09-18T09:30:00", "2026-09-18T10:01:00"),
            ("success-run", "same-hour-b", 3, 8, "2026-09-18T09:30:00", "2026-09-18T10:02:00"),
            ("success-run", "middle", 4, 1, "2026-09-18T11:00:00", "2026-09-18T11:01:00"),
            ("success-run", "late", 8, 1, "2026-09-18T12:00:00", "2026-09-18T12:01:00"),
            ("success-run", "very-late", 12, 1, "2026-09-18T13:00:00", "2026-09-18T13:01:00"),
            ("unfinished-run", "ignored", 2, 9, "2026-09-18T14:00:00", "2026-09-18T14:01:00"),
        ]
        for run_id, event_id, day, hour, captured_at, fetched_at in snapshots:
            conn.execute(
                """INSERT INTO combat_snapshots VALUES
                   (?, ?, 18, '18.2', 'Vanessa', 'Legendary', ?, ?, ?, 10,
                    'Win', 1, NULL, NULL, '[]', ?, '[]', '[]', ?)""",
                (run_id, event_id, day, hour, captured_at,
                 json.dumps([{"baseId": "skill-detail", "tierOverride": "Gold"}]), fetched_at),
            )
        conn.executemany("INSERT INTO combat_player_cards VALUES (?, ?, ?, ?, ?, ?, ?)", [
            ("success-run", "older-hour", "old-item", "item", "Bronze", None, 0),
            ("success-run", "same-hour-a", "wrong-tiebreak", "item", "Bronze", None, 0),
            ("success-run", "same-hour-b", "item-b", "item", "Diamond", "Fiery", 5),
            # Same base card with different quality/enchantment/slot stays in detail,
            # but must not change the item-only composition signature.
            ("success-run", "same-hour-b", "item-b", "item", "Gold", None, 6),
            ("success-run", "same-hour-b", "item-a", "item", "Bronze", None, 1),
            ("success-run", "same-hour-b", "skill-detail", "skill", "Gold", None, 9),
            ("success-run", "middle", "middle-item", "item", None, None, 0),
            ("success-run", "late", "late-item", "item", None, None, 0),
            ("success-run", "very-late", "very-late-item", "item", None, None, 0),
            ("unfinished-run", "ignored", "ignored-item", "item", None, None, 0),
        ])


def test_build_rejects_mixed_season_phase_source(tmp_path):
    from web_runs.day_stats_builder import build_day_stats

    source = tmp_path / "mixed-source.db"
    output = tmp_path / "stats.db"
    _create_source(source)
    with sqlite3.connect(source) as conn:
        conn.execute(
            "UPDATE combat_snapshots SET phase='18.3' WHERE event_id='very-late'"
        )
    with pytest.raises(ValueError, match="exactly one season/phase"):
        build_day_stats(source, output, version_id="mixed-v1")


def test_build_versioned_final_composition_facts(tmp_path):
    from web_runs.day_stats_builder import build_day_stats

    source = tmp_path / "source.db"
    output = tmp_path / "day-stats.db"
    _create_source(source)

    result = build_day_stats(
        source,
        output,
        version_id="test-v1",
        built_at="2026-09-19T00:00:00+00:00",
    )

    assert result == {
        "version_id": "test-v1",
        "source_cutoff": "2026-09-18T13:01:00",
        "source_success_run_count": 1,
        "source_snapshot_count": 6,
        "final_composition_count": 4,
    }
    with sqlite3.connect(output) as conn:
        conn.row_factory = sqlite3.Row
        metadata = dict(conn.execute(
            "SELECT * FROM build_versions WHERE version_id='test-v1'"
        ).fetchone())
        assert metadata["built_at"] == "2026-09-19T00:00:00+00:00"
        assert metadata["schema_version"] == 5
        assert metadata["source_cutoff"] == "2026-09-18T13:01:00"
        assert metadata["source_success_run_count"] == 1
        assert metadata["source_snapshot_count"] == 6
        assert metadata["final_composition_count"] == 4

        facts = conn.execute(
            """SELECT day, day_stage, event_id, item_signature
               FROM final_compositions ORDER BY day"""
        ).fetchall()
        assert [(r["day"], r["day_stage"], r["event_id"], r["item_signature"])
                for r in facts] == [
            (3, "early", "same-hour-b", '["item-a","item-b"]'),
            (4, "middle", "middle", '["middle-item"]'),
            (8, "late", "late", '["late-item"]'),
            (12, "very_late", "very-late", '["very-late-item"]'),
        ]
        columns = {row[1] for row in conn.execute("PRAGMA table_info(final_compositions)")}
        assert "player_board_json" not in columns
        assert "skills_json" not in columns

        cards = conn.execute(
            """SELECT card_id, card_kind, tier, enchantment, slot_position
               FROM final_composition_cards
               WHERE version_id='test-v1' AND run_id='success-run' AND day=3
               ORDER BY card_kind, card_id"""
        ).fetchall()
        assert [tuple(row) for row in cards] == [
            ("item-a", "item", "Bronze", None, 1),
            ("item-b", "item", "Diamond", "Fiery", 5),
            ("item-b", "item", "Gold", None, 6),
            ("skill-detail", "skill", "Gold", None, 9),
        ]

    # A later build is appended as another immutable version, not an overwrite.
    build_day_stats(source, output, version_id="test-v2", built_at="2026-09-19T01:00:00+00:00")
    with sqlite3.connect(output) as conn:
        assert conn.execute("SELECT COUNT(*) FROM build_versions").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM final_compositions").fetchone()[0] == 8


def test_build_only_includes_successful_legendary_runs(tmp_path):
    from web_runs.day_stats_builder import build_day_stats

    source = tmp_path / "source.db"
    output = tmp_path / "day-stats.db"
    _create_source(source)
    with sqlite3.connect(source) as conn:
        conn.execute("INSERT INTO snapshot_jobs VALUES ('ordinary-success', 'success')")
        conn.execute(
            """INSERT INTO combat_snapshots VALUES
               ('ordinary-success', 'ordinary-event', 18, '18.2', 'Vanessa', 'Gold', 1, 1,
                '2026-09-18T08:00:00', 2, 'Win', 1, NULL, NULL,
                '[]', '[]', '[]', '[]', '2026-09-18T14:01:00')"""
        )

    result = build_day_stats(source, output, version_id="legendary-only")

    assert result["source_success_run_count"] == 1
    with sqlite3.connect(output) as conn:
        assert conn.execute("SELECT DISTINCT player_rank FROM final_compositions").fetchall() == [
            ("Legendary",)
        ]


def test_build_filters_non_ten_win_jobs_when_source_schema_has_stat_wins(tmp_path):
    from web_runs.day_stats_builder import build_day_stats

    source = tmp_path / "source.db"
    output = tmp_path / "day-stats.db"
    _create_source(source)
    with sqlite3.connect(source) as conn:
        conn.execute("ALTER TABLE snapshot_jobs ADD COLUMN stat_wins INTEGER")
        conn.execute("UPDATE snapshot_jobs SET stat_wins=10 WHERE run_id='success-run'")
        conn.execute("INSERT INTO snapshot_jobs VALUES ('nine-win', 'success', 9)")
        conn.execute(
            """INSERT INTO combat_snapshots VALUES
               ('nine-win', 'nine-win-event', 18, '18.2', 'Vanessa', 'Legendary', 1, 1,
                '2026-09-18T08:00:00', 2, 'Win', 1, NULL, NULL,
                '[]', '[]', '[]', '[]', '2026-09-18T14:01:00')"""
        )

    result = build_day_stats(source, output, version_id="ten-win-only")

    assert result["source_success_run_count"] == 1
    with sqlite3.connect(output) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM final_compositions WHERE run_id='nine-win'"
        ).fetchone()[0] == 0


def test_build_rolls_back_version_and_facts_after_post_schema_failure(tmp_path, monkeypatch):
    import web_runs.day_stats_builder as builder

    source = tmp_path / "source.db"
    output = tmp_path / "day-stats.db"
    _create_source(source)

    def fail_late(*_args, **_kwargs):
        raise RuntimeError("late build failure")

    monkeypatch.setattr(builder, "_discover_early_day_item_cores", fail_late)
    with pytest.raises(RuntimeError, match="late build failure"):
        builder.build_day_stats(source, output, version_id="rollback-v1")

    with sqlite3.connect(output) as conn:
        # Schema DDL is intentionally completed before the data transaction; facts are atomic.
        assert conn.execute("SELECT COUNT(*) FROM build_versions").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM final_compositions").fetchone()[0] == 0


def test_build_migrates_legacy_json_columns_before_appending(tmp_path):
    from web_runs.day_stats_builder import build_day_stats

    source = tmp_path / "source.db"
    output = tmp_path / "legacy-day-stats.db"
    _create_source(source)
    with sqlite3.connect(output) as conn:
        conn.executescript("""
            CREATE TABLE build_versions (
                version_id TEXT PRIMARY KEY, schema_version INTEGER NOT NULL,
                built_at TEXT NOT NULL, source_path TEXT NOT NULL, source_cutoff TEXT,
                source_success_run_count INTEGER NOT NULL,
                source_snapshot_count INTEGER NOT NULL,
                final_composition_count INTEGER NOT NULL
            );
            CREATE TABLE final_compositions (
                version_id TEXT NOT NULL, run_id TEXT NOT NULL, event_id TEXT NOT NULL,
                season INTEGER NOT NULL, phase TEXT NOT NULL, hero TEXT,
                player_rank TEXT NOT NULL, day INTEGER NOT NULL, day_stage TEXT NOT NULL,
                hour INTEGER, captured_at TEXT, fetched_at TEXT NOT NULL,
                player_level INTEGER, outcome TEXT, is_pvp INTEGER,
                opponent_hero TEXT, opponent_name TEXT, item_signature TEXT NOT NULL,
                player_board_json TEXT NOT NULL, skills_json TEXT NOT NULL,
                PRIMARY KEY (version_id, run_id, day)
            ) WITHOUT ROWID;
        """)

    build_day_stats(source, output, version_id="migrated-v3")

    with sqlite3.connect(output) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(final_compositions)")}
        assert "player_board_json" not in columns
        assert "skills_json" not in columns
        assert conn.execute("SELECT COUNT(*) FROM final_compositions").fetchone()[0] == 4


def test_build_discovers_closed_early_day_item_core(tmp_path):
    from web_runs.day_stats_builder import build_day_stats

    source = tmp_path / "source.db"
    output = tmp_path / "day-stats.db"
    _create_source(source)
    with sqlite3.connect(source) as conn:
        conn.executemany("INSERT INTO snapshot_jobs VALUES (?, 'success')", [
            ("core-run-1",), ("core-run-2",), ("core-run-3",),
        ])
        for run_id in ("core-run-1", "core-run-2", "core-run-3"):
            conn.execute(
                """INSERT INTO combat_snapshots VALUES
                   (?, ?, 18, '18.2', 'Vanessa', 'Legendary', 1, 1,
                    '2026-09-18T08:00:00', 2, 'Win', 1, NULL, NULL,
                    '[]', '[{"baseId":"ignored-skill"}]', '[]', '[]',
                    '2026-09-18T08:01:00')""",
                (run_id, f"{run_id}-day-1"),
            )
            conn.executemany(
                "INSERT INTO combat_player_cards VALUES (?, ?, ?, 'item', ?, ?, ?)",
                [
                    (run_id, f"{run_id}-day-1", "item-a", "Bronze", None, 0),
                    (run_id, f"{run_id}-day-1", "item-b", "Gold", "Fiery", 7),
                ],
            )

    build_day_stats(source, output, version_id="cores-v1")

    with sqlite3.connect(output) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT card_ids, support_runs, observable_runs, coverage, rank, min_support
               FROM early_day_item_cores
               WHERE version_id='cores-v1' AND season=18 AND phase='18.2'
                 AND hero='Vanessa' AND day=1
               ORDER BY rank"""
        ).fetchall()
    assert [dict(row) for row in rows] == [{
        "card_ids": '["item-a","item-b"]',
        "support_runs": 3,
        "observable_runs": 3,
        "coverage": 1.0,
        "rank": 1,
        "min_support": 2,
    }]


def test_core_discovery_dynamic_threshold_scopes_days_and_run_deduplication():
    from web_runs.day_stats_builder import _discover_early_day_item_cores

    def fact(run_id, items, *, hero="Vanessa", day=2, season=18, phase="18.2"):
        return {
            "snapshot": {
                "run_id": run_id, "season": season, "phase": phase,
                "hero": hero, "day": day,
            },
            "item_signature": json.dumps(sorted(items), separators=(",", ":")),
        }

    facts = [fact(f"run-{number}", ["common"] if number <= 3 else [f"rare-{number}"])
             for number in range(1, 12)]
    facts += [
        # Repeated input for one run cannot inflate either support or denominator.
        fact("run-1", ["common"]),
        # Separate hero scope has its own small-sample threshold and denominator.
        fact("pyg-1", ["hammer"], hero="Pygmalien"),
        fact("pyg-2", ["hammer"], hero="Pygmalien"),
        # Only early Days 1-3 are mined.
        fact("late-1", ["ignored"], day=4),
        fact("late-2", ["ignored"], day=4),
    ]

    rows = _discover_early_day_item_cores("v1", facts)
    simplified = {
        (row["hero"], row["day"], row["card_ids"]): (
            row["support_runs"], row["observable_runs"], row["min_support"], row["rank"]
        )
        for row in rows
    }
    assert simplified == {
        ("Vanessa", 2, '["common"]'): (3, 11, 3, 1),
        ("Pygmalien", 2, '["hammer"]'): (2, 2, 2, 1),
    }
    assert all(len(row["core_id"]) == 24 for row in rows)


def test_core_routes_are_per_day_and_use_day_observable_denominators():
    from web_runs.day_stats_builder import _build_item_core_routes

    def fact(run_id, day, items):
        return {
            "snapshot": {
                "run_id": run_id, "season": 18, "phase": "18.2",
                "hero": "Vanessa", "day": day,
            },
            "item_signature": json.dumps(sorted(items), separators=(",", ":")),
        }

    facts = [
        fact("r1", 1, ["a", "b", "c"]), fact("r1", 2, ["a", "b", "x"]),
        fact("r1", 3, ["a", "x"]), fact("r2", 1, ["a", "b"]),
        # Missing Day 2: the next observed snapshot is Day 3, not a failed transition.
        fact("r2", 3, ["a", "b", "y"]), fact("r3", 1, ["a", "b", "d"]),
        fact("r3", 2, ["a", "b", "d"]), fact("r4", 1, ["a", "b"]),
        fact("r4", 2, ["a", "b", "z"]),
        # Terminal at Day 1: excluded from the parent observable denominator.
        fact("r5", 1, ["a", "b"]),
        # Same scope but no starting core: never enters this route cohort.
        fact("outside", 1, ["a"]), fact("outside", 2, ["a", "b", "surprise"]),
    ]
    core = {
        "version_id": "routes-v1", "season": 18, "phase": "18.2",
        "hero": "Vanessa", "day": 1, "core_id": "core-ab",
        "card_ids": '["a","b"]', "support_runs": 5,
    }

    nodes, edges, node_members, edge_members = _build_item_core_routes([core], facts)
    by_node = {(row["day"], row["signature"]): row for row in nodes}

    assert set(by_node) == {
        (1, '["a","b"]'), (1, "__OTHER__"),
        (2, "__OTHER__"), (3, "__OTHER__"),
    }
    assert by_node[(2, "__OTHER__")] == {
        "version_id": "routes-v1", "core_id": "core-ab", "day": 2,
        "signature": "__OTHER__", "run_count": 3,
        "observable_runs": 3, "day_observable_rate": 1.0, "start_rate": 3 / 5,
        "added": "[]", "removed": "[]", "retained": "[]",
    }

    by_edge = {
        (row["parent_day"], row["parent_signature"],
         row["child_day"], row["child_signature"]): row for row in edges
    }
    skipped = by_edge[(1, '["a","b"]', 3, "__OTHER__")]
    assert skipped["run_count"] == 1
    assert skipped["observable_runs"] == 1
    assert skipped["transition_rate"] == 1.0
    direct = by_edge[(1, '["a","b"]', 2, "__OTHER__")]
    assert direct["observable_runs"] == 1
    assert direct["transition_rate"] == 1.0
    assert {row["run_id"] for row in node_members} == {"r2", "r4", "r5"}
    assert edge_members == []


def test_core_routes_keep_major_signatures_and_merge_the_long_tail():
    from web_runs.day_stats_builder import _build_item_core_routes

    def fact(run_id, day, items):
        return {
            "snapshot": {
                "run_id": run_id, "season": 18, "phase": "18.2",
                "hero": "Vanessa", "day": day,
            },
            "item_signature": json.dumps(sorted(items), separators=(",", ":")),
        }

    facts = []
    for number in range(1, 41):
        run_id = f"r-{number:02d}"
        facts.append(fact(run_id, 1, ["core"]))
        later = ["core", "main"] if number <= 20 else ["core", f"rare-{number:02d}"]
        facts.append(fact(run_id, 2, later))
    core = {
        "version_id": "routes-v2", "season": 18, "phase": "18.2",
        "hero": "Vanessa", "day": 1, "core_id": "core-one",
        "card_ids": '["core"]', "support_runs": 40,
    }

    nodes, edges, node_members, edge_members = _build_item_core_routes([core], facts)
    day_two = {row["signature"]: row for row in nodes if row["day"] == 2}

    assert set(day_two) == {'["core","main"]', "__OTHER__"}
    assert day_two['["core","main"]']["run_count"] == 20
    assert day_two["__OTHER__"]["run_count"] == 20
    assert day_two["__OTHER__"]["observable_runs"] == 40
    assert day_two["__OTHER__"]["day_observable_rate"] == 0.5
    assert len(node_members) == 60
    assert all(row["signature"] != "__OTHER__" for row in node_members)
    assert edge_members == []
    assert len(edges) == 2
    assert sum(row["run_count"] for row in edges) == 40


def test_core_route_node_count_has_a_fixed_per_day_ceiling():
    from web_runs.day_stats_builder import _build_item_core_routes

    facts = []
    for signature_number in range(20):
        for copy in range(2):
            run_id = f"r-{signature_number:02d}-{copy}"
            for day in (1, 2):
                facts.append({
                    "snapshot": {
                        "run_id": run_id, "season": 18, "phase": "18.2",
                        "hero": "Vanessa", "day": day,
                    },
                    "item_signature": json.dumps(
                        ["core", f"variant-{signature_number:02d}"], separators=(",", ":")
                    ),
                })
    core = {
        "version_id": "routes-v2", "season": 18, "phase": "18.2",
        "hero": "Vanessa", "day": 1, "core_id": "bounded-core",
        "card_ids": '["core"]', "support_runs": 40,
    }

    nodes, _edges, _node_members, _edge_members = _build_item_core_routes([core], facts)

    assert max(sum(row["day"] == day for row in nodes) for day in (1, 2)) <= 13


def test_daily_archetypes_assign_each_run_once_per_day_and_ignore_temporary_cards():
    from web_runs.day_stats_builder import _build_daily_archetype_routes

    def fact(run_id, day, items):
        return {
            "snapshot": {"run_id": run_id, "season": 18, "phase": "18.2",
                         "hero": "Vanessa", "day": day},
            "item_signature": json.dumps(sorted(items), separators=(",", ":")),
        }

    facts = []
    for number in range(20):
        facts += [
            fact(f"water-{number}", 3, ["fish", "net", f"temp-{number}"]),
            fact(f"water-{number}", 4, ["fish", "net", "engine", f"shop-{number}"]),
        ]
        facts += [
            fact(f"burn-{number}", 3, ["torch", "fuel", f"temp-{number}"]),
            fact(f"burn-{number}", 4, ["torch", "fuel", "boiler", f"shop-{number}"]),
        ]

    nodes, node_cards, members, edges, edge_cards, edge_members = _build_daily_archetype_routes(
        "daily-v1", facts
    )

    assert {row["day"] for row in nodes} == {3, 4}
    assert len([row for row in nodes if row["day"] == 3]) == 2
    assert len([row for row in nodes if row["day"] == 4]) == 2
    assert len({(row["day"], row["run_id"]) for row in members}) == 80
    assert all(sum(1 for row in members if row["day"] == day and row["run_id"] == run) == 1
               for day in (3, 4)
               for run in [f"water-{n}" for n in range(20)] + [f"burn-{n}" for n in range(20)])
    day3_cores = [set(json.loads(row["core_items"])) for row in nodes if row["day"] == 3]
    assert any({"fish", "net"} <= cards for cards in day3_cores)
    assert any({"torch", "fuel"} <= cards for cards in day3_cores)
    assert not any(row["card_id"].startswith("temp-") for row in node_cards)
    assert all(len(json.loads(row["core_items"])) <= 5 for row in nodes)
    assert all(not json.loads(row["core_items"]) or row["core_support_rate"] >= .50
               for row in nodes)
    assert sum(row["run_count"] for row in edges) == 40
    assert all(row["parent_day"] == 3 and row["child_day"] == 4 for row in edges)
    assert any(row["change_kind"] == "added_core" and row["card_id"] == "engine"
               for row in edge_cards)


def test_daily_route_directions_merge_similar_cores_filter_noise_and_limit_to_three():
    from web_runs.day_stats_builder import _build_daily_route_directions

    nodes = [
        {"node_id": "ab", "day": 4, "core_items": '["a","b"]'},
        {"node_id": "abc", "day": 5, "core_items": '["a","b","c"]'},
        {"node_id": "ax", "day": 4, "core_items": '["a","x"]'},
        {"node_id": "yz", "day": 4, "core_items": '["y","z"]'},
        {"node_id": "pq", "day": 4, "core_items": '["p","q"]'},
        {"node_id": "noise", "day": 4, "core_items": '["n"]'},
    ]
    edges = [
        {"edge_id": "e-ab", "parent_day": 3, "parent_node_id": "parent",
         "child_day": 4, "child_node_id": "ab", "run_count": 30,
         "parent_observable_runs": 100},
        {"edge_id": "e-abc", "parent_day": 3, "parent_node_id": "parent",
         "child_day": 5, "child_node_id": "abc", "run_count": 20,
         "parent_observable_runs": 100},
        {"edge_id": "e-ax", "parent_day": 3, "parent_node_id": "parent",
         "child_day": 4, "child_node_id": "ax", "run_count": 18,
         "parent_observable_runs": 100},
        {"edge_id": "e-yz", "parent_day": 3, "parent_node_id": "parent",
         "child_day": 4, "child_node_id": "yz", "run_count": 15,
         "parent_observable_runs": 100},
        {"edge_id": "e-pq", "parent_day": 3, "parent_node_id": "parent",
         "child_day": 4, "child_node_id": "pq", "run_count": 14,
         "parent_observable_runs": 100},
        {"edge_id": "e-noise", "parent_day": 3, "parent_node_id": "parent",
         "child_day": 4, "child_node_id": "noise", "run_count": 2,
         "parent_observable_runs": 100},
    ]

    result = _build_daily_route_directions(nodes, edges)
    directions = result["parent"]

    assert len(directions) == 3
    assert directions[0]["run_count"] == 50
    assert directions[0]["target_core_items"] == ["a", "b"]
    assert directions[0]["variant_count"] == 2
    assert directions[0]["arrival_days"] == [
        {"day": 4, "run_count": 30, "rate": 0.6},
        {"day": 5, "run_count": 20, "rate": 0.4},
    ]
    assert directions[0]["transition_rate"] == 0.5
    assert directions[0]["display_threshold"] == 3
    assert directions[0]["coverage_rate"] == 0.83
    assert all(direction["run_count"] >= 3 for direction in directions)
    assert not any("n" in direction["target_core_items"] for direction in directions)


def test_daily_route_directions_use_smaller_core_containment_without_chain_merging():
    from web_runs.day_stats_builder import _build_daily_route_directions
    nodes = [
        {"node_id": "one", "day": 4, "core_items": ["a"]},
        {"node_id": "five", "day": 4, "core_items": ["a", "b", "c", "d", "e"]},
        {"node_id": "bridge", "day": 4, "core_items": ["b", "c", "d", "e", "f"]},
    ]
    edges = [
        {"edge_id": "one-edge", "parent_node_id": "parent", "child_node_id": "one", "child_day": 4, "run_count": 40, "parent_observable_runs": 100},
        {"edge_id": "five-edge", "parent_node_id": "parent", "child_node_id": "five", "child_day": 4, "run_count": 30, "parent_observable_runs": 100},
        {"edge_id": "bridge-edge", "parent_node_id": "parent", "child_node_id": "bridge", "child_day": 4, "run_count": 20, "parent_observable_runs": 100},
    ]
    directions = _build_daily_route_directions(nodes, edges)["parent"]
    assert [row["run_count"] for row in directions] == [70, 20]
    assert directions[0]["target_core_items"] == ["a"]
    assert directions[1]["target_core_items"] == ["b", "c", "d", "e", "f"]


def test_daily_route_direction_details_choose_real_representative_and_rank_non_core_cards():
    from web_runs.day_stats_builder import _build_daily_route_direction_details

    direction = {
        "target_core_items": ["a", "b"],
        "variants": [
            {"edge_id": "e1", "child_day": 4, "child_node_id": "n1"},
            {"edge_id": "e2", "child_day": 5, "child_node_id": "n2"},
        ],
    }
    edge_members = {
        "e1": {"r1", "r2", "r3"},
        "e2": {"r4", "r5"},
    }
    run_days = {
        "r1": {4: {"a", "b", "x", "y"}},
        "r2": {4: {"a", "b", "x"}},
        "r3": {4: {"a", "b", "x", "z"}},
        "r4": {5: {"a", "b", "y"}},
        "r5": {5: {"a", "b", "x", "y"}},
    }

    detail = _build_daily_route_direction_details(direction, edge_members, run_days)

    assert detail["representative_run_id"] in {"r1", "r5"}
    assert detail["representative_day"] in {4, 5}
    assert set(detail["representative_items"]) == {"a", "b", "x", "y"}
    assert detail["associated_cards"][0] == {
        "card_id": "x", "support_runs": 4, "support_rate": 0.8,
    }
    assert all(row["card_id"] not in {"a", "b"} for row in detail["associated_cards"])


def test_daily_routes_connect_next_observed_day_without_inventing_missing_days():
    from web_runs.day_stats_builder import _build_daily_archetype_routes

    facts = [
        {"snapshot": {"run_id": "r1", "season": 18, "phase": "18.2",
                      "hero": "Vanessa", "day": 3}, "item_signature": '["a"]'},
        {"snapshot": {"run_id": "r1", "season": 18, "phase": "18.2",
                      "hero": "Vanessa", "day": 5}, "item_signature": '["a","b"]'},
        {"snapshot": {"run_id": "r2", "season": 18, "phase": "18.2",
                      "hero": "Vanessa", "day": 3}, "item_signature": '["a"]'},
        {"snapshot": {"run_id": "r2", "season": 18, "phase": "18.2",
                      "hero": "Vanessa", "day": 4}, "item_signature": '["a","b"]'},
    ]

    _nodes, _cards, _members, edges, _edge_cards, _edge_members = _build_daily_archetype_routes(
        "daily-v2", facts
    )

    assert {(row["parent_day"], row["child_day"], row["stage_gap"], row["run_count"])
            for row in edges} == {(3, 4, 0, 1), (3, 5, 1, 1)}
    assert {row["parent_observable_runs"] for row in edges} == {2}
    assert {row["transition_rate"] for row in edges} == {0.5}
    assert sum(row["transition_rate"] for row in edges) == 1.0


def test_stage_archetypes_assign_each_run_once_and_build_real_paths():
    from web_runs.day_stats_builder import _build_stage_archetype_routes

    def fact(run_id, day, items):
        return {
            "snapshot": {
                "run_id": run_id, "season": 18, "phase": "18.2",
                "hero": "Vanessa", "day": day,
            },
            "item_signature": json.dumps(sorted(items), separators=(",", ":")),
        }

    facts = []
    for number in range(20):
        run = f"water-{number}"
        facts += [fact(run, 3, ["fish", "net", f"temp-{number}"]),
                  fact(run, 7, ["fish", "engine"]),
                  fact(run, 11, ["engine", "finisher"])]
    for number in range(20):
        run = f"burn-{number}"
        facts += [fact(run, 3, ["torch", "fuel", f"temp-{number}"]),
                  fact(run, 7, ["torch", "boiler"]),
                  fact(run, 11, ["boiler", "dragon"])]

    nodes, node_cards, members, edges, edge_cards, paths = _build_stage_archetype_routes(
        "stage-v1", facts
    )

    assert {row["stage"] for row in nodes} == {"early", "middle", "late"}
    assert len([row for row in nodes if row["stage"] == "early"]) == 2
    assert len({(row["stage"], row["run_id"]) for row in members}) == 120
    assert all(sum(1 for row in members if row["stage"] == stage and row["run_id"] == run) == 1
               for stage in ("early", "middle", "late")
               for run in [f"water-{n}" for n in range(20)] + [f"burn-{n}" for n in range(20)])
    early_representatives = [set(json.loads(row["representative_items"]))
                             for row in nodes if row["stage"] == "early"]
    early_cores = [set(json.loads(row["core_items"]))
                   for row in nodes if row["stage"] == "early"]
    assert any({"fish", "net"} <= cards for cards in early_cores)
    assert any({"torch", "fuel"} <= cards for cards in early_cores)
    assert any(cards & {"fish", "net"} for cards in early_representatives)
    assert any(cards & {"torch", "fuel"} for cards in early_representatives)
    assert sum(row["run_count"] for row in edges if row["parent_stage"] == "early") == 40
    assert sum(row["run_count"] for row in paths) == 40
    assert len(paths) == 2
    assert any(row["change_kind"] == "removed_core" and row["card_id"] == "fish"
               for row in edge_cards)
    assert any(row["change_kind"] == "added_core" and row["card_id"] == "finisher"
               for row in edge_cards)


def test_stage_archetypes_use_stage_end_state_and_have_fixed_node_ceiling():
    from web_runs.day_stats_builder import _build_stage_archetype_routes

    facts = []
    for number in range(80):
        run = f"r-{number}"
        for day, items in (
            (1, ["obsolete"]),
            (3, [f"family-{number % 4}", f"noise-{number}"]),
            (4, ["middle-obsolete"]),
            (7, [f"mid-{number % 3}"]),
            (11, [f"late-{number % 2}"]),
        ):
            facts.append({
                "snapshot": {"run_id": run, "season": 18, "phase": "18.2",
                             "hero": "Vanessa", "day": day},
                "item_signature": json.dumps(sorted(items), separators=(",", ":")),
            })

    nodes, node_cards, members, _edges, _edge_cards, paths = _build_stage_archetype_routes(
        "stage-v2", facts
    )

    assert max(sum(row["stage"] == stage for row in nodes)
               for stage in ("early", "middle", "late")) <= 6
    assert all(row["max_snapshot_day"] == 3 for row in nodes if row["stage"] == "early")
    assert not any(row["card_id"] == "obsolete" for row in node_cards if row["stage"] == "early")
    assert not any(row["card_id"] == "middle-obsolete" for row in node_cards if row["stage"] == "middle")
    assert len({(row["stage"], row["run_id"]) for row in members}) == 240
    assert sum(row["run_count"] for row in paths) == 80


def test_build_persists_versioned_core_route_nodes_edges_and_memberships(tmp_path):
    from web_runs.day_stats_builder import build_day_stats

    source = tmp_path / "source.db"
    output = tmp_path / "day-stats.db"
    _create_source(source)
    with sqlite3.connect(source) as conn:
        conn.executemany("INSERT INTO snapshot_jobs VALUES (?, 'success')", [
            ("route-1",), ("route-2",),
        ])
        for run_id, later_day, later_item in (
            ("route-1", 2, "x"), ("route-2", 3, "y"),
        ):
            for day, items in ((1, ["a", "b"]), (later_day, ["a", "b", later_item])):
                event_id = f"{run_id}-day-{day}"
                conn.execute(
                    """INSERT INTO combat_snapshots VALUES
                       (?, ?, 18, '18.2', 'Vanessa', 'Legendary', ?, 1,
                        '2026-09-18T08:00:00', 2, 'Win', 1, NULL, NULL,
                        '[]', '[{"baseId":"detail-only-skill"}]', '[]', '[]',
                        '2026-09-18T08:01:00')""",
                    (run_id, event_id, day),
                )
                conn.executemany(
                    "INSERT INTO combat_player_cards VALUES (?, ?, ?, 'item', NULL, NULL, ?)",
                    [(run_id, event_id, item, slot) for slot, item in enumerate(items)],
                )

    build_day_stats(source, output, version_id="routes-db-v1")

    with sqlite3.connect(output) as conn:
        conn.row_factory = sqlite3.Row
        core_id = conn.execute(
            "SELECT core_id FROM early_day_item_cores WHERE version_id='routes-db-v1' AND card_ids='[\"a\",\"b\"]'"
        ).fetchone()[0]
        assert conn.execute(
            "SELECT COUNT(*) FROM item_core_route_nodes WHERE version_id='routes-db-v1' AND core_id=?",
            (core_id,),
        ).fetchone()[0] == 3
        edges = conn.execute(
            """SELECT parent_day, child_day, run_count, observable_runs, transition_rate
               FROM item_core_route_edges
               WHERE version_id='routes-db-v1' AND core_id=? ORDER BY child_day""",
            (core_id,),
        ).fetchall()
        assert [tuple(row) for row in edges] == [(1, 2, 1, 1, 1.0), (1, 3, 1, 1, 1.0)]
        assert conn.execute(
            "SELECT COUNT(*) FROM item_core_route_node_runs WHERE version_id='routes-db-v1' AND core_id=?",
            (core_id,),
        ).fetchone()[0] == 2
        assert conn.execute(
            "SELECT COUNT(*) FROM item_core_route_edge_runs WHERE version_id='routes-db-v1' AND core_id=?",
            (core_id,),
        ).fetchone()[0] == 0
        # Skills remain detail only and cannot appear in any item route signature.
        assert conn.execute(
            "SELECT COUNT(*) FROM item_core_route_nodes WHERE signature LIKE '%detail-only-skill%'"
        ).fetchone()[0] == 0
