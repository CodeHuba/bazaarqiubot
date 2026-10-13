"""Small, read-only public API for Zaza Space."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import uuid
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from contextlib import closing
from pathlib import Path
from typing import Any

from flask import Blueprint, jsonify, request

_PUBLIC_USER = "Puffy"
_PROFILE_ID = "2d4bdb0f-f103-4b1c-9ef7-df15c73860d3"


def _next_odd_hour_sync(now: datetime | None = None) -> str:
    """Next strictly future Beijing odd-hour boundary, stored in UTC."""
    current = now or datetime.now(timezone.utc)
    local = current.astimezone(timezone(timedelta(hours=8)))
    candidate = local.replace(minute=0, second=0, microsecond=0)
    candidate += timedelta(hours=1 if candidate.hour % 2 == 0 else 2)
    return candidate.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def _key(season: int, phase: str, run_id: Any) -> str:
    return hashlib.sha256(f"zaza:{season}:{phase}:{run_id}".encode()).hexdigest()[:32]


def _json(value: Any, default: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return default
    return value if value is not None else default


def _card_id(card: Any) -> str | None:
    if isinstance(card, str): return card
    if not isinstance(card, dict): return None
    return card.get("cardId") or card.get("baseId") or card.get("card_id") or card.get("id")


_INSTANCE_FIELDS = ("tier", "isLegendary", "slotPosition")


def _instance_fields(card: Any) -> dict[str, Any]:
    if not isinstance(card, dict):
        return {}
    result = {}
    if "tier" in card:
        result["tier"] = card["tier"]
    elif "tierOverride" in card:
        result["tier"] = card["tierOverride"]
    for key in ("isLegendary", "slotPosition"):
        if key in card:
            result[key] = card[key]
    return result


def _final_cards(row: dict, kind: str) -> list:
    """Enrich compact terminal cards only from matching raw instances.

    Explicit values (including null) win. Ambiguous duplicate cards contribute
    only fields shared by every matching instance; static card tiers are unused.
    """
    raw = _json(row.get('raw_json'), {})
    raw = raw if isinstance(raw, dict) else {}
    originals = raw.get(kind, [])
    originals = originals if isinstance(originals, list) else []
    cards = _json(row.get(kind + '_json'), None)
    if cards is None:
        cards = row.get(kind) if isinstance(row.get(kind), list) else originals
    if not isinstance(cards, list):
        return []
    def identity(card):
        return _card_id(card) or (card.get('skillId') if isinstance(card, dict) else None)
    by_id = defaultdict(list)
    for card in originals:
        if isinstance(card, dict) and identity(card) is not None:
            by_id[identity(card)].append(card)
    result = []
    for card in cards:
        cid = identity(card)
        if cid is None:
            continue
        enriched = dict(card) if isinstance(card, dict) else {'cardId': cid}
        matches = by_id.get(cid, [])
        if enriched.get('slotPosition') is not None:
            matches = [candidate for candidate in matches if candidate.get('slotPosition') == enriched['slotPosition']]
        for field in _INSTANCE_FIELDS:
            if field not in enriched and matches and all(field in candidate for candidate in matches):
                value = matches[0][field]
                if all(candidate[field] == value for candidate in matches):
                    enriched[field] = value
        result.append(enriched)
    return result


def _display(card: Any, resolver=None) -> dict[str, Any]:
    card_id = _card_id(card)
    if card_id is None: return {}
    result = {"card_id": card_id, **_instance_fields(card)}
    if isinstance(card, dict):
        for source, target in (("title", "name"), ("name", "name"), ("size", "size"), ("art", "art"), ("img", "img")):
            if card.get(source) is not None: result[target] = card[source]
    if resolver:
        try:
            fn = resolver if callable(resolver) else resolver.card_display_info_cached
            info = fn(card_id)
            if isinstance(info, dict):
                result.update({k: info[k] for k in ("art", "img", "name", "name_en", "size") if k in info})
        except Exception:
            pass
    return result


def _snapshot_day(s: dict[str, Any]) -> int | None:
    try: return int(s.get("day"))
    except (TypeError, ValueError): return None


def _snapshot_hour(s: dict[str, Any]) -> int:
    try: return int(s.get("hour", -1))
    except (TypeError, ValueError): return -1


def _snapshot_order(s: dict[str, Any]) -> tuple[int, str, str]:
    return (_snapshot_hour(s), str(s.get("ts") or s.get("timestamp") or ""), str(s.get("eventId") or s.get("event_id") or ""))


class _ArtifactError(Exception):
    pass


def _create_legacy_zaza_blueprint(runs_path, data_path, season, phase, card_resolver=None):
    blueprint = Blueprint("zaza_space", __name__)
    season, phase = int(season), str(phase)

    def path(value, env):
        value = value() if callable(value) else value
        return Path(str(value or os.environ.get(env, "")))

    def load_runs() -> list[dict[str, Any]]:
        artifact_path = path(data_path, "ZAZA_DATA_PATH")
        try:
            raw = json.loads(artifact_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            raise _ArtifactError("artifact unavailable") from exc
        if not isinstance(raw, dict) or raw.get("season") not in (None, season) or str(raw.get("phase", "")) != phase:
            raise _ArtifactError("artifact season/phase mismatch")
        # Identity is an ID, never a username.  A manifest is accepted only as a
        # trusted identity assertion when the source rows do not carry one.
        artifact_profile = raw.get("profileId") or (raw.get("manifest") or {}).get("profileId")
        rows = raw.get("runs")
        if not isinstance(rows, list): raise _ArtifactError("invalid artifact schema")
        ids = [str(r["id"]) for r in rows if isinstance(r, dict) and r.get("id")]
        if not ids: return []
        db_rows: dict[str, dict[str, Any]] = {}
        db_path = path(runs_path, "RUNS_DB_PATH")
        try:
            with sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True) as conn:
                cols = {r[1] for r in conn.execute("PRAGMA table_info(runs)")}
                if "id" not in cols: raise _ArtifactError("runs schema missing id")
                wanted = [x for x in ("id", "hero", "created_at", "stat_wins", "stat_losses", "items_json", "profileId", "raw_json", "season", "phase") if x in cols]
                marks = ",".join("?" for _ in ids)
                for row in conn.execute("SELECT " + ",".join(wanted) + f" FROM runs WHERE id IN ({marks})", ids):
                    item = dict(zip(wanted, row))
                    if item.get("season") not in (None, season) or str(item.get("phase", phase)) != phase: continue
                    profile = item.get("profileId")
                    if not profile and item.get("raw_json"):
                        profile = _json(item["raw_json"], {}).get("profileId")
                    if profile != _PROFILE_ID and not (not profile and artifact_profile == _PROFILE_ID): continue
                    db_rows[str(item["id"])] = item
        except _ArtifactError:
            raise
        except (sqlite3.Error, OSError) as exc:
            raise _ArtifactError("runs database unavailable") from exc
        result = []
        for row in rows:
            if not isinstance(row, dict) or not row.get("id") or str(row["id"]) not in db_rows: continue
            merged = dict(db_rows[str(row["id"])])
            merged.update(row)
            merged["snapshots"] = [s for s in (merged.get("snapshots") or []) if isinstance(s, dict) and _snapshot_day(s) is not None and _snapshot_hour(s) == 6 and (s.get('combat') or {}).get('is_pvp') in (True, 1, 'true', '1')]
            result.append(merged)
        return sorted(result, key=lambda r: (str(r.get("created_at") or ""), str(r.get("id"))), reverse=True)

    def public_items(items):
        return [_display(x, card_resolver) for x in (items or []) if _card_id(x) is not None]

    def public_skills(skills):
        out = []
        for skill in skills or []:
            sid = _card_id(skill) or (skill.get("skillId") if isinstance(skill, dict) else None)
            if not sid: continue
            info = None
            try:
                fn = getattr(card_resolver, "skill_display_info_cached", None) or getattr(card_resolver, "resolve_skill", None)
                if fn: info = fn(sid)
            except Exception: info = None
            item = {"skill_id": sid, **_instance_fields(skill)}
            if isinstance(info, dict): item.update({k: info[k] for k in ("name", "name_en", "img", "art", "description") if k in info})
            elif isinstance(skill, dict) and skill.get("title"): item["name"] = skill["title"]
            out.append(item)
        return out

    def public_days(run):
        chosen = {}
        for snap in run.get("snapshots", []):
            d = _snapshot_day(snap)
            if d is not None and (d not in chosen or _snapshot_order(snap) > _snapshot_order(chosen[d])): chosen[d] = snap
        return [{"day": d, "hour": _snapshot_hour(s), "outcome": (s.get("combat") or {}).get("outcome"),
                 "items": public_items(s.get("playerBoard") or s.get("player_board") or []),
                 "skills": public_skills(s.get("playerSkills") or s.get("player_skills") or [])}
                for d, s in sorted(chosen.items())]

    def user_ok():
        return request.args.get("username", "").casefold() == _PUBLIC_USER.casefold()

    @blueprint.get("/api/zaza/users")
    def users():
        if request.args.get("username") is not None and not user_ok(): return jsonify({"error": "unknown user"}), 404
        return jsonify({"users": [_PUBLIC_USER]})

    @blueprint.get("/api/zaza/runs")
    def runs():
        if not user_ok(): return jsonify({"error": "unknown user"}), 404
        try: loaded = load_runs()
        except _ArtifactError as exc: return jsonify({"error": str(exc)}), 503
        output = [{"run_key": _key(season, phase, r["id"]), "hero": r.get("hero"), "time": r.get("created_at"),
                   "wins": r.get("stat_wins"), "losses": r.get("stat_losses"), "items": public_items(_final_cards(r, 'items')),
                   "day_count": len(public_days(r)), "snapshot_status": "available" if r.get("snapshots") else "missing"} for r in loaded]
        return jsonify({"username": _PUBLIC_USER, "season": season, "phase": phase, "total": len(output), "runs": output})

    def get_run(run_key):
        try: return next((r for r in load_runs() if _key(season, phase, r.get("id")) == run_key), None)
        except _ArtifactError: return None

    @blueprint.get("/api/zaza/runs/<run_key>/days")
    def days(run_key):
        try: run = next((r for r in load_runs() if _key(season, phase, r.get("id")) == run_key), None)
        except _ArtifactError as exc: return jsonify({"error": str(exc)}), 503
        if run is None: return jsonify({"error": "unknown run"}), 404
        return jsonify({"run_key": run_key, "days": public_days(run)})

    @blueprint.get("/api/zaza/routes")
    def routes():
        if not user_ok(): return jsonify({"error": "unknown user"}), 404
        hero_filter = request.args.get("hero")
        try: loaded = load_runs()
        except _ArtifactError as exc: return jsonify({"error": str(exc)}), 503
        from web_runs.day_stats_builder import _cluster_daily_compositions
        scopes = defaultdict(dict)  # (hero, day) -> run -> board
        boards = defaultdict(dict)
        for run in loaded:
            hero = run.get("hero")
            if hero_filter and hero != hero_filter: continue
            rk = _key(season, phase, run["id"])
            for snap in run.get("snapshots", []):
                day = _snapshot_day(snap); items = [_card_id(x) for x in (snap.get("playerBoard") or snap.get("player_board") or [])]
                items = [x for x in items if x]
                if day is not None and items and (rk not in scopes[(hero, day)] or _snapshot_order(snap) > scopes[(hero, day)][rk][0]): scopes[(hero, day)][rk] = (_snapshot_order(snap), frozenset(items))
        nodes, assignment = [], {}
        for (hero, day), values in sorted(scopes.items(), key=lambda x: (str(x[0][0]), x[0][1])):
            snapshots = {rk: board for rk, (_, board) in values.items()}
            clusters = _cluster_daily_compositions(snapshots)
            assigned = set()
            for cluster in clusters:
                nid = hashlib.sha256(json.dumps([season, phase, hero or "", day, cluster["core_items"]], sort_keys=True).encode()).hexdigest()[:24]
                for rk in cluster["runs"]: assignment[(rk, day)] = nid; assigned.add(rk)
                reps = [snapshots[rk] for rk in cluster["runs"] if rk in snapshots]
                representative = min(reps, key=lambda b: (tuple(sorted(b)), len(b)))
                nodes.append({"node_id": nid, "day": day, "hero": hero, "run_count": len(cluster["runs"]), "sample_count": len(cluster["runs"]), "core_items": [_display(x, card_resolver) for x in sorted(cluster["core_items"])], "items": [_display(x, card_resolver) for x in sorted(representative)]})
            # Builder intentionally drops below-support families; retain each exact
            # signature, but do not invent a core for it.
            by_sig = defaultdict(list)
            for rk, board in snapshots.items():
                if rk not in assigned: by_sig[tuple(sorted(board))].append(rk)
            for sig, members in sorted(by_sig.items()):
                nid = hashlib.sha256(json.dumps([season, phase, hero or "", day, sig]).encode()).hexdigest()[:24]
                for rk in members: assignment[(rk, day)] = nid
                nodes.append({"node_id": nid, "day": day, "hero": hero, "run_count": len(members), "sample_count": len(members), "core_items": [], "items": [_display(x, card_resolver) for x in sig]})
        edge_members = defaultdict(set)
        run_days = defaultdict(list)
        for (rk, day), nid in assignment.items(): run_days[rk].append(day)
        for rk, ds in run_days.items():
            for a, b in zip(sorted(set(ds)), sorted(set(ds))[1:]): edge_members[(assignment[(rk,a)], assignment[(rk,b)], a, b)].add(rk)
        edges = [{"parent_node_id": p, "child_node_id": c, "parent_day": a, "child_day": b, "run_count": len(rs), "gap_days": max(0,b-a-1)} for (p,c,a,b), rs in sorted(edge_members.items())]
        return jsonify({"username": _PUBLIC_USER, "season": season, "phase": phase, "hero": hero_filter, "nodes": nodes, "edges": edges})

    return blueprint


_RESERVED_STATUSES = ("pending", "creating", "active", "failed", "stopped")
_PUBLIC_STATUSES = _RESERVED_STATUSES


def _utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalized(value: str) -> str:
    return " ".join(value.strip().split()).casefold()


def _profile_id(value: object) -> str:
    text = str(value or "").strip()
    match = re.fullmatch(r"https://bazaardb\.gg/run/profile/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/?", text, re.IGNORECASE)
    if not match:
        raise ValueError("无效的 BazaarDB 个人页 URL，须为 https://bazaardb.gg/run/profile/UUID")
    return str(uuid.UUID(match.group(1)))


def _space_name(value: object) -> tuple[str, str]:
    name = " ".join(str(value or "").strip().split())
    if not name.endswith("空间") or not 3 <= len(name) <= 48:
        raise ValueError("空间名称须为 3-48 个字符且以“空间”结尾")
    return name, _normalized(name)


class _LegacySpaceSchema:
    """Durable lifecycle/outbox store. It deliberately owns reservations, not run data."""

    def __init__(self, path: str | Path):
        self.path = str(path)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def initialize(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript("""
                PRAGMA user_version=1;
                CREATE TABLE IF NOT EXISTS zaza_spaces (
                    id TEXT PRIMARY KEY, profile_id TEXT NOT NULL, profile_url TEXT NOT NULL,
                    name TEXT NOT NULL, normalized_name TEXT NOT NULL, status TEXT NOT NULL,
                    auto_approved INTEGER NOT NULL DEFAULT 0, collection_error TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT,
                    current_season INTEGER, current_phase TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS uq_zaza_space_profile_live
                    ON zaza_spaces(profile_id) WHERE status IN ('pending','creating','active','failed','stopped');
                CREATE UNIQUE INDEX IF NOT EXISTS uq_zaza_space_name_live
                    ON zaza_spaces(normalized_name) WHERE status IN ('pending','creating','active','failed','stopped');
                CREATE TABLE IF NOT EXISTS zaza_outbox (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, space_id TEXT NOT NULL, kind TEXT NOT NULL,
                    payload_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                    lease_token TEXT, available_at TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    UNIQUE(space_id, kind, status)
                );
                CREATE TABLE IF NOT EXISTS zaza_run_interest (
                    space_id TEXT NOT NULL, run_id TEXT NOT NULL, season INTEGER NOT NULL, phase TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    PRIMARY KEY(space_id, run_id)
                );
            """)

    def _row(self, row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        value = dict(row)
        value["auto_approved"] = bool(value["auto_approved"])
        return value

    def create(self, profile_url: object, name: object, *, auto_approve: bool = False) -> dict:
        profile = _profile_id(profile_url)
        display_name, normalized_name = _space_name(name)
        now = _utcnow()
        space = {"id": str(uuid.uuid4()), "profile_id": profile, "profile_url": str(profile_url).strip(),
                 "name": display_name, "normalized_name": normalized_name,
                 "status": "creating" if auto_approve else "pending", "auto_approved": int(auto_approve),
                 "created_at": now, "updated_at": now}
        try:
            with self.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("""INSERT INTO zaza_spaces
                    (id,profile_id,profile_url,name,normalized_name,status,auto_approved,created_at,updated_at)
                    VALUES (:id,:profile_id,:profile_url,:name,:normalized_name,:status,:auto_approved,:created_at,:updated_at)""", space)
                if auto_approve:
                    conn.execute("""INSERT INTO zaza_outbox (space_id,kind,payload_json,available_at,created_at,updated_at)
                        VALUES (?,?,?,?,?,?)""", (space["id"], "discover", "{}", now, now, now))
                conn.commit()
        except sqlite3.IntegrityError as exc:
            message = "该 BazaarDB 个人页已申请空间" if "profile" in str(exc).lower() else "该空间名称已被占用"
            raise ValueError(message) from exc
        return self.get(space["id"])

    def get(self, space_id: str, *, include_deleted: bool = False) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM zaza_spaces WHERE id=?" + ("" if include_deleted else " AND status != 'deleted'"), (space_id,)).fetchone()
        return self._row(row)

    def list_public(self, query: str = "") -> list[dict]:
        query = _normalized(query)
        with self.connect() as conn:
            rows = conn.execute("""SELECT * FROM zaza_spaces WHERE status IN ('pending','creating','active','failed','stopped')
                AND (?='' OR normalized_name LIKE ?) ORDER BY updated_at DESC, id""", (query, f"%{query}%")).fetchall()
        return [self._row(row) for row in rows]

    def admin_list(self) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM zaza_spaces ORDER BY updated_at DESC, id").fetchall()
        return [self._row(row) for row in rows]

    def transition(self, space_id: str, action: str, *, name: object = None) -> dict:
        now = _utcnow()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute("SELECT * FROM zaza_spaces WHERE id=?", (space_id,)).fetchone()
            if current is None:
                raise LookupError("空间不存在")
            if action == "rename":
                display_name, normalized_name = _space_name(name)
                try:
                    conn.execute("UPDATE zaza_spaces SET name=?, normalized_name=?, updated_at=? WHERE id=?",
                                 (display_name, normalized_name, now, space_id))
                except sqlite3.IntegrityError as exc:
                    raise ValueError("该空间名称已被占用") from exc
            elif action in ("approve", "retry", "resume"):
                conn.execute("UPDATE zaza_spaces SET status='creating', collection_error=NULL, updated_at=? WHERE id=?", (now, space_id))
                conn.execute("""INSERT OR IGNORE INTO zaza_outbox (space_id,kind,payload_json,available_at,created_at,updated_at)
                    VALUES (?,?,?,?,?,?)""", (space_id, "discover", "{}", now, now, now))
            elif action == "reject":
                conn.execute("UPDATE zaza_spaces SET status='rejected', updated_at=? WHERE id=?", (now, space_id))
            elif action == "cancel":
                conn.execute("UPDATE zaza_spaces SET status='cancelled', updated_at=? WHERE id=?", (now, space_id))
            elif action == "stop":
                conn.execute("UPDATE zaza_spaces SET status='stopped', updated_at=? WHERE id=?", (now, space_id))
                conn.execute("UPDATE zaza_run_interest SET active=0, updated_at=? WHERE space_id=?", (now, space_id))
            elif action == "delete":
                conn.execute("UPDATE zaza_spaces SET status='deleted', deleted_at=?, updated_at=? WHERE id=?", (now, now, space_id))
                conn.execute("UPDATE zaza_run_interest SET active=0, updated_at=? WHERE space_id=?", (now, space_id))
            else:
                raise ValueError("不支持的操作")
            conn.commit()
        return self.get(space_id, include_deleted=True)

    def claim_outbox(self, limit: int = 5) -> list[dict]:
        """Lease Zaza-only discovery jobs; stale leases are safely retried."""
        now = _utcnow()
        token = uuid.uuid4().hex
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("""UPDATE zaza_outbox SET status='pending', lease_token=NULL, updated_at=?
                            WHERE status='processing' AND updated_at < datetime('now', '-30 minutes')""", (now,))
            rows = conn.execute("""SELECT id, space_id, kind, payload_json, attempts FROM zaza_outbox
                                 WHERE status='pending' AND available_at <= ? ORDER BY id LIMIT ?""", (now, limit)).fetchall()
            claimed = []
            for row in rows:
                changed = conn.execute("""UPDATE zaza_outbox SET status='processing', lease_token=?, attempts=attempts+1, updated_at=?
                                        WHERE id=? AND status='pending'""", (token, now, row["id"])).rowcount
                if changed:
                    item = dict(row)
                    item["lease_token"] = token
                    claimed.append(item)
            conn.commit()
        return claimed

    def complete_outbox(self, job_id: int, lease_token: str, error: str | None = None) -> None:
        now = _utcnow()
        with self.connect() as conn:
            status = "pending" if error else "success"
            changed = conn.execute("""UPDATE zaza_outbox SET status=?, payload_json=?, lease_token=NULL, updated_at=?, available_at=?
                                    WHERE id=? AND status='processing' AND lease_token=?""",
                                   (status, json.dumps({"error": error[:1000]} if error else {}, ensure_ascii=False), now, now, job_id, lease_token)).rowcount
            if not changed:
                raise ValueError("outbox job is not claimed")
            conn.commit()

    def mark_collection(self, space_id: str, *, season: int, phase: str, run_ids: list[str], error: str | None = None) -> dict:
        """Idempotent reconciliation after an external discovery attempt.

        This is intentionally separate from lifecycle transitions: a published space
        remains readable even when enrichment has failed.
        """
        now = _utcnow()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            space = conn.execute("SELECT status, current_season FROM zaza_spaces WHERE id=?", (space_id,)).fetchone()
            if space is None:
                raise LookupError("空间不存在")
            if space["status"] in ("stopped", "deleted", "rejected", "cancelled"):
                conn.commit()
                return self.get(space_id, include_deleted=True)
            status = "failed" if error and space["status"] != "active" and space["current_season"] is None else "active"
            conn.execute("""UPDATE zaza_spaces SET status=?, collection_error=?, current_season=?, current_phase=?, updated_at=?
                            WHERE id=?""", (status, error[:1000] if error else None, season, phase, now, space_id))
            if not error:
                conn.execute("UPDATE zaza_run_interest SET active=0, updated_at=? WHERE space_id=? AND (season != ? OR phase != ?)",
                             (now, space_id, season, phase))
                conn.executemany("""INSERT INTO zaza_run_interest (space_id,run_id,season,phase,active,created_at,updated_at)
                    VALUES (?,?,?,?,1,?,?) ON CONFLICT(space_id,run_id) DO UPDATE SET active=1,season=excluded.season,phase=excluded.phase,updated_at=excluded.updated_at""",
                                 [(space_id, run_id, season, phase, now, now) for run_id in sorted(set(run_ids))])
            conn.commit()
        return self.get(space_id, include_deleted=True)


class ZazaSpaceStore(_LegacySpaceSchema):
    """Atomic, audited lifecycle and recurring discovery leases."""

    def initialize(self):
        super().initialize()
        with self.connect() as conn:
            columns = {row[1] for row in conn.execute('PRAGMA table_info(zaza_spaces)')}
            for column, definition in [('profile_json', "TEXT NOT NULL DEFAULT '{}'"), ('generation', 'INTEGER NOT NULL DEFAULT 0'), ('next_sync_at', 'TEXT'), ('stopped_at', 'TEXT')]:
                if column not in columns:
                    conn.execute(f'ALTER TABLE zaza_spaces ADD COLUMN {column} {definition}')
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS zaza_settings (id INTEGER PRIMARY KEY CHECK(id=1), auto_approve INTEGER NOT NULL DEFAULT 0);
                INSERT OR IGNORE INTO zaza_settings(id) VALUES(1);
                CREATE TABLE IF NOT EXISTS zaza_audit (id INTEGER PRIMARY KEY, space_id TEXT, action TEXT NOT NULL, before_json TEXT, after_json TEXT, created_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS idx_zaza_interest_scope ON zaza_run_interest(space_id,season,phase,run_id);
                CREATE TABLE IF NOT EXISTS zaza_local_runs (source TEXT NOT NULL, run_id TEXT NOT NULL, profile_id TEXT NOT NULL, season INTEGER, phase TEXT, PRIMARY KEY(source,run_id));
                CREATE INDEX IF NOT EXISTS idx_zaza_local_profile_scope ON zaza_local_runs(source,profile_id,season,phase,run_id);
                CREATE TABLE IF NOT EXISTS zaza_local_sources (source TEXT PRIMARY KEY, fingerprint TEXT NOT NULL);
            """)
            sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='zaza_outbox'").fetchone()[0]
            if 'UNIQUE(space_id, kind, status)' in sql:
                conn.executescript("""
                    ALTER TABLE zaza_outbox RENAME TO zaza_outbox_old;
                    CREATE TABLE zaza_outbox (id INTEGER PRIMARY KEY AUTOINCREMENT, space_id TEXT NOT NULL, kind TEXT NOT NULL, payload_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0, lease_token TEXT, available_at TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                    INSERT INTO zaza_outbox SELECT * FROM zaza_outbox_old;
                    DROP TABLE zaza_outbox_old;
                """)
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_zaza_outbox_live ON zaza_outbox(space_id,kind) WHERE status IN ('pending','processing')")
            conn.execute('CREATE INDEX IF NOT EXISTS idx_zaza_outbox_due ON zaza_outbox(status,available_at)')

    def settings(self):
        with self.connect() as conn:
            return {'auto_approve': bool(conn.execute('SELECT auto_approve FROM zaza_settings WHERE id=1').fetchone()[0])}

    def set_settings(self, auto_approve):
        if not isinstance(auto_approve, bool):
            raise ValueError('auto_approve 必须为布尔值')
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            before = dict(conn.execute('SELECT * FROM zaza_settings WHERE id=1').fetchone())
            conn.execute('UPDATE zaza_settings SET auto_approve=? WHERE id=1', (int(auto_approve),))
            self._audit(conn, None, 'settings', before, {'auto_approve': auto_approve})
        return self.settings()

    def _audit(self, conn, space_id, action, before, after):
        conn.execute('INSERT INTO zaza_audit(space_id,action,before_json,after_json,created_at) VALUES(?,?,?,?,?)',
                     (space_id, action, json.dumps(before, ensure_ascii=False), json.dumps(after, ensure_ascii=False), _utcnow()))

    def audit(self, space_id=None):
        with self.connect() as conn:
            rows = conn.execute('SELECT * FROM zaza_audit WHERE (? IS NULL OR space_id=?) ORDER BY id DESC LIMIT 200', (space_id, space_id)).fetchall()
        return [dict(row) for row in rows]

    def create(self, profile_url, name, *, auto_approve=None):
        profile = _profile_id(profile_url)
        name, normalized = _space_name(name)
        now = _utcnow()
        ident = str(uuid.uuid4())
        try:
            with self.connect() as conn:
                conn.execute('BEGIN IMMEDIATE')
                if auto_approve is None:
                    auto_approve = bool(conn.execute('SELECT auto_approve FROM zaza_settings WHERE id=1').fetchone()[0])
                conn.execute('INSERT INTO zaza_spaces(id,profile_id,profile_url,name,normalized_name,status,auto_approved,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',
                             (ident, profile, f'https://bazaardb.gg/run/profile/{profile}', name, normalized, 'creating' if auto_approve else 'pending', int(auto_approve), now, now))
                if auto_approve:
                    self._enqueue(conn, ident, 0)
                self._audit(conn, ident, 'create', None, dict(conn.execute('SELECT * FROM zaza_spaces WHERE id=?', (ident,)).fetchone()))
        except sqlite3.IntegrityError as exc:
            raise ValueError('该 BazaarDB 个人页已申请空间' if 'profile' in str(exc).lower() else '该空间名称已被占用') from exc
        return self.get(ident)

    def _enqueue(self, conn, ident, generation):
        now = _utcnow()
        conn.execute('INSERT OR IGNORE INTO zaza_outbox(space_id,kind,payload_json,available_at,created_at,updated_at) VALUES(?,?,?,?,?,?)',
                     (ident, 'discover', json.dumps({'generation': generation}), now, now, now))

    def transition(self, space_id, action, *, name=None):
        allowed = {'approve': {'pending'}, 'reject': {'pending'}, 'retry': {'failed','active'}, 'resume': {'stopped'}, 'stop': {'creating','active','failed'}, 'cancel': {'pending','creating','failed'}, 'delete': set(_RESERVED_STATUSES) | {'rejected','cancelled'}, 'rename': set(_RESERVED_STATUSES)}
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT * FROM zaza_spaces WHERE id=?', (space_id,)).fetchone()
            if row is None:
                raise LookupError('空间不存在')
            before = dict(row)
            if action not in allowed or row['status'] not in allowed[action]:
                raise ValueError('当前状态不支持此操作')
            now = _utcnow()
            generation = row['generation'] + (action != 'rename')
            if action == 'rename':
                display, normalized = _space_name(name)
                try:
                    conn.execute('UPDATE zaza_spaces SET name=?,normalized_name=?,updated_at=? WHERE id=?', (display,normalized,now,space_id))
                except sqlite3.IntegrityError as exc:
                    raise ValueError('该空间名称已被占用') from exc
            else:
                conn.execute("UPDATE zaza_outbox SET status='cancelled',lease_token=NULL,updated_at=? WHERE space_id=? AND status IN ('pending','processing')", (now,space_id))
                if action in ('approve','retry','resume'):
                    status = 'active' if row['current_season'] is not None else 'creating'
                    self._enqueue(conn, space_id, generation)
                else:
                    status = {'reject':'rejected','cancel':'cancelled','stop':'stopped','delete':'deleted'}[action]
                    conn.execute('UPDATE zaza_run_interest SET active=0,updated_at=? WHERE space_id=?', (now,space_id))
                conn.execute('UPDATE zaza_spaces SET status=?,generation=?,collection_error=NULL,updated_at=?,deleted_at=?,stopped_at=? WHERE id=?', (status,generation,now,now if action=='delete' else None,now if action=='stop' else None,space_id))
            self._audit(conn,space_id,action,before,dict(conn.execute('SELECT * FROM zaza_spaces WHERE id=?',(space_id,)).fetchone()))
        return self.get(space_id,include_deleted=True)

    def local_runs(self, runs_path, profile_id, season, phase, *, created_after=None, created_before=None):
        """Materialize identities once per changed local DB; never write its schema."""
        runs_path=Path(runs_path).resolve()
        if not runs_path.exists():
            raise FileNotFoundError('local runs database unavailable')
        source=str(runs_path)
        fingerprint=json.dumps([(str(p),p.stat().st_mtime_ns,p.stat().st_size) for p in (runs_path,Path(source+'-wal')) if p.exists()])
        with self.connect() as conn:
            known=conn.execute('SELECT fingerprint FROM zaza_local_sources WHERE source=?',(source,)).fetchone()
        if known is None or known[0]!=fingerprint:
            with closing(sqlite3.connect(f'file:{runs_path}?mode=ro',uri=True)) as conn:
                columns={r[1] for r in conn.execute('PRAGMA table_info(runs)')}
                if not {'id','season','phase'} <= columns:
                    raise ValueError('local runs schema missing id/season/phase')
                identity="CASE WHEN raw_json IS NULL OR raw_json='' THEN profileId ELSE json_extract(CASE WHEN json_valid(raw_json) THEN raw_json ELSE '{}' END,'$.profileId') END" if {'profileId','raw_json'} <= columns else ("json_extract(CASE WHEN json_valid(raw_json) THEN raw_json ELSE '{}' END,'$.profileId')" if 'raw_json' in columns else 'profileId')
                if 'profileId' not in columns and 'raw_json' not in columns:
                    raise ValueError('local runs schema missing profile identity')
                rows=conn.execute(f'SELECT id,{identity},season,phase FROM runs').fetchall()
            with self.connect() as conn:
                conn.execute('BEGIN IMMEDIATE')
                conn.execute('DELETE FROM zaza_local_runs WHERE source=?',(source,))
                conn.executemany('INSERT OR REPLACE INTO zaza_local_runs VALUES(?,?,?,?,?)',[(source,rid,str(pid).casefold(),ss,pp) for rid,pid,ss,pp in rows if pid])
                conn.execute('INSERT OR REPLACE INTO zaza_local_sources VALUES(?,?)',(source,fingerprint))
        with self.connect() as conn:
            ids=[r[0] for r in conn.execute('SELECT run_id FROM zaza_local_runs WHERE source=? AND profile_id=? AND season=? AND phase=?',(source,profile_id,season,phase))]
        return _database_runs(runs_path,profile_id,season,phase,ids,
                              created_after=created_after,created_before=created_before)

    def schedule_sync(self, season, phase):
        now = _utcnow()
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            for row in conn.execute("SELECT * FROM zaza_spaces WHERE status IN ('creating','active','failed') AND (next_sync_at IS NULL OR next_sync_at<=? OR current_season!=? OR current_phase!=?)", (now,season,phase)).fetchall():
                self._enqueue(conn,row['id'],row['generation'])

    def claim_outbox(self, limit=5):
        now = _utcnow()
        cutoff = (datetime.now(timezone.utc)-timedelta(minutes=10)).isoformat().replace('+00:00','Z')
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute("UPDATE zaza_outbox SET status='pending',lease_token=NULL WHERE status='processing' AND updated_at<?", (cutoff,))
            rows = conn.execute("SELECT * FROM zaza_outbox WHERE status='pending' AND available_at<=? ORDER BY id LIMIT ?",(now,limit)).fetchall()
            jobs=[]
            for row in rows:
                token=uuid.uuid4().hex
                conn.execute("UPDATE zaza_outbox SET status='processing',lease_token=?,attempts=attempts+1,updated_at=? WHERE id=?",(token,now,row['id']))
                jobs.append(dict(row)|{'lease_token':token,'attempts':row['attempts']+1})
        return jobs

    def complete_outbox(self, job_id, lease_token, error=None):
        now = _utcnow()
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row=conn.execute("SELECT * FROM zaza_outbox WHERE id=? AND status='processing' AND lease_token=?",(job_id,lease_token)).fetchone()
            if row is None:
                raise ValueError('outbox job is not claimed')
            due=_next_odd_hour_sync() if error else now
            conn.execute('UPDATE zaza_outbox SET status=?,lease_token=NULL,available_at=?,updated_at=? WHERE id=?',('pending' if error else 'success',due,now,job_id))

    def mark_collection(self, space_id, *, season, phase, run_ids, error=None, profile=None, job=None,
                        created_after=None, created_before=None):
        now = _utcnow()
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row=conn.execute('SELECT * FROM zaza_spaces WHERE id=?',(space_id,)).fetchone()
            if row is None:
                raise LookupError('空间不存在')
            valid = row['status'] in ('creating','active','failed')
            if job:
                lease=conn.execute("SELECT * FROM zaza_outbox WHERE id=? AND lease_token=? AND status='processing'",(job['id'],job['lease_token'])).fetchone()
                valid = valid and lease is not None and _json(job['payload_json'],{}).get('generation')==row['generation']
            if not valid:
                return dict(row)
            status='active' if not error or row['current_season'] is not None else 'failed'
            due=_next_odd_hour_sync()
            conn.execute('UPDATE zaza_spaces SET status=?,collection_error=?,next_sync_at=?,updated_at=? WHERE id=?',(status,str(error)[:1000] if error else None,due,now,space_id))
            if not error:
                profile = dict(profile or _json(row['profile_json'],{}))
                if created_after is not None or created_before is not None:
                    profile['collection_window'] = {'created_after':created_after,'created_before':created_before}
                conn.execute('UPDATE zaza_spaces SET current_season=?,current_phase=?,profile_json=? WHERE id=?',(season,phase,json.dumps(profile or _json(row['profile_json'],{}),ensure_ascii=False),space_id))
                conn.execute('UPDATE zaza_run_interest SET active=0,updated_at=? WHERE space_id=?',(now,space_id))
                conn.executemany('INSERT INTO zaza_run_interest VALUES(?,?,?,?,1,?,?) ON CONFLICT(space_id,run_id) DO UPDATE SET active=1,season=excluded.season,phase=excluded.phase,updated_at=excluded.updated_at',[(space_id,r,season,phase,now,now) for r in sorted(set(run_ids))])
        return self.get(space_id,include_deleted=True)


