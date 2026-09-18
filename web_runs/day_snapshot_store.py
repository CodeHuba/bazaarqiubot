"""Independent SQLite store for Bazaar game-Day composition snapshots."""

from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path


_RETRY_DELAYS_MINUTES = (5, 15, 60, 180)


class DaySnapshotStore:
    def __init__(self, path: str | Path):
        self.path = str(path)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.execute("PRAGMA busy_timeout=30000")
        conn.row_factory = sqlite3.Row
        return conn

    def initialize(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS snapshot_jobs (
                    run_id TEXT PRIMARY KEY,
                    season INTEGER NOT NULL,
                    phase TEXT NOT NULL,
                    hero TEXT,
                    player_rank TEXT NOT NULL,
                    stat_wins INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    queued_at TEXT NOT NULL,
                    next_attempt_at TEXT NOT NULL,
                    started_at TEXT,
                    lease_token TEXT,
                    finished_at TEXT,
                    updated_at TEXT NOT NULL,
                    last_error TEXT,
                    last_http_status INTEGER,
                    source TEXT NOT NULL DEFAULT 'bazaardb-rsc'
                );
                CREATE INDEX IF NOT EXISTS idx_snapshot_jobs_claim
                    ON snapshot_jobs(status, next_attempt_at, queued_at);
                CREATE INDEX IF NOT EXISTS idx_snapshot_jobs_scope
                    ON snapshot_jobs(season, phase, player_rank, stat_wins, status);

                CREATE TABLE IF NOT EXISTS combat_snapshots (
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
                ) WITHOUT ROWID;
                CREATE INDEX IF NOT EXISTS idx_combat_snapshot_scope_day_run
                    ON combat_snapshots(season, phase, day, hero, player_rank, run_id);
                CREATE INDEX IF NOT EXISTS idx_combat_snapshot_run_day
                    ON combat_snapshots(run_id, day, hour);

                CREATE TABLE IF NOT EXISTS combat_player_cards (
                    run_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    card_id TEXT NOT NULL,
                    card_kind TEXT NOT NULL,
                    tier TEXT,
                    enchantment TEXT,
                    slot_position INTEGER NOT NULL DEFAULT -1,
                    PRIMARY KEY (run_id, event_id, card_id, card_kind, slot_position)
                ) WITHOUT ROWID;
                CREATE INDEX IF NOT EXISTS idx_combat_player_cards_card_event
                    ON combat_player_cards(card_id, card_kind, run_id, event_id);

                CREATE TABLE IF NOT EXISTS snapshot_fetch_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    attempted_at TEXT NOT NULL,
                    success INTEGER NOT NULL,
                    http_status INTEGER,
                    elapsed_ms INTEGER,
                    error_type TEXT,
                    error_message TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_snapshot_attempts_run_time
                    ON snapshot_fetch_attempts(run_id, attempted_at);
                """
            )
            job_columns = {row[1] for row in conn.execute("PRAGMA table_info(snapshot_jobs)")}
            if "lease_token" not in job_columns:
                conn.execute("ALTER TABLE snapshot_jobs ADD COLUMN lease_token TEXT")

    @staticmethod
    def eligible(player_rank: object, stat_wins: object) -> bool:
        return player_rank == "Legendary" and stat_wins == 10

    def enqueue_if_eligible(
        self,
        run_id: str,
        season: int,
        phase: str,
        hero: str | None,
        player_rank: object,
        stat_wins: object,
        *,
        now: str | None = None,
    ) -> bool:
        if not run_id or not self.eligible(player_rank, stat_wins):
            return False
        now = now or datetime.now().isoformat()
        with self.connect() as conn:
            before = conn.total_changes
            conn.execute(
                """INSERT OR IGNORE INTO snapshot_jobs
                   (run_id, season, phase, hero, player_rank, stat_wins,
                    queued_at, next_attempt_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (run_id, season, phase, hero, player_rank, stat_wins, now, now, now),
            )
            return conn.total_changes > before

    def backfill_current_phase(self, runs_path: str | Path, season: int, phase: str) -> dict[str, int]:
        # Read and close the main Runs database before starting the independent
        # snapshot database write transaction. This avoids cross-database locks.
        with sqlite3.connect(str(runs_path), timeout=30) as runs_conn:
            rows = runs_conn.execute(
                """SELECT id, hero, player_rank, stat_wins FROM runs
                   WHERE season=? AND phase=? AND player_rank='Legendary' AND stat_wins=10
                   ORDER BY id""",
                (season, phase),
            ).fetchall()
        now = datetime.now().isoformat()
        with self.connect() as conn:
            before = conn.total_changes
            conn.executemany(
                """INSERT OR IGNORE INTO snapshot_jobs
                   (run_id, season, phase, hero, player_rank, stat_wins,
                    queued_at, next_attempt_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [(run_id, season, phase, hero, rank, wins, now, now, now)
                 for run_id, hero, rank, wins in rows],
            )
            created = conn.total_changes - before
        return {"eligible": len(rows), "created": created}

    def status(self) -> dict:
        with self.connect() as conn:
            jobs = {row["status"]: row["count"] for row in conn.execute(
                "SELECT status, COUNT(*) AS count FROM snapshot_jobs GROUP BY status"
            )}
            snapshot_count = conn.execute("SELECT COUNT(*) FROM combat_snapshots").fetchone()[0]
            run_count = conn.execute("SELECT COUNT(DISTINCT run_id) FROM combat_snapshots").fetchone()[0]
            attempt_count = conn.execute("SELECT COUNT(*) FROM snapshot_fetch_attempts").fetchone()[0]
            success_attempts = conn.execute(
                "SELECT COUNT(*) FROM snapshot_fetch_attempts WHERE success=1"
            ).fetchone()[0]
        return {
            "jobs": jobs,
            "snapshots": snapshot_count,
            "runs_with_snapshots": run_count,
            "attempts": attempt_count,
            "successful_attempts": success_attempts,
        }

    def claim(
        self,
        limit: int = 20,
        *,
        now: str | None = None,
        lease_minutes: int = 90,
        max_attempts: int = 5,
    ) -> list[dict]:
        now_dt = datetime.fromisoformat(now) if now else datetime.now()
        now_text = now_dt.isoformat()
        lease_cutoff = (now_dt - timedelta(minutes=lease_minutes)).isoformat()
        claimed: list[dict] = []
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """UPDATE snapshot_jobs SET status='retry', next_attempt_at=?,
                       updated_at=?, last_error='processing lease expired', lease_token=NULL
                   WHERE status='processing' AND started_at < ? AND attempts < ?""",
                (now_text, now_text, lease_cutoff, max_attempts),
            )
            conn.execute(
                """UPDATE snapshot_jobs SET status='failed', finished_at=?, updated_at=?,
                       lease_token=NULL
                   WHERE status IN ('pending','retry') AND attempts >= ?
                      OR (status='processing' AND started_at < ? AND attempts >= ?)""",
                (now_text, now_text, max_attempts, lease_cutoff, max_attempts),
            )
            rows = conn.execute(
                """SELECT run_id, attempts FROM snapshot_jobs
                   WHERE status IN ('pending','retry') AND attempts < ?
                     AND next_attempt_at <= ?
                   ORDER BY queued_at, run_id LIMIT ?""",
                (max_attempts, now_text, max(1, min(int(limit), 100))),
            ).fetchall()
            for row in rows:
                lease_token = secrets.token_urlsafe(24)
                updated = conn.execute(
                    """UPDATE snapshot_jobs SET status='processing', attempts=attempts+1,
                           started_at=?, updated_at=?, last_error=NULL, lease_token=?
                       WHERE run_id=? AND status IN ('pending','retry') AND attempts=?""",
                    (now_text, now_text, lease_token, row["run_id"], row["attempts"]),
                ).rowcount
                if updated:
                    claimed.append({"run_id": row["run_id"], "attempts": row["attempts"] + 1,
                                    "lease_token": lease_token})
            conn.commit()
            return claimed
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _processing_job(self, conn: sqlite3.Connection, run_id: str,
                        lease_token: str) -> sqlite3.Row:
        row = conn.execute(
            """SELECT * FROM snapshot_jobs
               WHERE run_id=? AND status='processing' AND lease_token=?""",
            (run_id, lease_token),
        ).fetchone()
        if row is None:
            raise ValueError("snapshot job lease is stale or not claimed")
        return row

    def complete_failure(
        self,
        run_id: str,
        error: str,
        lease_token: str,
        *,
        now: str | None = None,
        http_status: int | None = None,
        elapsed_ms: int | None = None,
        error_type: str | None = None,
        max_attempts: int = 5,
    ) -> str:
        now_dt = datetime.fromisoformat(now) if now else datetime.now()
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            job = self._processing_job(conn, run_id, lease_token)
            attempts = int(job["attempts"])
            terminal = attempts >= max_attempts
            status = "failed" if terminal else "retry"
            delay_index = min(max(attempts - 1, 0), len(_RETRY_DELAYS_MINUTES) - 1)
            next_attempt = (now_dt + timedelta(minutes=_RETRY_DELAYS_MINUTES[delay_index])).isoformat()
            updated = conn.execute(
                """UPDATE snapshot_jobs SET status=?, last_error=?, last_http_status=?,
                       next_attempt_at=?, finished_at=?, updated_at=?, lease_token=NULL
                   WHERE run_id=? AND status='processing' AND lease_token=?""",
                (status, str(error)[:1000], http_status, next_attempt,
                 now_dt.isoformat() if terminal else None, now_dt.isoformat(), run_id, lease_token),
            ).rowcount
            if updated != 1:
                raise ValueError("snapshot job lease is stale or not claimed")
            conn.execute(
                """INSERT INTO snapshot_fetch_attempts
                   (run_id, attempted_at, success, http_status, elapsed_ms, error_type, error_message)
                   VALUES (?, ?, 0, ?, ?, ?, ?)""",
                (run_id, now_dt.isoformat(), http_status, elapsed_ms, error_type, str(error)[:1000]),
            )
            conn.commit()
            return status
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def complete_success(
        self,
        run_id: str,
        snapshots: list[dict],
        lease_token: str,
        *,
        now: str | None = None,
        http_status: int | None = 200,
        elapsed_ms: int | None = None,
    ) -> None:
        if not snapshots:
            raise ValueError("combat snapshots cannot be empty")
        now = now or datetime.now().isoformat()
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            job = self._processing_job(conn, run_id, lease_token)
            valid_snapshots = []
            for snapshot in snapshots:
                if not isinstance(snapshot, dict):
                    continue
                event_id = snapshot.get("eventId")
                day = snapshot.get("day")
                if isinstance(event_id, str) and 0 < len(event_id) <= 128 \
                        and isinstance(day, int) and not isinstance(day, bool) and day >= 1:
                    valid_snapshots.append(snapshot)
            if not valid_snapshots:
                raise ValueError("combat snapshots contain no valid eventId/day entries")
            for snapshot in valid_snapshots:
                event_id = snapshot["eventId"]
                day = snapshot["day"]
                combat = snapshot.get("combat") or {}
                conn.execute("DELETE FROM combat_player_cards WHERE run_id=? AND event_id=?",
                             (run_id, event_id))
                conn.execute(
                    """INSERT OR REPLACE INTO combat_snapshots
                       (run_id,event_id,season,phase,hero,player_rank,day,hour,captured_at,
                        player_level,outcome,is_pvp,opponent_hero,opponent_name,
                        player_board_json,player_skills_json,opponent_board_json,
                        opponent_skills_json,fetched_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (run_id, event_id, job["season"], job["phase"], job["hero"],
                     job["player_rank"], day, snapshot.get("hour"), snapshot.get("ts"),
                     snapshot.get("playerLevel"), combat.get("outcome"),
                     int(bool(combat.get("is_pvp"))), combat.get("opponent_hero"),
                     combat.get("opponent_name"),
                     json.dumps(snapshot.get("playerBoard", []), ensure_ascii=False),
                     json.dumps(snapshot.get("playerSkills", []), ensure_ascii=False),
                     json.dumps(snapshot.get("opponentBoard", []), ensure_ascii=False),
                     json.dumps(snapshot.get("opponentSkills", []), ensure_ascii=False), now),
                )
                for card_kind, cards in (("item", snapshot.get("playerBoard", [])),
                                         ("skill", snapshot.get("playerSkills", []))):
                    for card in cards:
                        if not isinstance(card, dict):
                            continue
                        card_id = card.get("baseId") or card.get("cardId")
                        if not card_id:
                            continue
                        conn.execute(
                            """INSERT OR REPLACE INTO combat_player_cards
                               (run_id,event_id,card_id,card_kind,tier,enchantment,slot_position)
                               VALUES (?,?,?,?,?,?,?)""",
                            (run_id, event_id, card_id, card_kind, card.get("tierOverride"),
                             card.get("enchantmentOverride"),
                             card.get("slotPosition") if card.get("slotPosition") is not None else -1),
                        )
            updated = conn.execute(
                """UPDATE snapshot_jobs SET status='success', last_error=NULL,
                       last_http_status=?, finished_at=?, updated_at=?, lease_token=NULL
                   WHERE run_id=? AND status='processing' AND lease_token=?""",
                (http_status, now, now, run_id, lease_token),
            ).rowcount
            if updated != 1:
                raise ValueError("snapshot job lease is stale or not claimed")
            conn.execute(
                """INSERT INTO snapshot_fetch_attempts
                   (run_id, attempted_at, success, http_status, elapsed_ms)
                   VALUES (?, ?, 1, ?, ?)""",
                (run_id, now, http_status, elapsed_ms),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
