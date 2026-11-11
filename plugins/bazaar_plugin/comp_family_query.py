"""Apply published composition groups to the live filtered run snapshot."""

from functools import lru_cache
import json
import logging
from pathlib import Path
import time

from .comp_analysis import HEROES, metric, timestamp
from .comp_core_extension import core_key
from .query_budget import budgeted_rows

MODEL_FILE = Path(__file__).resolve().parents[2] / 'data/comp-family-model.json'
SCHEMA = 1
RELATED_LIMIT = 5
BRANCH_LIMIT = 3
logger = logging.getLogger(__name__)


@lru_cache(maxsize=2)
def _read_model(path, signature):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        logger.warning('Cannot load QQ composition model: %s', type(error).__name__)
        return {}


def load_model(path, season, phase):
    try:
        st = path.stat()
    except FileNotFoundError:
        return {}
    model = _read_model(path, (st.st_ino, st.st_mtime_ns, st.st_size))
    if not isinstance(model, dict) or (model.get('schema'), model.get('season'), model.get('phase')) != (SCHEMA, season, phase):
        return {}
    return model


def query_families(engine, anchor, hero, rank, days, model, *, allow_empty=False):
    if not model:
        return None
    published = model['heroes']
    captured = time.time() if engine.live else timestamp(engine.payload['captured_at'])
    cutoff = captured - days * 86400 if days else None
    runs = [row for row in budgeted_rows(engine.runs[i] for i in engine.item_index.get(anchor, ()))
            if (not hero or row['hero'] == hero)
            and (rank != 'legendary' or row.get('rank') == 'Legendary')
            and (cutoff is None or (timestamp(row.get('created_at')) or 0) >= cutoff)]
    by_hero = {}
    for row in runs:
        by_hero.setdefault(row['hero'], []).append(row)
    candidates = []
    for name, rows in by_hero.items():
        definition = published.get(name, {}).get(anchor, {})
        extensions = definition.get('extensions', {})
        for original in budgeted_rows(definition.get('families', ())):
            variants = sorted({tuple(extensions.get(core_key(core), {}).get('core', core))
                               for core in original})
            branch_cores = {tuple(branch) for core in original
                            for branch in extensions.get(core_key(core), {}).get('branches', ())}
            branch_cores.difference_update(variants)
            cores = [frozenset(core) for core in variants]
            variant_counts = [0] * len(cores)
            matched = []
            for row in rows:
                has_variant = False
                for index, core in enumerate(cores):
                    if core <= row['item_ids']:
                        variant_counts[index] += 1
                        has_variant = True
                if has_variant:
                    matched.append(row)
            if len(matched) >= 10:
                candidates.append((name, variants, variant_counts, matched, branch_cores))

    candidates.sort(key=lambda x: (-len(x[3]), x[0], x[1]))
    regular = any(len(candidate[3]) >= 30 for candidate in candidates)
    threshold, limit = (30, 3) if regular else (10, 2)
    groups = []
    covered = set()
    for name, variants, counts, rows, branch_cores in candidates:
        if len(rows) < threshold or len(groups) == limit:
            break
        ids = {row['id'] for row in rows}
        fresh = len(ids - covered)
        if fresh < threshold or fresh / len(ids) < .35:
            continue
        covered.update(ids)
        excluded = {anchor}.union(*(set(variant) for variant in variants))
        active = sorted(((-count, variant) for variant, count in zip(variants, counts) if count),
                        key=lambda pair: (pair[0], pair[1]))
        core_order = list(dict.fromkeys(cid for _, variant in active for cid in variant))
        branches = []
        for branch in sorted(branch_cores):
            required = frozenset(branch)
            matches = [row for row in rows if required <= row['item_ids']]
            if len(matches) >= 10:
                display = [cid for cid in core_order if cid in required]
                display.extend(cid for cid in branch if cid not in display)
                branches.append(metric(matches, len(runs)) | {
                    'cards': [engine.card(cid) for cid in display],
                })
        branches.sort(key=lambda branch: (-branch['count'], [card['cardId'] for card in branch['cards']]))
        branches = branches[:BRANCH_LIMIT]
        excluded.update(card['cardId'] for branch in branches for card in branch['cards'])
        groups.append(metric(rows, len(runs)) | {
            'id': name + ':' + '|'.join(','.join(variant) for variant in variants),
            'hero': name, 'hero_name': HEROES.get(name, name),
            'variants': [[engine.card(cid) for cid in variant] for _, variant in active],
            'branches': branches,
            'core': [engine.card(anchor)] + [engine.card(cid) for cid in active[0][1]],
            'related_items': engine.companions(rows, excluded, 'items')[:RELATED_LIMIT],
            'related_skills': engine.companions(rows, excluded, 'skills')[:RELATED_LIMIT],
        })
    if runs and not groups and not allow_empty:
        return None
    return {'scope': engine.metadata() | {'hero': hero, 'rank': rank, 'days': days},
            'anchors': [engine.card(anchor)], 'baseline': metric(runs, len(runs)),
            'groups': groups, 'view': 'qq'}



def query_qq(engine, anchor, hero, rank, days, model):
    if not model:
        return None
    result = query_families(engine, anchor, hero, rank, days, model)
    if result is not None:
        return result
    # Reuse the ordinary engine's small-sample candidates when the learned
    # groups lack support under the current filters.
    fallback = engine.query([anchor], hero=hero, rank=rank, days=days, sort='appearance')
    heroes = {}
    for group in fallback['groups']:
        definition = heroes.setdefault(group['hero'], {anchor: {'families': []}})
        definition[anchor]['families'].append([group['extra_ids']])
    return query_families(engine, anchor, hero, rank, days, {'heroes': heroes}, allow_empty=True)
