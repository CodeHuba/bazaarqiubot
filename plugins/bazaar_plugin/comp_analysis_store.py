"""Versioned snapshots: refresh off the request path and atomically publish."""
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import hashlib
import logging
from pathlib import Path
import threading
import time

from .comp_analysis import Analysis
from .run_instances import json_value, terminal_instances
from .runs_metadata import file_version

_engines = OrderedDict()
_lock = threading.RLock()
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='comp-refresh')
_watchers = set()
_ASSET_CATALOG = Path(__file__).resolve().parents[2] / 'web_runs/static/comp-art/catalog.json'
_INSTANCE_FIELDS = ('cardId', 'tier', 'tierOverride', 'isLegendary', 'enchantment', 'slotPosition')
_logger = logging.getLogger(__name__)


@dataclass
class Entry:
    factory: object
    season: int
    phase: str
    engine: object = None
    signature: object = None
    revision: int = 0
    built_revision: int = -1
    refreshing: bool = False
    error: object = None
    failed_at: float = 0
    builds: int = 0
    ready: object = field(default_factory=threading.Event)


def _instances(compact, original, kind):
    return [{key: card[key] for key in _INSTANCE_FIELDS if key in card}
            for card in terminal_instances(compact, original, kind)]


def _signature(query):
    return (file_version(query.db_path), file_version(str(query.db_path)+'-wal'),
            query.metadata_version(), file_version(_ASSET_CATALOG),
            getattr(query, 'analysis_snapshot_time', None))


def _row_digest(row):
    digest = hashlib.sha256()
    for value in row:
        data = b'N' if value is None else type(value).__name__.encode()+b':'+str(value).encode('utf-8')
        digest.update(len(data).to_bytes(8, 'little'))
        digest.update(data)
    return digest.digest()


def _build(query, season, phase, previous=None):
    try:
        if not Path(query.db_path).is_file():
            raise FileNotFoundError('Analysis source database is unavailable')
        query.load()
        query.conn.execute('PRAGMA query_only=ON')
        signature = _signature(query)
        columns = {r[1] for r in query.conn.execute('PRAGMA table_info(runs)')}
        raw = 'raw_json' if 'raw_json' in columns else 'NULL'
        rows = query.conn.execute(
            'SELECT id,hero,created_at,items_json,skills_json,stat_wins,player_rank,screenshot_url,' + raw +
            ' FROM runs WHERE season=? AND phase=?', (season, phase))
        records = []
        previous_rows = {r['id']: r for r in previous.runs} if previous else {}
        for r in rows:
            fingerprint = _row_digest(r)
            old = previous_rows.get(r[0])
            if old is not None and old.get('_source_digest') == fingerprint:
                records.append(old)
                continue
            original = json_value(r[8], {})
            records.append(dict(id=r[0], hero=r[1], created_at=r[2],
                                items=_instances(r[3], original, 'items'),
                                skills=_instances(r[4], original, 'skills'),
                                    wins=r[5], rank=r[6], screenshot=r[7], _source_digest=fingerprint))
        try:
            assets = json.loads(_ASSET_CATALOG.read_text(encoding='utf-8'))
            if not isinstance(assets, dict):
                assets = {}
        except (OSError, ValueError):
            assets = {}
        catalog = {}
        ids = set(query.card_mapping)
        ids.update(c['cardId'] for row in records for c in row['items'] + row['skills'])
        for cid in ids:
            display = query.card_display_info_cached(cid)
            image = query._card_image_info(cid)
            catalog[cid] = {'name': display['name'], 'internalName': display['name_en'],
                            'size': display['size'], 'artLarge': display['img'],
                            'type': image.get('type') or query._safe_mapping_info(cid).get('type') or 'Item',
                            'heroes': query.card_heroes.get(cid, image.get('heroes'))}
            asset = assets.get(cid)
            if isinstance(asset, dict):
                catalog[cid].update({k: asset[k] for k in ('name', 'internalName', 'size', 'type', 'artLarge') if asset.get(k)})
        engine = Analysis({'runs': records, 'cards': catalog, 'season': season, 'phase': phase,
                           'captured_at': getattr(query, 'analysis_snapshot_time', None) or datetime.now(timezone.utc).isoformat(),
                           'live': not bool(getattr(query, 'analysis_snapshot_time', None)),
                           'source': '当前赛季、当前阶段采集记录；原始采集不含 0 胜局'})
        engine.metadata()
        return engine, signature
    finally:
        if query.conn is not None:
            query.conn.close()


