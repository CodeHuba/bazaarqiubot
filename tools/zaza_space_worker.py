"""Durable Zaza discovery and real shared Day capture service; no Flask threads."""
from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from web_runs.zaza_space import ZazaSpaceStore, _database_runs, _json
from web_runs.day_snapshot_store import DaySnapshotStore
from tools.tracker_rsc import extract_zaza_combat_snapshots, _parse_tracker_payload

LOG = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _utc_text(value=None) -> str:
    value = _utcnow() if value is None else value
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def _session():
    from curl_cffi import requests
    session = requests.Session(impersonate='chrome')
    # Optional exported browser cookies allow authenticated tracker capture.
    cookies = os.environ.get('ZAZA_BAZAAR_COOKIES_JSON')
    if cookies:
        session.cookies.update(json.loads(cookies))
    return session


def _fetch(session, path):
    response = session.get('https://bazaardb.gg' + path, headers={
        'Accept': 'text/x-component', 'Rsc': '1', 'Next-Url': path,
        'Referer': 'https://bazaardb.gg/run'}, timeout=30)
    response.raise_for_status()
    if any(marker in response.text[:5000].lower() for marker in ('cf-chl-', 'just a moment', 'attention required')):
        raise RuntimeError('BazaarDB Cloudflare authentication required')
    return response


def _objects(text):
    """Decode structured JSON objects in RSC and escaped Next HTML streams."""
    import re
    decoder = json.JSONDecoder()
    streams = [text]
    for match in re.finditer(r'self\.__next_f\.push\(\[1,"((?:\\.|[^"\\])*)"\]\)', text):
        try:
            streams.append(json.loads('"' + match.group(1) + '"'))
        except ValueError:
            continue
    def walk(value):
        if isinstance(value, dict):
            yield value
            for child in value.values():
                yield from walk(child)
        elif isinstance(value, list):
            for child in value:
                yield from walk(child)
    for stream in streams:
        for index, char in enumerate(stream):
            if char == '{':
                try:
                    value, _ = decoder.raw_decode(stream[index:])
                except ValueError:
                    continue
                yield from walk(value)


def validate_profile(profile_id: str, *, session=None) -> dict:
    profile_id = str(uuid.UUID(profile_id))
    owned = session is None
    session = session or _session()
    try:
        response = _fetch(session, '/run/profile/' + profile_id)
        for obj in _objects(response.text):
            profile = obj.get('profile')
            if not isinstance(profile, dict):
                continue
            if str(profile.get('id') or '').lower() == profile_id:
                nickname = profile.get('nickname')
                if isinstance(nickname, str) and nickname.strip():
                    return {'profile_id': profile_id, 'nickname': nickname,
                            'verified': profile.get('verified'),
                            'supporter': profile.get('isSupporter')}
        raise ValueError('BazaarDB 个人页响应未包含匹配的 profile.id 和昵称')
    finally:
        if owned:
            session.close()


