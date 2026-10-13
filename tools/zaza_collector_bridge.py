"""Staging-only durable Windows collector queue (no network or deployment).

Deployment must prepare an isolated runs SQLite file, optionally seeded/merged
from a read-only production base. This module never opens or writes that base.
Register routes once in the staging Flask factory; workers enqueue_profile then
wait for has_completion_result before treating an empty run list as synced.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import jsonify, request

LEASE_SECONDS = 900
RETRY_SECONDS = 30
SCHEDULE_SECONDS = 7200
ROOT = Path(__file__).resolve().parents[1]


def _now():
    return datetime.now(timezone.utc)


def _dt(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('timestamp must be an ISO string')
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise ValueError('timestamp must include timezone')
    return dt.astimezone(timezone.utc)


def _text(dt):
    return dt.isoformat(timespec='microseconds').replace('+00:00', 'Z')


def _next_odd_hour(now):
    local = now.astimezone(timezone(timedelta(hours=8)))
    candidate = local.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    if candidate.hour % 2 == 0:
        candidate += timedelta(hours=1)
    return candidate.astimezone(timezone.utc)


def _connect(path):
    db = sqlite3.connect(str(path), timeout=30)
    db.row_factory = sqlite3.Row
    return db


def _queue_path(store):
    if not getattr(store, 'path', None):
        raise ValueError('store.path is required')
    path = Path(store.path).absolute()
    if path.is_symlink():
        raise ValueError('queue database must not be a symlink')
    return path


def _init(store):
    with closing(_connect(_queue_path(store))) as db, db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS zaza_collector_jobs (
          id TEXT PRIMARY KEY, profile_id TEXT NOT NULL, season INTEGER NOT NULL,
          phase TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
          mode TEXT NOT NULL DEFAULT 'initial', attempts INTEGER NOT NULL DEFAULT 0,
          lease_token TEXT, lease_until TEXT, created_after TEXT, created_before TEXT,
          next_attempt_at TEXT NOT NULL, completed_at TEXT, last_error TEXT,
          scheduled_count INTEGER NOT NULL DEFAULT 0,
          UNIQUE(profile_id,season,phase));
        CREATE INDEX IF NOT EXISTS idx_zaza_collector_due
          ON zaza_collector_jobs(season,phase,next_attempt_at);
        ''')
        columns = {row[1] for row in db.execute('PRAGMA table_info(zaza_collector_jobs)')}
        if 'scheduled_count' not in columns:
            db.execute("ALTER TABLE zaza_collector_jobs ADD COLUMN scheduled_count INTEGER NOT NULL DEFAULT 0")


def enqueue_profile(store, profile_id, season, phase):
    """Idempotent, shared across spaces: one queue row per profile + scope."""
    if not isinstance(profile_id, str) or not profile_id.strip() or not str(phase).strip():
        raise ValueError('profile_id and phase must be nonempty strings')
    _init(store)
    jid = hashlib.sha256(json.dumps([profile_id, int(season), str(phase)]).encode()).hexdigest()
    with closing(_connect(_queue_path(store))) as db, db:
        db.execute('''INSERT OR IGNORE INTO zaza_collector_jobs
          (id,profile_id,season,phase,next_attempt_at) VALUES(?,?,?,?,?)''',
                   (jid, profile_id, int(season), str(phase), _text(_now())))
        db.execute('''UPDATE zaza_collector_jobs SET status='scheduled',mode='scheduled',
          next_attempt_at=?,last_error=NULL WHERE id=? AND status='completed'
          AND completed_at IS NOT NULL''', (_text(_now()), jid))
        return dict(db.execute('SELECT * FROM zaza_collector_jobs WHERE id=?', (jid,)).fetchone())


def collector_status(store, profile_id, season, phase):
    _init(store)
    with closing(_connect(_queue_path(store))) as db:
        row = db.execute('''SELECT status,mode,completed_at,next_attempt_at,last_error
          FROM zaza_collector_jobs WHERE profile_id=? AND season=? AND phase=?''',
                         (profile_id, int(season), str(phase))).fetchone()
    result = dict(row) if row else {'status': 'not_yet_synced', 'completed_at': None}
    result['synced'] = bool(result['completed_at'])
    result['marker'] = 'synced' if result['synced'] else 'not_yet_synced'
    return result


def has_completion_result(store, profile_id, season, phase):
    """Successful empty arrays count; later scheduled/retry does not erase sync."""
    return collector_status(store, profile_id, season, phase)['synced']


def _safe_runs_path(app, store, runs_path):
    """Require deployment-prepared isolated DB; reject known production/aliases.

    ZAZA_PRODUCTION_RUNS_DB / RUNS_DB_PATH and app.config equivalents can name
    extra production paths. ZAZA_BASE_RUNS_DB is always protected, never opened.
    """
    path = _isolated_path(runs_path)
    path = path.resolve()
    protected = [ROOT / 'plugins/bazaar_plugin/data/runs.db',
                 ROOT / 'plugins/bazaar_plugin/cache/runs.db',
                 Path('/opt/qiubot/data/runs.db'), _queue_path(store)]
    for key in ('RUNS_DB_PATH', 'ZAZA_PRODUCTION_RUNS_DB', 'ZAZA_BASE_RUNS_DB', 'ZAZA_SNAPSHOTS_DB'):
        for value in (app.config.get(key), os.environ.get(key)):
            if value:
                protected.append(Path(value).resolve())
    if any(path == p or (path.exists() and p.exists() and os.path.samefile(path, p)) for p in protected):
        raise ValueError('production/base/queue database is not a collector write target')
    if any(part in ('production', 'prod') for part in path.parts) or path.is_relative_to(ROOT / 'plugins'):
        raise ValueError('production path is prohibited')
    if not path.is_file():
        raise ValueError('deployment must prepare an isolated runs database first')
    return path


def _isolated_path(runs_path):
    path = Path(os.path.abspath(runs_path))
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('runs_path must not contain symlink components')
    if Path('/opt/qiubot/data') in path.parents or path == Path('/opt/qiubot/data'):
        raise ValueError('production data directory is prohibited')
    if path.name == 'bazaar_runs.db' or any(p in ('production', 'prod') for p in path.parts):
        raise ValueError('production path is prohibited')
    if path.is_relative_to(ROOT / 'plugins'):
        raise ValueError('production plugin path is prohibited')
    return path


def initialize_runs(runs_path):
    """Public deployment helper: initialize an independent collector runs DB."""
    path = _isolated_path(runs_path)
    protected = [os.environ.get(key) for key in
                 ('RUNS_DB_PATH', 'ZAZA_PRODUCTION_RUNS_DB', 'ZAZA_BASE_RUNS_DB', 'ZAZA_SNAPSHOTS_DB')]
    for value in filter(None, protected):
        p = Path(value).resolve()
        if path.resolve() == p or (path.exists() and p.exists() and os.path.samefile(path, p)):
            raise ValueError('production/base database is prohibited')
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(':memory:')) as db, db:
        db.execute('ATTACH DATABASE ? AS collector_runs', (str(path),))
        _schema(db)
    return path


def _schema(db, namespace='collector_runs'):
    prefix = f'{namespace}.' if namespace else ''
    db.execute(f'''CREATE TABLE IF NOT EXISTS {prefix}runs (
      id TEXT PRIMARY KEY, profileId TEXT NOT NULL, season INTEGER NOT NULL,
      phase TEXT NOT NULL, created_at TEXT NOT NULL, stat_wins INTEGER,
      stat_losses INTEGER, hero TEXT, player_rank TEXT, items_json TEXT,
      skills_json TEXT, raw_json TEXT)''')
    columns = {r[1] for r in db.execute(f'PRAGMA {namespace + "." if namespace else ""}table_info(runs)')}
    required = {'id', 'profileId', 'season', 'phase', 'created_at', 'stat_wins',
                'stat_losses', 'hero', 'player_rank', 'items_json', 'skills_json', 'raw_json'}
    if not required <= columns:
        raise ValueError('runs table is incompatible with zaza_space._database_runs')


def _validate(runs, job):
    if not isinstance(runs, list):
        raise ValueError('runs must be an array')
    after, before = _dt(job['created_after']), _dt(job['created_before'])
    values = {}
    for run in runs:
        if not isinstance(run, dict):
            raise ValueError('every run must be an object')
        rid = run.get('id')
        if not isinstance(rid, str) or not rid.strip() or len(rid) > 256:
            raise ValueError('invalid run id')
        if run.get('profileId') != job['profile_id']:
            raise ValueError('profileId mismatch')
        when = _dt(run.get('createdAt'))
        if not after <= when < before:
            raise ValueError('run outside authoritative phase boundaries')
        if run.get('season', job['season']) != job['season'] or run.get('phase', job['phase']) != job['phase']:
            raise ValueError('season/phase mismatch')
        stats = []
        for field, upstream, alias in (('stat_wins', 'statWins', 'wins'), ('stat_losses', 'statLosses', 'losses')):
            stat = run.get(field, run.get(upstream, run.get(alias, 0)))
            if type(stat) is not int or stat < 0:
                raise ValueError('wins/losses must be nonnegative integers')
            stats.append(stat)
        for field in ('items', 'skills'):
            if not isinstance(run.get(field, []), list):
                raise ValueError('items/skills must be arrays')
        raw = json.dumps(run, ensure_ascii=False, allow_nan=False)
        row = (rid, job['profile_id'], job['season'], job['phase'], _text(when),
               *stats, run.get('hero'), run.get('player_rank', run.get('playerRank')),
               json.dumps(run.get('items', []), ensure_ascii=False),
               json.dumps(run.get('skills', []), ensure_ascii=False), raw)
        if rid in values and values[rid] != row:
            raise ValueError('conflicting duplicate run id')
        values[rid] = row
    return list(values.values())


def register_collector_routes(app, store, runs_path, season, phase, created_after, token):
    """Register authenticated protocol; created_after is server-owned phase start.

    Optional app.config['ZAZA_COLLECTOR_CREATED_BEFORE'] is an authoritative
    closed-phase end. Otherwise the claim time is the exclusive upper bound.
    All lease bounds are persisted; client-supplied bounds are never trusted.
    """
    if not isinstance(token, str) or not token:
        raise ValueError('collector token must be configured')
    start = _text(_dt(created_after))
    end_value = app.config.get('ZAZA_COLLECTOR_CREATED_BEFORE')
    end = _dt(end_value) if end_value else None
    if end and end <= _dt(start):
        raise ValueError('invalid phase boundaries')
    output = _safe_runs_path(app, store, runs_path)
    _init(store)
    season, phase = int(season), str(phase)

    def auth():
        supplied = request.headers.get('X-Ingest-Token', '')
        return hmac.compare_digest(supplied.encode(), token.encode())

    def denied():
        return jsonify(error='unauthorized'), 401

    def body():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ValueError('request must be a JSON object')
        return data

    def leased(db, data):
        if not isinstance(data.get('id'), str) or not isinstance(data.get('lease_token'), str):
            raise ValueError('id and lease_token are required')
        row = db.execute('''SELECT * FROM zaza_collector_jobs WHERE id=? AND season=?
          AND phase=? AND status='leased' AND lease_token=? AND lease_until>?''',
                         (data['id'], season, phase, data['lease_token'], _text(_now()))).fetchone()
        return row

    @app.post('/api/zaza/collector/claim')
    def collector_claim():
        if not auth():
            return denied()
        now = _now()
        upper = min(now, end) if end else now
        if upper <= _dt(start):
            return jsonify(jobs=[])
        with closing(_connect(_queue_path(store))) as db, db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('''UPDATE zaza_collector_jobs SET status='retry',lease_token=NULL,
              next_attempt_at=? WHERE season=? AND phase=? AND status='leased' AND lease_until<=?''',
                       (_text(now), season, phase, _text(now)))
            row = db.execute('''SELECT * FROM zaza_collector_jobs WHERE season=? AND phase=?
              AND status IN ('pending','retry','scheduled') AND next_attempt_at<=?
              ORDER BY next_attempt_at,id LIMIT 1''', (season, phase, _text(now))).fetchone()
            if row is None:
                return jsonify(jobs=[])
            lease = secrets.token_urlsafe(32)
            mode = 'scheduled' if row['completed_at'] else 'initial'
            db.execute('''UPDATE zaza_collector_jobs SET status='leased',mode=?,lease_token=?,
              lease_until=?,created_after=?,created_before=?,attempts=attempts+1,
              scheduled_count=CASE WHEN ?='scheduled' THEN scheduled_count+1 ELSE scheduled_count END WHERE id=?''',
                       (mode, lease, _text(now + timedelta(seconds=LEASE_SECONDS)), start, _text(upper), mode, row['id']))
            return jsonify(jobs=[dict(id=row['id'], lease_token=lease, profile_id=row['profile_id'],
                                     season=season, phase=phase, mode=mode,
                                     created_after=start, created_before=_text(upper))])

    @app.post('/api/zaza/collector/complete')
    def collector_complete():
        if not auth():
            return denied()
        try:
            data = body()
            # Recheck paths immediately before every write, including hardlinks.
            target = _safe_runs_path(app, store, output)
            db = _connect(_queue_path(store))
            output_db = None
            try:
                db.execute('BEGIN IMMEDIATE')
                job = leased(db, data)
                if job is None:
                    db.rollback()
                    return jsonify(error='stale lease'), 409
                values = _validate(data.get('runs'), job)
                output_db = _connect(target)
                _schema(output_db, '')
                for row in values:
                    existing = output_db.execute('SELECT profileId,season,phase FROM runs WHERE id=?', (row[0],)).fetchone()
                    if existing and tuple(existing) != tuple(row[1:4]):
                        raise ValueError('run id conflicts with historical identity/scope')
                    output_db.execute('''INSERT INTO runs
                      (id,profileId,season,phase,created_at,stat_wins,stat_losses,hero,
                       player_rank,items_json,skills_json,raw_json)
                      VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                      stat_wins=excluded.stat_wins,stat_losses=excluded.stat_losses,
                      hero=excluded.hero,player_rank=excluded.player_rank,
                      items_json=excluded.items_json,skills_json=excluded.skills_json,
                      raw_json=excluded.raw_json''', row)
                output_db.commit()
                now = _now()
                db.execute('''UPDATE zaza_collector_jobs SET status='scheduled',completed_at=?,
                  next_attempt_at=?,lease_token=NULL,lease_until=NULL,last_error=NULL,attempts=0 WHERE id=?''',
                           (_text(now), _text(_next_odd_hour(now)), job['id']))
                db.commit()
            finally:
                if output_db is not None:
                    output_db.close()
                try:
                    db.close()
                except sqlite3.Error:
                    pass
            return jsonify(ok=True, status='scheduled', synced=True, runs_count=len(values))
        except (ValueError, TypeError, OverflowError, sqlite3.Error) as exc:
            try:
                if 'db' in locals():
                    db.rollback()
                    db.close()
            except sqlite3.Error:
                pass
            return jsonify(error=str(exc)), 400

    @app.post('/api/zaza/collector/failure')
    def collector_failure():
        if not auth():
            return denied()
        try:
            data = body()
            if not isinstance(data.get('error'), str) or not data['error'].strip():
                raise ValueError('error must be a nonempty string')
            with closing(_connect(_queue_path(store))) as db, db:
                db.execute('BEGIN IMMEDIATE')
                job = leased(db, data)
                if job is None:
                    db.rollback()
                    return jsonify(error='stale lease'), 409
                delay = min(3600, RETRY_SECONDS * 2 ** min(job['attempts'] - 1, 7))
                db.execute('''UPDATE zaza_collector_jobs SET status='retry',lease_token=NULL,
                  lease_until=NULL,last_error=?,next_attempt_at=? WHERE id=?''',
                           (data['error'][:1000], _text(_now() + timedelta(seconds=delay)), job['id']))
            return jsonify(ok=True, status='retry')
        except (ValueError, TypeError) as exc:
            return jsonify(error=str(exc)), 400

    @app.get('/api/zaza/collector/status/<profile_id>')
    def collector_readback(profile_id):
        if not auth():
            return denied()
        return jsonify(collector_status(store, profile_id, season, phase))

    return app