def _refresh(entry):
    with _lock:
        revision = entry.revision
        previous = entry.engine
    try:
        engine, signature = _build(entry.factory(), entry.season, entry.phase, previous)
        with _lock:
            entry.engine, entry.signature = engine, signature
            entry.built_revision = revision
            entry.builds += 1
            entry.error = None
        del previous  # Large old snapshots are released outside the publication lock.
    except Exception as exc:
        with _lock:
            entry.error, entry.failed_at = exc, time.monotonic()
        _logger.warning('Composition snapshot refresh failed: %s', type(exc).__name__)
    finally:
        with _lock:
            entry.refreshing = False
            entry.ready.set()


def _schedule(entry):
    # Only one worker builds at a time. Requests keep their old immutable engine
    # reference while a new snapshot is prepared, without holding this lock.
    if entry.refreshing or (entry.error and time.monotonic()-entry.failed_at < 5):
        return
    entry.refreshing = True
    entry.ready.clear()
    _executor.submit(_refresh, entry)


def _entry(query, season, phase, factory=None):
    key = (str(Path(query.db_path).resolve()), season, phase)
    signature = _signature(query)
    with _lock:
        entry = _engines.get(key)
        if entry is None:
            if factory is None:
                cls = type(query)
                options = dict(db_path=query.db_path, mapping_path=query.mapping_path, translations_path=query.translations_path)
                factory = lambda: cls(**options)
            entry = Entry(factory, season, phase)
            if len(_engines) >= 4:
                removable = next((k for k,v in _engines.items() if not v.refreshing), None)
                if removable is None:
                    raise RuntimeError('分析缓存正在预热，请稍后重试')
                _engines.pop(removable)
            _engines[key] = entry
        _engines.move_to_end(key)
        if entry.engine is None or entry.built_revision != entry.revision or entry.signature != signature:
            _schedule(entry)
        return entry


def invalidate(*, hard=False):
    with _lock:
        if hard:
            _engines.clear()
            return
        for entry in _engines.values():
            entry.revision += 1
            _schedule(entry)


def _wait(entry):
    if not entry.ready.wait(45):
        raise TimeoutError('阵容分析正在预热，请稍后重试')
    with _lock:
        if entry.engine is None:
            raise RuntimeError('阵容分析暂时无法读取') from entry.error
        return entry.engine


def prewarm(factory, *, season, phase, wait=False):
    query = factory()
    entry = _entry(query, season, phase, factory)
    return _wait(entry) if wait else entry


def start_background_refresh(factory, *, season, phase):
    """Warm on startup and detect external DB/resource changes within 15 seconds."""
    query = factory()
    key = (str(Path(query.db_path).resolve()), season, phase)
    with _lock:
        if key in _watchers:
            return
        _watchers.add(key)
    def maintain():
        while True:
            try:
                prewarm(factory, season=season, phase=phase)
            except Exception as exc:
                _logger.warning('Composition cache check failed: %s', type(exc).__name__)
            time.sleep(15)
    threading.Thread(target=maintain, name='comp-cache-watch', daemon=True).start()


def cache_status():
    with _lock:
        return [dict(season=e.season, phase=e.phase, ready=e.engine is not None,
                     builds=e.builds,
                     refreshing=e.refreshing, source_runs=e.engine.source_count if e.engine else 0,
                     captured_at=e.engine.payload['captured_at'] if e.engine else None,
                     error=type(e.error).__name__ if e.error else None) for e in _engines.values()]


def analyze(query, card_ids, *, season, phase, hero='', rank='all', days=None, sort='reliable'):
    entry = _entry(query, season, phase)
    with _lock:
        engine = entry.engine
    if engine is None:
        engine = _wait(entry)
    return engine.query(card_ids, hero=hero, rank=rank, days=days, sort=sort)