class ZazaSnapshotStore:
    """Explicit Zaza queue and supplemental facts; never mutate global jobs/facts.

    Legacy workers do not know Zaza leases. We defer to every nonterminal global
    job instead of claiming it or pretending a Zaza lease fences a legacy worker.
    A terminal global capture lacking hour6 PvP needs a separate scoped fetch.
    """

    def __init__(self, path):
        self.path = str(path)

    def connect(self):
        return DaySnapshotStore(self.path).connect()

    def initialize(self):
        DaySnapshotStore(self.path).initialize()
        with self.connect() as conn:
            self._migrate_scope_keys(conn)
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS zaza_snapshot_jobs (
                    run_id TEXT NOT NULL, season INTEGER NOT NULL, phase TEXT NOT NULL, hero TEXT, player_rank TEXT NOT NULL,
                    stat_wins INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                    queued_at TEXT NOT NULL, next_attempt_at TEXT NOT NULL, started_at TEXT, lease_token TEXT, finished_at TEXT, updated_at TEXT NOT NULL,
                    last_error TEXT, last_http_status INTEGER, source TEXT NOT NULL DEFAULT 'zaza-rsc', PRIMARY KEY(run_id,season,phase)
                );
                CREATE TABLE IF NOT EXISTS zaza_combat_snapshots (
                    run_id TEXT NOT NULL, event_id TEXT NOT NULL, season INTEGER NOT NULL,
                    phase TEXT NOT NULL, hero TEXT, player_rank TEXT NOT NULL,
                    day INTEGER NOT NULL, hour INTEGER NOT NULL CHECK(hour=6),
                    captured_at TEXT, outcome TEXT, is_pvp INTEGER NOT NULL CHECK(is_pvp=1),
                    player_board_json TEXT NOT NULL, player_skills_json TEXT NOT NULL,
                    fetched_at TEXT NOT NULL, PRIMARY KEY(run_id,event_id,season,phase)
                ) WITHOUT ROWID;
                CREATE INDEX IF NOT EXISTS idx_zaza_snapshot_scope
                    ON zaza_combat_snapshots(run_id,season,phase,day);
            ''')

    def _migrate_scope_keys(self, conn):
        """Rebuild legacy Zaza tables whose keys omitted season/phase."""
        jobs = conn.execute("SELECT sql FROM sqlite_master WHERE name='zaza_snapshot_jobs'").fetchone()
        facts = conn.execute("SELECT sql FROM sqlite_master WHERE name='zaza_combat_snapshots'").fetchone()
        if jobs and ('PRIMARY KEY(run_id)' in (jobs[0] or '').replace(' ', '') or 'PRIMARY KEY (run_id)' in (jobs[0] or '')):
            conn.execute('ALTER TABLE zaza_snapshot_jobs RENAME TO zaza_snapshot_jobs_legacy')
            conn.execute('''CREATE TABLE zaza_snapshot_jobs (
                run_id TEXT NOT NULL, season INTEGER NOT NULL, phase TEXT NOT NULL, hero TEXT,
                player_rank TEXT NOT NULL, stat_wins INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0, queued_at TEXT NOT NULL, next_attempt_at TEXT NOT NULL,
                started_at TEXT, lease_token TEXT, finished_at TEXT, updated_at TEXT NOT NULL,
                last_error TEXT, last_http_status INTEGER, source TEXT NOT NULL DEFAULT 'zaza-rsc',
                PRIMARY KEY(run_id,season,phase))''')
            conn.execute('''INSERT INTO zaza_snapshot_jobs SELECT run_id,season,phase,hero,player_rank,stat_wins,status,attempts,queued_at,next_attempt_at,started_at,lease_token,finished_at,updated_at,last_error,last_http_status,source FROM zaza_snapshot_jobs_legacy''')
            conn.execute('DROP TABLE zaza_snapshot_jobs_legacy')
        if facts and ('PRIMARY KEY(run_id,event_id)' in (facts[0] or '').replace(' ', '') or 'PRIMARY KEY (run_id, event_id)' in (facts[0] or '')):
            conn.execute('ALTER TABLE zaza_combat_snapshots RENAME TO zaza_combat_snapshots_legacy')
            conn.execute('''CREATE TABLE zaza_combat_snapshots (
                run_id TEXT NOT NULL,event_id TEXT NOT NULL,season INTEGER NOT NULL,phase TEXT NOT NULL,
                hero TEXT,player_rank TEXT NOT NULL,day INTEGER NOT NULL,hour INTEGER NOT NULL,
                captured_at TEXT,outcome TEXT,is_pvp INTEGER NOT NULL,player_board_json TEXT NOT NULL,
                player_skills_json TEXT NOT NULL,fetched_at TEXT NOT NULL,
                PRIMARY KEY(run_id,event_id,season,phase)) WITHOUT ROWID''')
            conn.execute('''INSERT OR IGNORE INTO zaza_combat_snapshots SELECT run_id,event_id,season,phase,hero,player_rank,day,hour,captured_at,outcome,is_pvp,player_board_json,player_skills_json,fetched_at FROM zaza_combat_snapshots_legacy''')
            conn.execute('DROP TABLE zaza_combat_snapshots_legacy')


    def enqueue(self, run):
        now = _utc_text()
        with self.connect() as conn:
            conn.execute('''INSERT OR IGNORE INTO zaza_snapshot_jobs
                (run_id,season,phase,hero,player_rank,stat_wins,queued_at,next_attempt_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?)''',
                (run['id'],run['season'],run['phase'],run.get('hero'),run.get('player_rank') or 'Unknown',run.get('stat_wins') or 0,now,now,now))

    def enqueue_if_eligible(self, *args, **kwargs):
        # Global admission belongs exclusively to DaySnapshotStore.
        return False

    def claim_run(self, run_id, season=None, phase=None):
        now = _utcnow()
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            query = 'SELECT * FROM zaza_snapshot_jobs WHERE run_id=?'
            args = [run_id]
            if season is not None and phase is not None:
                query += ' AND season=? AND phase=?'; args += [season, phase]
            row = conn.execute(query, args).fetchone()
            if row is None:
                return None
            params = (run_id, row['season'], row['phase'])
            for table in ('combat_snapshots', 'zaza_combat_snapshots'):
                if conn.execute(f'SELECT 1 FROM {table} WHERE run_id=? AND season=? AND phase=? AND hour=6 AND is_pvp=1 LIMIT 1', params).fetchone():
                    return None
            global_job = conn.execute('SELECT status FROM snapshot_jobs WHERE run_id=? AND season=? AND phase=?', params).fetchone()
            if global_job is not None and global_job['status'] not in ('success', 'failed'):
                return None
            if row['status'] in ('failed', 'missing'):
                return None
            if row['status'] == 'processing' and row['started_at']:
                started = datetime.fromisoformat(row['started_at'].replace('Z', '+00:00'))
                if started.tzinfo is None:
                    # Existing databases stored local naive timestamps; interpret them
                    # in the host local zone before comparing with UTC.
                    started = started.replace(tzinfo=datetime.now().astimezone().tzinfo)
                if started >= now-timedelta(minutes=10):
                    return None
            if row['status'] not in ('processing', 'missing') and row['next_attempt_at'] > _utc_text(now):
                return None
            if row['attempts'] >= 5:
                conn.execute("UPDATE zaza_snapshot_jobs SET status='failed',lease_token=NULL WHERE run_id=? AND season=? AND phase=?", params)
                return None
            token = uuid.uuid4().hex
            conn.execute("UPDATE zaza_snapshot_jobs SET status='processing',lease_token=?,attempts=attempts+1,started_at=?,updated_at=? WHERE run_id=? AND season=? AND phase=?", (token,_utc_text(now),_utc_text(now),run_id,row['season'],row['phase']))
            return {'run_id':run_id,'season':row['season'],'phase':row['phase'],'lease_token':token}

    def _processing_job(self, conn, run_id, lease_token):
        row = conn.execute("SELECT * FROM zaza_snapshot_jobs WHERE run_id=? AND season=? AND phase=? AND status='processing' AND lease_token=?", (run_id, self._scope_for_lease(conn,run_id,lease_token)[0], self._scope_for_lease(conn,run_id,lease_token)[1], lease_token)).fetchone()
        if row is None:
            raise ValueError('snapshot job lease is stale or not claimed')
        return row

    @staticmethod
    def _scope_for_lease(conn, run_id, token):
        row = conn.execute('SELECT season,phase FROM zaza_snapshot_jobs WHERE run_id=? AND lease_token=?', (run_id,token)).fetchone()
        return (row['season'], row['phase']) if row else (None, None)

    def release(self, job):
        with self.connect() as conn:
            conn.execute("UPDATE zaza_snapshot_jobs SET status='retry',lease_token=NULL,attempts=MAX(0,attempts-1) WHERE run_id=? AND status='processing' AND lease_token=?", (job['run_id'],job['lease_token']))

    def complete_success(self, run_id, snapshots, lease_token, *, http_status=200):
        for snap in snapshots:
            if not isinstance(snap, dict) or any(not isinstance(snap.get(field, []), list) for field in ('playerBoard', 'playerSkills')):
                raise ValueError('combat snapshot board/skills must be resolved arrays')
        valid = [s for s in snapshots if isinstance(s, dict)
                 and isinstance(s.get('eventId'), str) and 0 < len(s['eventId']) <= 128
                 and type(s.get('day')) is int and s['day'] >= 1
                 and type(s.get('hour')) is int and s['hour'] == 6
                 and (s.get('combat') or {}).get('is_pvp') in (True, 1, 'true', '1')]
        if not valid:
            raise ValueError('combat snapshots contain no valid hour6 PvP entries')
        now = _utc_text()
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            job = self._processing_job(conn, run_id, lease_token)
            for snap in valid:
                conn.execute('''INSERT OR IGNORE INTO zaza_combat_snapshots
                    (run_id,event_id,season,phase,hero,player_rank,day,hour,captured_at,
                     outcome,is_pvp,player_board_json,player_skills_json,fetched_at)
                    VALUES(?,?,?,?,?,?,?,6,?,?,1,?,?,?)''',
                    (run_id,snap['eventId'],job['season'],job['phase'],job['hero'],job['player_rank'],
                     snap['day'],snap.get('ts'),snap['combat'].get('outcome'),
                     json.dumps(snap.get('playerBoard',[]),ensure_ascii=False),
                     json.dumps(snap.get('playerSkills',[]),ensure_ascii=False),now))
            conn.execute("UPDATE zaza_snapshot_jobs SET status='success',last_error=NULL,last_http_status=?,finished_at=?,updated_at=?,lease_token=NULL WHERE run_id=? AND season=? AND phase=? AND lease_token=?", (http_status,now,now,run_id,job['season'],job['phase'],lease_token))

    def complete_failure(self, run_id, error, lease_token, *, error_type=None):
        now = _utcnow()
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            job = self._processing_job(conn,run_id,lease_token)
            attempts = job['attempts']
            status = 'failed' if attempts >= 5 else 'retry'
            due = _utc_text(now+timedelta(minutes=(5,15,60,180)[min(attempts-1,3)]))
            conn.execute('UPDATE zaza_snapshot_jobs SET status=?,last_error=?,next_attempt_at=?,updated_at=?,lease_token=NULL WHERE run_id=? AND season=? AND phase=? AND lease_token=?', (status,str(error)[:1000],due,_utc_text(now),run_id,job['season'],job['phase'],lease_token))

    def complete_missing(self, run_id, lease_token, *, http_status=200):
        now = _utc_text()
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            job = self._processing_job(conn, run_id, lease_token)
            conn.execute("UPDATE zaza_snapshot_jobs SET status='missing',last_http_status=?,finished_at=?,updated_at=?,lease_token=NULL WHERE run_id=? AND season=? AND phase=? AND lease_token=?", (http_status,now,now,run_id,job['season'],job['phase'],lease_token))

    def status(self, season=None, phase=None):
        with self.connect() as conn:
            where = '' if season is None or phase is None else ' WHERE season=? AND phase=?'
            args = () if not where else (season, phase)
            jobs = {r[0]:r[1] for r in conn.execute('SELECT status,count(*) FROM zaza_snapshot_jobs'+where+' GROUP BY status', args)}
            fact_where = '' if not where else ' AND season=? AND phase=?'
            fact_args = () if not where else (season, phase)
            count, runs = conn.execute('''SELECT count(*),count(DISTINCT run_id) FROM (
                SELECT run_id,event_id FROM combat_snapshots WHERE hour=6 AND is_pvp=1''' + fact_where + '''
                UNION SELECT run_id,event_id FROM zaza_combat_snapshots WHERE hour=6 AND is_pvp=1''' + fact_where + ''')''', fact_args + fact_args).fetchone()
        return {'jobs':jobs,'snapshots':count,'runs_with_snapshots':runs,'reused_valid_facts':count}


def _interested(store, run_id, season, phase):
    with store.connect() as conn:
        return conn.execute('''SELECT 1 FROM zaza_run_interest i JOIN zaza_spaces s ON s.id=i.space_id
            WHERE i.run_id=? AND i.season=? AND i.phase=? AND i.active=1 AND s.status='active'
            AND s.current_season=? AND s.current_phase=? LIMIT 1''',(run_id,season,phase,season,phase)).fetchone() is not None


def capture_once(store, snapshots, *, season, phase, session=None, enabled=lambda: True, limit=5):
    if not enabled():
        return 0
    with store.connect() as conn:
        rows=conn.execute('''SELECT DISTINCT i.run_id FROM zaza_run_interest i JOIN zaza_spaces s ON s.id=i.space_id
            WHERE i.active=1 AND i.season=? AND i.phase=? AND s.status='active' ORDER BY i.run_id''',(season,phase)).fetchall()
    count=0
    owned=session is None
    session=session or _session()
    try:
        for row in rows:
            if count>=limit or not enabled():
                break
            if not _interested(store,row['run_id'],season,phase):
                continue
            job=snapshots.claim_run(row['run_id'], season, phase)
            if not job:
                continue
            count+=1
            target=snapshots
            try:
                response=_fetch(session,'/run/tracker/'+job['run_id'])
                facts=extract_zaza_combat_snapshots(response.text)
                parsed = _parse_tracker_payload(response.text)
                valid_tracker = isinstance(parsed, dict) and isinstance(parsed.get('snapshots'), list) and isinstance(parsed.get('combats'), list)
                if not facts and not valid_tracker:
                    raise RuntimeError('malformed tracker response')
                # Serialize completion with stop/delete/scope transitions. A stopped
                # subscriber cannot cause new facts to become visible through its view.
                with store.connect() as conn:
                    conn.execute('BEGIN IMMEDIATE')
                    if not enabled() or not _interested(store,job['run_id'],season,phase):
                        snapshots.release(job)
                    else:
                        if facts:
                            target.complete_success(job['run_id'],facts,job['lease_token'],http_status=response.status_code)
                        else:
                            target.complete_missing(job['run_id'],job['lease_token'],http_status=response.status_code)
            except Exception as exc:
                LOG.warning('zaza capture failed for %s: %s',job['run_id'],exc)
                try:
                    target.complete_failure(job['run_id'],str(exc),job['lease_token'],error_type=type(exc).__name__)
                except ValueError:
                    pass  # A newer claimant owns the lease.
    finally:
        if owned:
            session.close()
    return count


def process_once(store, runs_path, *, season, phase, session=None, snapshots_path=None, enabled=lambda: True, discovery_ready=None,
                 created_after=None, created_before=None):
    if not enabled():
        return 0
    snapshots=ZazaSnapshotStore(snapshots_path or Path(store.path).with_name('day_snapshots.db'))
    snapshots.initialize()
    store.schedule_sync(season,phase)
    jobs=store.claim_outbox(limit=5)
    for job in jobs:
        space=store.get(job['space_id'])
        if not space or space['status'] not in ('creating','active','failed'):
            continue
        try:
            if discovery_ready is not None and not discovery_ready(space, job):
                # Browser acquisition is asynchronous. Keep the existing lease
                # fenced and retry this outbox without marking the space failed.
                with store.connect() as conn:
                    conn.execute("UPDATE zaza_outbox SET status='pending',lease_token=NULL WHERE id=? AND lease_token=?", (job['id'],job['lease_token']))
                continue
            profile=validate_profile(space['profile_id'],session=session)
            runs=store.local_runs(Path(runs_path),space['profile_id'],season,phase,
                                  created_after=created_after,created_before=created_before)
            if not enabled():
                store.complete_outbox(job['id'],job['lease_token'],error='collection paused')
                continue
            current=store.mark_collection(space['id'],season=season,phase=phase,run_ids=[str(r['id']) for r in runs],profile=profile,job=job,
                                          created_after=created_after,created_before=created_before)
            if current['status']=='active' and current['generation']==_json(job['payload_json'],{}).get('generation'):
                for run in runs:
                    snapshots.enqueue(run)
            store.complete_outbox(job['id'],job['lease_token'])
        except Exception as exc:
            LOG.warning('zaza discovery failed for %s: %s',space['id'],exc)
            store.mark_collection(space['id'],season=season,phase=phase,run_ids=[],error=str(exc),job=job)
            try:
                store.complete_outbox(job['id'],job['lease_token'],error=str(exc))
            except ValueError:
                pass
    capture_once(store,snapshots,season=season,phase=phase,session=session,enabled=enabled)
    return len(jobs)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once',action='store_true')
    parser.add_argument('--interval',type=int,default=60)
    parser.add_argument('--runs-db',default=os.environ.get('RUNS_DB_PATH','/opt/qiubot/data/bazaar_runs.db'))
    parser.add_argument('--spaces-db',default=os.environ.get('ZAZA_SPACE_DB_PATH','/opt/qiubot/data/zaza_spaces.db'))
    parser.add_argument('--snapshots-db',default=os.environ.get('DAY_SNAPSHOT_DB_PATH','/opt/qiubot/data/bazaar_day_snapshots.db'))
    from plugins.bazaar_plugin.data_client import RUNS_SEASON_ID, CURRENT_PHASE
    parser.add_argument('--season',type=int,default=int(os.environ.get('RUNS_SEASON_ID',RUNS_SEASON_ID)))
    parser.add_argument('--phase',default=os.environ.get('CURRENT_PHASE',CURRENT_PHASE))
    args=parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    store=ZazaSpaceStore(args.spaces_db)
    store.initialize()
    enabled=lambda: os.environ.get('ENABLE_DAY_SNAPSHOT_QUEUE','0')=='1'
    while True:
        try:
            count=process_once(store,args.runs_db,season=args.season,phase=args.phase,snapshots_path=args.snapshots_db,enabled=enabled)
            LOG.info('discovery jobs processed=%d',count)
        except Exception:
            LOG.exception('zaza worker cycle failed')
        if args.once:
            return
        time.sleep(max(10,args.interval))


if __name__=='__main__':
    main()
