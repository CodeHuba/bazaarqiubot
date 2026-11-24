"""Bounded, shared terminal-day statistics; only matching runs decode combats."""
from collections import OrderedDict
from concurrent.futures import Future
from copy import deepcopy
from fractions import Fraction
import json
from pathlib import Path
import sqlite3
import threading
import time

from .query_budget import BoundedTTLCache, budgeted_rows, checkpoint

MIN_TERMINAL_DAY = 10
CACHE = BoundedTTLCache(64)
_pending = {}
_lock = threading.Lock()
_version_lock = threading.Lock()
_observers = OrderedDict()


def terminal_day(raw):
    try:
        combats = json.loads(raw or '[]')
    except (ValueError, TypeError):
        return None
    if not isinstance(combats, list):
        return None
    return max((c['d'] for c in combats if isinstance(c, dict)
                and type(c.get('d')) is int and c['d'] > 0), default=None)


def highlights(rows):
    rows = [row for row in rows if row['day'] >= MIN_TERMINAL_DAY and row['total'] > 0]
    if not rows:
        return [], []
    peak = max(row['total'] for row in rows)
    rates = [Fraction(row['ten_win'], row['total']) for row in rows]
    best = max(rates)
    return ([row for row in rows if row['total'] == peak],
            [row for row, rate in zip(rows, rates) if rate == best])


def _version(query):
    path = getattr(query, 'db_path', ':memory:')
    if path == ':memory:':
        return None
    path = Path(path).resolve()
    # data_version is comparable across measurements on the same connection.
    # A read-only observer does not hold a transaction or pin the WAL snapshot.
    with _version_lock:
        entry = _observers.pop(path, None)
        try:
            stat = path.stat()
            identity = (stat.st_dev, stat.st_ino)
            if entry and entry[0] != identity:
                entry[2].close()
                entry = None
            if entry is None:
                conn = sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=.25,
                                       check_same_thread=False)
                entry = identity, object(), conn
            version = entry[2].execute('PRAGMA data_version').fetchone()[0]
            _observers[path] = entry
            if len(_observers) > 4:
                _observers.popitem(last=False)[1][2].close()
            return path, entry[1], version
        except (OSError, sqlite3.Error):
            if entry:
                entry[2].close()
            return None


def aggregate(query, sql, params, groups, threshold, scope, filters):
    """Cache a complete snapshot (including its time bounds), never partial rows."""
    version = _version(query)
    key = (version if version else id(query.conn), filters,
           tuple(sorted(tuple(sorted(g)) for g in groups)), threshold)
    # Skip the cache when we cannot reliably detect database updates.
    with _lock:
        cached = CACHE.get(key) if version else None
        if cached:
            return deepcopy(cached[0])
        future = _pending.get(key)
        leader = future is None
        if leader:
            _pending[key] = future = Future()
    if not leader:
        while not future.done():
            checkpoint()
            try:
                return deepcopy(future.result(timeout=.1))
            except TimeoutError:
                if future.done():
                    return deepcopy(future.result())
        return deepcopy(future.result())
    try:
        result = _compute(query.conn, sql, params, groups, threshold, scope)
        if version and _version(query) == version:
            CACHE[key] = (deepcopy(result), time.time()+60)
        future.set_result(result)
        return deepcopy(result)
    except BaseException as exc:
        future.set_exception(exc)
        raise
    finally:
        with _lock:
            _pending.pop(key, None)


def _compute(conn, sql, params, groups, threshold, scope):
    counts = {}
    unknown = {'total': 0, 'ten_win': 0}
    # Both reads must describe the same database snapshot while ingestion runs.
    conn.execute('SAVEPOINT winrate_terminal_days')
    try:
        index_ready = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                                   "AND name='runs_query_cards_ready_v1'").fetchone()
        if index_ready:
            # Existing runs deployments maintain a card-to-run index at ingest.
            # Intersect the candidate sets, then validate their stored card IDs.
            candidate_sql = []
            candidate_params = []
            for group in groups:
                marks = ','.join('?' for _ in group)
                candidate_sql.append(f'SELECT run_id FROM runs_query_cards_v1 WHERE card_id IN ({marks})')
                candidate_params.extend(sorted(group))
            candidates = ' INTERSECT '.join(candidate_sql)
            sql += f' AND id IN ({candidates})'
            params = [*params, *candidate_params]
        matched = {}
        for run_id, text, wins in budgeted_rows(conn.execute(sql, params)):
            if not text:
                continue
            ids = set(text.split())
            if all(ids.intersection(group) for group in groups):
                matched[run_id] = int((wins or 0) >= threshold)
        run_ids = list(matched)
        for offset in range(0, len(run_ids), 300):
            checkpoint()
            batch = run_ids[offset:offset+300]
            rows = conn.execute('SELECT id, combats_json FROM runs WHERE id IN ('+
                                ','.join('?' for _ in batch)+')', batch)
            for run_id, combats in budgeted_rows(rows):
                day = terminal_day(combats)
                if day is None:
                    row = unknown
                elif day >= MIN_TERMINAL_DAY:
                    row = counts.setdefault(day, {'day': day, 'total': 0, 'ten_win': 0})
                else:
                    continue  # Early endings only contribute to the overall stats.
                row['total'] += 1
                row['ten_win'] += matched[run_id]
        total, wins = len(matched), sum(matched.values())
        distribution = [counts[day] for day in sorted(counts)]
        for row in [*distribution, unknown]:
            row['rate'] = row['ten_win']/row['total'] if row['total'] else None
        return {'total': total, 'ten_win': wins, 'rate': wins/total if total else 0.0,
                'day_distribution': distribution, 'unknown_day': unknown,
                'scope': scope}
    finally:
        conn.execute('RELEASE winrate_terminal_days')
