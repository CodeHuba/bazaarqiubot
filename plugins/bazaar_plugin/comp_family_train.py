"""Refresh the QQ composition model from a read-only SQLite snapshot."""

from collections import defaultdict
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time

from .comp_analysis import HEROES, timestamp
from .comp_core_extension import extend_cores
from .comp_family_model import HeroPatterns, MIN_RUNS
from .comp_family_query import MODEL_FILE, SCHEMA, load_model
from .data_client import RUNS_SEASON_ID, CURRENT_PHASE
from .run_instances import terminal_instances

def read_boards(path, season, phase, watermark):
    uri = Path(path).resolve().as_uri() + '?mode=ro'
    boards = defaultdict(list)
    dated_boards = defaultdict(list)
    arrivals = defaultdict(set)
    newest = 0
    with closing(sqlite3.connect(uri, uri=True, timeout=5)) as conn:
        rows = conn.execute(
            """SELECT rowid, hero, items_json, created_at,
                      CASE WHEN items_json IS NULL AND json_valid(raw_json)
                           THEN json_extract(raw_json, '$.items') END, stat_wins
                 FROM runs WHERE season=? AND phase=? ORDER BY rowid""",
            (season, phase),
        )
        for rowid, hero, compact, created_at, original, wins in rows:
            newest = max(newest, rowid)
            if hero not in HEROES or type(wins) is not int or not 0 <= wins <= 10:
                continue
            items = {card['cardId'] for card in terminal_instances(
                compact if compact is not None else original, {}, 'items')}
            if items:
                instant = timestamp(created_at)
                if instant is not None:
                    dated_boards[hero].append((instant, 1 << len(boards[hero])))
                boards[hero].append(items)
                if rowid > watermark:
                    arrivals[hero].update(items)
    return boards, dated_boards, arrivals, newest


def publish_families(prior, candidates, arrivals, extensions=None):
    published = {}
    extensions = extensions or {}
    for anchor in sorted(prior.keys() | candidates.keys()):
        candidate = candidates.get(anchor, [])
        proposed_extensions = extensions.get(anchor, {})
        proposed = {'families': candidate}
        if proposed_extensions:
            proposed['extensions'] = proposed_extensions
        state = prior.get(anchor)
        if not state:
            if candidate:
                published[anchor] = proposed
            continue
        if anchor not in arrivals:
            published[anchor] = state
        else:
            current = state['families'], state.get('extensions', {})
            pending = state.get('pending'), state.get('pending_extensions', {})
            if (candidate, proposed_extensions) in (current, pending):
                published[anchor] = proposed
            else:
                published[anchor] = {**state, 'pending': candidate}
                published[anchor].pop('pending_extensions', None)
                if proposed_extensions:
                    published[anchor]['pending_extensions'] = proposed_extensions
    return published


def write_model(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                     delete=False, prefix='.comp-family-') as handle:
        temporary = Path(handle.name)
        try:
            json.dump(data, handle, ensure_ascii=False, separators=(',', ':'))
            handle.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def refresh(db, output, season, phase):
    started = time.monotonic()
    prior = load_model(output, season, phase)
    boards, dated_boards, arrivals, newest = read_boards(db, season, phase, prior.get('watermark', 0))
    if prior.get('watermark') == newest:
        print('Composition model already current', flush=True)
        return
    models = {}
    for hero, hero_boards in sorted(boards.items()):
        old = prior.get('heroes', {}).get(hero, {})
        if old and not arrivals[hero]:
            models[hero] = old
            continue
        trainer = HeroPatterns(hero, hero_boards)
        dated = sorted(dated_boards[hero])
        candidates = {}
        extensions = {}
        for anchor, bits in sorted(trainer.cards.items()):
            if bits.bit_count() >= MIN_RUNS and (not old or anchor in arrivals[hero]):
                families = trainer.families(anchor)
                if families:
                    candidates[anchor] = families
                    extensions[anchor] = extend_cores(anchor, families, trainer.cards, dated)
        models[hero] = publish_families(old, candidates, arrivals[hero], extensions)
        print(f'{hero}: {len(hero_boards)} runs, {len(models[hero])} cards, '
              f'{time.monotonic()-started:.1f}s elapsed', flush=True)
    write_model(output, dict(schema=SCHEMA, season=season, phase=phase, watermark=newest, heroes=models))
    print(f'Published composition model for {season}/{phase}, rowid {newest}', flush=True)


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument('--db', default=os.getenv('RUNS_DB_PATH', '/opt/qiubot/data/bazaar_runs.db'))
    parser.add_argument('--output', type=Path, default=MODEL_FILE)
    args = parser.parse_args()
    refresh(args.db, args.output, RUNS_SEASON_ID, CURRENT_PHASE)
