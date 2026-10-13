"""Read-only, isolated profile-runs pagination for Zaza.

The endpoint and cursor names below are taken from the BazaarDB profile client
bundle and must not be changed to a guessed endpoint.  This module does not
modify ``runs`` or any production database.  Callers provide an authenticated
client (the live endpoint returns 401 without the browser's Turnstile token).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

ENDPOINT = "/api/run"
PAGE_SIZE = 20  # observed client contract; the endpoint does not require a size parameter


class ProfileRunsAuthError(RuntimeError):
    """Browser authentication is absent or expired."""


class HTTPProfileClient:
    """Small transport adapter; authentication remains an explicit caller concern."""
    def __init__(self, session, base_url="https://bazaardb.gg"):
        self.session, self.base_url = session, base_url.rstrip("/")

    def get(self, path, params):
        response = self.session.get(self.base_url + path, params=params, timeout=30)
        if response.status_code in (401, 403):
            raise ProfileRunsAuthError(f"BazaarDB API authentication rejected ({response.status_code})")
        if response.status_code >= 400:
            raise RuntimeError(f"BazaarDB API returned HTTP {response.status_code}")
        return response.json()


def _validate_page(page: Any, profile_id: str) -> list[dict[str, Any]]:
    if not isinstance(page, list):
        raise ValueError("profile runs response must be a JSON array")
    result = []
    for run in page:
        if not isinstance(run, dict) or not isinstance(run.get("id"), str) or not run["id"]:
            raise ValueError("profile runs array contains an invalid run object")
        if not isinstance(run.get("createdAt"), str) or not run["createdAt"]:
            raise ValueError("profile runs object lacks createdAt cursor field")
        if "profileId" in run and run["profileId"] != profile_id:
            raise ValueError("profile runs response contains a different profile")
        result.append(run)
    return result


def collect_profile_runs(
    client, profile_id: str, *, mode: str = "scheduled",
    created_after=None, created_before=None, max_pages=10000,
) -> list[dict[str, Any]]:
    """Collect IDs from the verified BazaarDB profile endpoint.

    ``initial`` follows the createdAt/id cursor until the endpoint returns a
    short page (or an empty page). ``scheduled`` intentionally requests exactly
    the newest page. Deduplication is by the authoritative run ``id`` and does
    not remove anything from a local store.

    ``client.get(path, params)`` must perform the authenticated request and
    return the decoded JSON. Keeping transport/auth outside this function makes
    the pagination behavior independently testable without fake live data.
    """
    if mode not in {"initial", "scheduled"}:
        raise ValueError("mode must be 'initial' or 'scheduled'")
    all_runs: list[dict[str, Any]] = []
    seen: set[str] = set()
    cursor: tuple[str, str] | None = None
    visited_cursors = set()
    for _ in range(max_pages):
        params: dict[str, Any] = {
            "profileId": profile_id,
            "sort": "newest",
            "order": "desc",
        }
        if created_after is not None:
            params["createdAfter"] = created_after
        if created_before is not None:
            params["createdBefore"] = created_before
        if cursor:
            params.update(cursorCreatedAt=cursor[0], cursorId=cursor[1])
        page = _validate_page(client.get(ENDPOINT, params), profile_id)
        for run in page:
            when = datetime.fromisoformat(run['createdAt'].replace('Z', '+00:00'))
            if when.tzinfo is None:
                raise ValueError('createdAt must include timezone')
            if created_after is not None and when < datetime.fromisoformat(created_after.replace('Z', '+00:00')):
                continue
            if created_before is not None and when >= datetime.fromisoformat(created_before.replace('Z', '+00:00')):
                continue
            if run["id"] not in seen:
                seen.add(run["id"])
                all_runs.append(run)
        if mode == "scheduled" or len(page) < PAGE_SIZE:
            return all_runs
        last = page[-1]
        # The profile endpoint can return older rows despite createdAfter.
        # With newest/desc ordering, crossing the lower bound completes this
        # window; following the historical cursor can time out upstream.
        if created_after is not None and datetime.fromisoformat(last['createdAt'].replace('Z', '+00:00')) < datetime.fromisoformat(created_after.replace('Z', '+00:00')):
            return all_runs
        next_cursor = (last["createdAt"], last["id"])
        if next_cursor in visited_cursors:
            raise ValueError("pagination cursor repeated")
        visited_cursors.add(next_cursor)
        cursor = next_cursor
    raise ValueError("pagination page limit reached; initial discovery incomplete")


def initialize_store(path: str | Path) -> None:
    """Create only the independent local profile-runs store."""
    path = Path(path).resolve()
    if path.name in {"bazaar_runs.db", "bazaar_runs.sqlite"} or Path("/opt/qiubot/data") in path.parents:
        raise ValueError("isolated profile-runs store required; refusing production runs database")
    if path.exists():
        with sqlite3.connect(path) as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "runs" in tables:
            raise ValueError("isolated profile-runs store required; existing runs database refused")
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS profile_runs (
            profile_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            raw_json TEXT NOT NULL,
            PRIMARY KEY(profile_id, run_id)
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS profile_sync (
            profile_id TEXT NOT NULL, season INTEGER NOT NULL, phase TEXT NOT NULL,
            initial_complete INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(profile_id, season, phase)
        )""")


def sync_profile_runs(path, client, profile_id, *, season, phase, created_after=None, created_before=None):
    """Run one isolated initial/incremental sync with a durable completion marker."""
    if not phase or not created_after:
        raise ValueError("authoritative phase start boundary is required")
    initialize_store(path)
    with sqlite3.connect(path) as conn:
        row = conn.execute("SELECT initial_complete FROM profile_sync WHERE profile_id=? AND season=? AND phase=?", (profile_id, season, phase)).fetchone()
    mode = "scheduled" if row and row[0] else "initial"
    runs = collect_profile_runs(client, profile_id, mode=mode, created_after=created_after, created_before=created_before)
    inserted = store_runs(path, profile_id, runs)
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT OR REPLACE INTO profile_sync VALUES (?,?,?,1)", (profile_id, season, phase))
    return {"mode": mode, "inserted": inserted, "fetched": len(runs), "season": season, "phase": phase}



def store_runs(path: str | Path, profile_id: str, runs: list[dict[str, Any]]) -> int:
    """Append new runs to the isolated store; never delete or update history."""
    initialize_store(path)
    inserted = 0
    with sqlite3.connect(path) as conn:
        for run in runs:
            cur = conn.execute(
                "INSERT OR IGNORE INTO profile_runs(profile_id,run_id,raw_json) VALUES (?,?,?)",
                (profile_id, run["id"], json.dumps(run, ensure_ascii=False, sort_keys=True)),
            )
            inserted += cur.rowcount
    return inserted