def _database_runs(runs_path: Path, profile_id: str, season: int, phase: str, run_ids=None,
                   *, created_after=None, created_before=None) -> list[dict]:
    if not runs_path.exists():
        return []
    with sqlite3.connect(f"file:{runs_path.resolve()}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        columns = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
        selected = [column for column in ("id", "hero", "created_at", "stat_wins", "stat_losses", "items_json", "skills_json", "player_rank", "profileId", "raw_json", "season", "phase") if column in columns]
        if not {'id', 'season', 'phase'} <= columns:
            return []
        where = 'season=? AND phase=?'
        params = [season, phase]
        if run_ids is not None:
            if not run_ids:
                return []
            # JSON each avoids SQLite parameter-count limits and probes the run id index.
            where += ' AND id IN (SELECT value FROM json_each(?))'
            params.append(json.dumps(run_ids))
        else:
            identity = "CASE WHEN raw_json IS NULL OR raw_json='' THEN profileId ELSE json_extract(CASE WHEN json_valid(raw_json) THEN raw_json ELSE '{}' END,'$.profileId') END" if {'profileId','raw_json'} <= columns else ("json_extract(CASE WHEN json_valid(raw_json) THEN raw_json ELSE '{}' END,'$.profileId')" if 'raw_json' in columns else 'profileId')
            if 'profileId' not in columns and 'raw_json' not in columns:
                return []
            where += f' AND {identity}=?'
            params.append(profile_id)
        rows = conn.execute("SELECT " + ",".join(selected) + ' FROM runs WHERE ' + where, params).fetchall()
    result = []
    def timestamp(value):
        parsed = datetime.fromisoformat(value.replace('Z','+00:00'))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    start = timestamp(created_after) if created_after else None
    end = timestamp(created_before) if created_before else None
    for row in rows:
        value = dict(row)
        found = _json(value.get('raw_json'), {}).get('profileId') if value.get('raw_json') else value.get('profileId')
        if str(found or "").casefold() == profile_id:
            if start is not None or end is not None:
                try:
                    when = timestamp(value.get('created_at') or '')
                except (ValueError, TypeError, AttributeError):
                    continue
                if (start is not None and when < start) or (end is not None and when >= end):
                    continue
            result.append(value)
    return sorted(result, key=lambda row: (str(row.get("created_at") or ""), str(row["id"])), reverse=True)


def create_zaza_blueprint(runs_path, space_path=None, snapshots_path=None, season=None, phase=None,
                          card_resolver=None, admin_authorized=None, auto_approve=None, data_path=None,
                          created_after=None, created_before=None):
    """Public multi-space API. `data_path` keeps the former fixed artifact caller compatible."""
    # Compatibility with the original positional factory:
    # create_zaza_blueprint(runs_path, artifact_path, season, phase, resolver).
    if data_path is not None or (isinstance(snapshots_path, int) and isinstance(season, str)):
        artifact_path = data_path if data_path is not None else space_path
        legacy_season = season if data_path is not None else snapshots_path
        legacy_phase = phase if data_path is not None else season
        legacy_resolver = card_resolver if data_path is not None else phase
        return _create_legacy_zaza_blueprint(runs_path, artifact_path, legacy_season, legacy_phase, legacy_resolver)
    blueprint = Blueprint("zaza_space", __name__)
    resolved_space_path = space_path() if callable(space_path) else space_path
    store = ZazaSpaceStore(resolved_space_path or os.environ.get("ZAZA_SPACE_DB_PATH", "/opt/qiubot/data/zaza_spaces.db"))
    store.initialize()
    is_admin = admin_authorized or (lambda: False)
    auto = auto_approve

    def resolve(value):
        return Path(str(value() if callable(value) else value))

    def scope() -> tuple[int, str]:
        return int(season() if callable(season) else season), str(phase() if callable(phase) else phase)

    def public(space: dict) -> dict:
        return {key: space.get(key) for key in ("id", "name", "status", "created_at", "updated_at", "current_season", "current_phase", "collection_error")}

    def require_admin():
        return bool(is_admin())

    @blueprint.post("/api/zaza/spaces")
    def create_space():
        body = request.get_json(silent=True) or {}
        try:
            space = store.create(body.get("profileUrl"), body.get("name"), auto_approve=(bool(auto()) if callable(auto) else auto))
            return jsonify({"space": space}), 201
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400 if "无效" in str(exc) or "须为" in str(exc) else 409

    @blueprint.get("/api/zaza/spaces")
    def spaces():
        return jsonify({"spaces": [public(space) for space in store.list_public(request.args.get("query", ""))]})

    def visible_runs(space):
        current_season, current_phase = scope()
        if space['status']=='stopped' and space['current_season'] is not None:
            current_season,current_phase=space['current_season'],space['current_phase']
        if space['status']=='pending' or space['current_season'] is None:
            return current_season,current_phase,[]
        window = _json(space.get('profile_json'),{}).get('collection_window',{}) if (space['current_season'],space['current_phase']) == (current_season,current_phase) else {}
        start,end = window.get('created_after'),window.get('created_before')
        if space['status']!='stopped' and (current_season,current_phase) == scope():
            if created_after is not None:
                start = created_after() if callable(created_after) else created_after
            if created_before is not None:
                end = created_before() if callable(created_before) else created_before
        with store.connect() as conn:
            active = '' if space['status']=='stopped' else ' AND active=1'
            ids=[row[0] for row in conn.execute('SELECT run_id FROM zaza_run_interest WHERE space_id=? AND season=? AND phase=?'+active,(space['id'],current_season,current_phase))]
        return current_season,current_phase,_database_runs(resolve(runs_path),space['profile_id'],current_season,current_phase,ids,
                                                          created_after=start,created_before=end)

    def skill_display(skill):
        sid=_card_id(skill) or (skill.get('skillId') if isinstance(skill,dict) else None)
        if not sid:
            return {}
        result={'skill_id':sid, **_instance_fields(skill)}
        try:
            fn=getattr(card_resolver,'skill_display_info_cached',None) or getattr(card_resolver,'resolve_skill',None)
            if fn:
                info=fn(sid)
                if isinstance(info,dict):
                    result.update({k:v for k,v in info.items() if k in ('name','name_en','art','img','description')})
        except Exception:
            pass
        if isinstance(skill,dict) and 'name' not in result:
            result['name']=skill.get('title') or skill.get('name')
        return result

    def days_for(row, space, ss, pp):
        snapshot_file=resolve(snapshots_path) if snapshots_path is not None else None
        if snapshot_file is None or not snapshot_file.exists():
            return [],'missing'
        try:
            with sqlite3.connect(f'file:{snapshot_file.resolve()}?mode=ro',uri=True) as conn:
                conn.row_factory=sqlite3.Row
                columns='run_id,event_id,season,phase,day,hour,captured_at,outcome,player_board_json,player_skills_json,fetched_at'
                source='SELECT '+columns+' FROM combat_snapshots WHERE hour=6 AND is_pvp=1'
                if conn.execute("SELECT 1 FROM sqlite_master WHERE name='zaza_combat_snapshots'").fetchone():
                    source+=' UNION ALL SELECT '+columns+' FROM zaza_combat_snapshots WHERE hour=6 AND is_pvp=1'
                query='SELECT * FROM ('+source+') WHERE run_id=? AND season=? AND phase=?'
                params=[row['id'],ss,pp]
                if space['status']=='stopped':
                    # Persisted UTC stop instant compared as time, not mixed text formats.
                    query+=' AND julianday(fetched_at)<=julianday(?)'
                    params.append(space.get('stopped_at') or space['updated_at'])
                snaps=conn.execute(query+' ORDER BY day,hour,captured_at,event_id',params).fetchall()
                job=None
                if conn.execute("SELECT 1 FROM sqlite_master WHERE name='zaza_snapshot_jobs'").fetchone():
                    job=conn.execute('SELECT status FROM zaza_snapshot_jobs WHERE run_id=? AND season=? AND phase=?',(row['id'],ss,pp)).fetchone()
            chosen={s['day']:s for s in snaps}
            days=[]
            for d,s in sorted(chosen.items()):
                board=_json(s['player_board_json'],None)
                skills=_json(s['player_skills_json'],None)
                valid=isinstance(board,list) and isinstance(skills,list)
                days.append({'day':d,'hour':s['hour'],'time':s['captured_at'],'outcome':s['outcome'],
                    'items':[_display(c,card_resolver) for c in board] if valid else [],
                    'skills':[skill_display(c) for c in skills] if valid else [],
                    'data_status':'available' if valid else 'invalid',
                    'data_error':None if valid else '快照数据解析异常，等待重新采集'})
            return days,'available' if days else (job[0] if job and job[0] != 'success' else 'missing')
        except sqlite3.Error:
            return [],'missing'

    def run_display(row,space,ss,pp, snapshot_result=None):
        days,status=snapshot_result if snapshot_result is not None else days_for(row,space,ss,pp)
        return {'run_key':_key(ss,pp,row['id']),'hero':row.get('hero'),'time':row.get('created_at'),
                'wins':row.get('stat_wins'),'losses':row.get('stat_losses'),
                'items':[_display(c,card_resolver) for c in _final_cards(row,'items')],
                'skills':[skill_display(c) for c in _final_cards(row,'skills')],
                'day_count':len(days),'snapshot_status':status}

    @blueprint.get('/api/zaza/spaces/<space_id>/runs/<run_key>')
    def space_run(space_id,run_key):
        space=store.get(space_id)
        if not space or space['status'] not in _PUBLIC_STATUSES:
            return jsonify({'error':'空间不存在'}),404
        ss,pp,rows=visible_runs(space)
        row=next((r for r in rows if _key(ss,pp,r['id'])==run_key),None)
        if row is None:
            return jsonify({'error':'未知对局'}),404
        snapshots=days_for(row,space,ss,pp)
        return jsonify({'run_display':run_display(row,space,ss,pp,snapshots),
                        'scope':{'season':ss,'phase':pp},'space':public(space),
                        'days':snapshots[0]})

    @blueprint.get('/api/zaza/spaces/<space_id>/runs/<run_key>/days')
    def space_days(space_id,run_key):
        space=store.get(space_id)
        if not space or space['status'] not in _PUBLIC_STATUSES:
            return jsonify({'error':'空间不存在'}),404
        ss,pp,rows=visible_runs(space)
        row=next((r for r in rows if _key(ss,pp,r['id'])==run_key),None)
        if row is None:
            return jsonify({'error':'未知对局'}),404
        days,status=days_for(row,space,ss,pp)
        return jsonify({'run_key':run_key,'days':days,'snapshot_status':status})

    @blueprint.get("/api/zaza/spaces/<space_id>")
    @blueprint.get('/api/zaza/spaces/<space_id>/runs')
    def space_detail(space_id):
        space=store.get(space_id)
        if not space or space['status'] not in _PUBLIC_STATUSES:
            return jsonify({'error':'空间不存在'}),404
        ss,pp,runs=visible_runs(space)
        try:
            page=int(request.args.get('page','1'))
            per_page=int(request.args.get('perPage',request.args.get('per_page','20')))
            if page<1 or not 1<=per_page<=100:
                raise ValueError()
            start=request.args.get('from') or request.args.get('fromDate') or request.args.get('start')
            end=request.args.get('to') or request.args.get('toDate') or request.args.get('end')
            for stamp in (start,end):
                if stamp:
                    datetime.fromisoformat(stamp.replace('Z','+00:00'))
        except ValueError:
            return jsonify({'error':'无效分页或时间参数'}),400
        heroes=defaultdict(int)
        wins=[int(r.get('stat_wins') or 0) for r in runs]
        suggestions={}
        for row in runs:
            heroes[row.get('hero') or '未知']+=1
            for card in _json(row.get('items_json'),[]):
                cid=_card_id(card)
                if cid:
                    suggestions[cid]=_display(card,card_resolver)
        cards=set(c.strip() for value in request.args.getlist('cards') for c in value.split(',') if c.strip())
        hero=request.args.get('hero')
        def stamp_value(value):
            parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
            return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
        filtered=[]
        for row in runs:
            if hero and row.get('hero')!=hero:
                continue
            if not cards <= {_card_id(c) for c in _json(row.get('items_json'),[])}:
                continue
            if start or end:
                try:
                    when=stamp_value(row.get('created_at') or '')
                    if start and when<stamp_value(start):
                        continue
                    if end and when>stamp_value(end):
                        continue
                except ValueError:
                    continue
            filtered.append(row)
        profile=_json(space.get('profile_json'),{})
        displayed = {row['id']: run_display(row, space, ss, pp) for row in runs}
        counts = {key: 0 for key in ('completed', 'pending', 'processing', 'retry', 'failed', 'missing')}
        for value in displayed.values():
            status = value['snapshot_status']
            key = 'completed' if status == 'available' else status
            if key in counts:
                counts[key] += 1
        progress = {'snapshots': counts, 'total_runs': len(runs),
                    'discovery': space['status'], 'stopped': space['status'] == 'stopped'}
        return jsonify({'space':public(space),'profile':{'url':space['profile_url'],'nickname':profile.get('nickname'),
                        'verified':profile.get('verified'),'supporter':profile.get('supporter')},
                        'scope':{'season':ss,'phase':pp},
                        'summary':{'run_count':len(runs),'average_wins':round(sum(wins)/len(wins),2) if wins else 0,
                                   'ten_win_rate':round(sum(w>=10 for w in wins)/len(wins),4) if wins else 0,'heroes':heroes},
                        'pagination':{'page':page,'per_page':per_page,'total':len(filtered)},
                        'progress': progress,
                        'suggestions':list(suggestions.values()),
                        'runs':[displayed[row['id']] for row in filtered[(page-1)*per_page:page*per_page]]})

    @blueprint.route('/api/admin/zaza/settings',methods=['GET','PUT'])
    def admin_settings():
        if not require_admin():
            return jsonify({'error':'Unauthorized'}),401
        if request.method=='PUT':
            body=request.get_json(silent=True) or {}
            try:
                store.set_settings(body.get('auto_approve',body.get('autoApprove')))
            except ValueError as exc:
                return jsonify({'error':str(exc)}),400
        return jsonify({'settings':store.settings()})

    @blueprint.get('/api/admin/zaza/audit')
    def admin_audit():
        if not require_admin():
            return jsonify({'error':'Unauthorized'}),401
        return jsonify({'audit':store.audit(request.args.get('space_id'))})


    @blueprint.get("/api/admin/zaza/spaces")
    def admin_spaces():
        if not require_admin():
            return jsonify({"error": "Unauthorized"}), 401
        return jsonify({"spaces": store.admin_list()})

    @blueprint.put("/api/admin/zaza/spaces/<space_id>")
    def admin_space(space_id):
        if not require_admin():
            return jsonify({"error": "Unauthorized"}), 401
        body = request.get_json(silent=True) or {}
        try:
            result = store.transition(space_id, str(body.get("action") or ""), name=body.get("name"))
            if str(body.get("action") or "") == "retry" and snapshots_path is not None:
                ss, pp = scope()
                snapshot_file = resolve(snapshots_path)
                with store.connect() as interests:
                    run_ids = [r[0] for r in interests.execute(
                        'SELECT run_id FROM zaza_run_interest WHERE space_id=? AND season=? AND phase=?',
                        (space_id, ss, pp))]
                if snapshot_file.exists() and run_ids:
                    with sqlite3.connect(snapshot_file) as conn:
                        marks = ','.join('?' for _ in run_ids)
                        conn.execute(f"""UPDATE zaza_snapshot_jobs
                            SET status='pending', attempts=0, next_attempt_at=?, last_error=NULL,
                                lease_token=NULL, started_at=NULL, finished_at=NULL, updated_at=?
                            WHERE season=? AND phase=? AND status IN ('failed','missing')
                              AND run_id IN ({marks})""",
                            (datetime.now(timezone.utc).isoformat().replace('+00:00','Z'),
                             datetime.now(timezone.utc).isoformat().replace('+00:00','Z'), ss, pp, *run_ids))
            return jsonify({"space": result})
        except LookupError as exc:
            return jsonify({"error": str(exc)}), 404
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 409

    return blueprint
