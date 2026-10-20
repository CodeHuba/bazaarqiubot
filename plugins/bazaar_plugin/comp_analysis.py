"""Composition analysis: one run is one observation, including repeated builds."""
from collections import Counter, defaultdict
from concurrent.futures import Future
from datetime import datetime
from itertools import combinations
from math import sqrt
import threading
import time

HEROES = {'Vanessa': '瓦内莎', 'Dooley': '杜利', 'Mak': '马克', 'Pygmalien': '皮格马利翁',
          'Stelle': '斯黛尔', 'Jules': '朱尔斯', 'Karnok': '卡诺克', 'The Dragons': '双龙'}


def lower_bound(wins, count):
    if not count:
        return 0.0
    p, z = wins / count, 1.96
    return (p + z*z/(2*count) - z*sqrt(p*(1-p)/count + z*z/(4*count*count))) / (1 + z*z/count)


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return parsed.timestamp() if parsed.tzinfo else None
    except (ValueError, TypeError, OverflowError):
        return None


def metric(runs, denominator):
    count = len(runs)
    wins = sum(r['wins'] >= 10 for r in runs)
    return {'count': count, 'ten_wins': wins, 'rate': wins/count if count else 0,
            'average_wins': sum(r['wins'] for r in runs)/count if count else 0,
            'appearance_rate': count/denominator if denominator else 0,
            'lower_bound': lower_bound(wins, count),
            'sample_level': 'explore' if count < 30 else 'regular'}


class Analysis:
    def __init__(self, payload):
        self.source_count = len(payload['runs'])
        self.payload = {key: value for key, value in payload.items() if key != 'runs'}
        self.catalog = payload.get('cards', {})
        self.runs, self.item_index, self.skill_ids = [], defaultdict(set), set()
        seen = set()
        for source in payload['runs']:
            rid, wins = source.get('id'), source.get('wins')
            if not rid or rid in seen or type(wins) is not int or not 0 <= wins <= 10:
                continue
            seen.add(rid)
            # The snapshot owns these instances; queries never mutate them. Avoid
            # duplicating millions of skill dictionaries while loading a phase.
            if (source.get('_validated_run') is True and isinstance(source.get('item_ids'), frozenset)
                    and isinstance(source.get('skill_ids'), frozenset)):
                row = source  # Internal immutable rows can be shared by adjacent snapshots.
            else:
                items = [c for c in source.get('items', []) if isinstance(c, dict) and c.get('cardId')]
                skills = [c for c in source.get('skills', []) if isinstance(c, dict) and c.get('cardId')]
                if not items:
                    continue
                row = dict(source, items=items, skills=skills, _validated_run=True,
                           item_ids=frozenset(c['cardId'] for c in items),
                           skill_ids=frozenset(c['cardId'] for c in skills))
            index = len(self.runs)
            self.runs.append(row)
            self.skill_ids.update(row['skill_ids'])
            for cid in row['item_ids']:
                self.item_index[cid].add(index)
        self.cache, self.lock = {}, threading.Lock()
        self.inflight = {}
        self.live = payload.get('live', False)
        self._metadata = None

    def card(self, cid):
        info = self.catalog.get(cid) or {}
        return {'cardId': cid, 'name': info.get('name') or info.get('internalName') or cid,
                'name_en': info.get('internalName') or '', 'size': info.get('size') or 'Small',
                'img': info.get('artLarge') or info.get('art') or '', 'type': info.get('type') or 'Item'}

    def metadata(self):
        if self._metadata is not None:
            return self._metadata
        counts = Counter(r['hero'] for r in self.runs)
        popular = sorted(self.item_index, key=lambda cid: (-len(self.item_index[cid]), cid))[:16]
        self._metadata = {k: self.payload[k] for k in ('season', 'phase', 'captured_at', 'source')} | {
            'total_runs': len(self.runs), 'excluded_runs': self.source_count-len(self.runs),
            'heroes': [{'id': h, 'name': HEROES.get(h, h), 'count': n} for h,n in counts.items()],
            'popular': [dict(self.card(cid), count=len(self.item_index[cid])) for cid in popular]}
        return self._metadata

    def search(self, text):
        text = text.strip().lower().replace(' ', '')
        found = []
        for cid in self.item_index:
            card = self.card(cid)
            names = [card['name'].lower().replace(' ', ''), card['name_en'].lower().replace(' ', '')]
            if text and (text == cid.lower() or any(text in name for name in names)):
                found.append((0 if text in names else 1, -len(self.item_index[cid]), cid, card))
        found.sort(key=lambda v: v[:3])
        return [dict(card, count=len(self.item_index[cid])) for _,_,cid,card in found[:15]]

    def examples(self, runs):
        def display(c):
            return self.card(c['cardId']) | {k:v for k,v in c.items() if k != 'cardId'}
        result = []
        for row in sorted(runs, key=lambda r: (r['wins'] >= 10, timestamp(r.get('created_at')) or 0, r['id']), reverse=True)[:3]:
            items = row['items']
            if items and all(type(c.get('slotPosition')) is int for c in items):
                items = sorted(items, key=lambda c: c['slotPosition'])
            result.append({k: row.get(k) for k in ('id', 'hero', 'wins', 'created_at', 'screenshot')} | {
                'items': [display(c) for c in items], 'skills': [display(c) for c in row['skills']]})
        return result

    def companions(self, runs, anchors, kind, winning=False):
        counts, wins = Counter(), Counter()
        key = 'item_ids' if kind == 'items' else 'skill_ids'
        for row in runs:
            ids = row[key] - anchors if kind == 'items' else row[key]
            counts.update(ids)
            if row['wins'] >= 10:
                wins.update(ids)
        total_wins = sum(r['wins'] >= 10 for r in runs)
        order = wins if winning else counts
        return [self.card(cid) | {'count': counts[cid], 'frequency': counts[cid]/len(runs) if runs else 0,
                                 'ten_wins': wins[cid], 'ten_win_frequency': wins[cid]/total_wins if total_wins else None}
                for cid,n in sorted(order.items(), key=lambda x: (-x[1], x[0]))[:8]]

    def skill_pairs(self, runs):
        # Bound enumeration to the most common skills; counts always use actual co-occurrence.
        counts = Counter(cid for r in runs for cid in r['skill_ids'])
        eligible = {cid for cid,_ in sorted(counts.items(), key=lambda x: (-x[1], x[0]))[:20]}
        pairs = Counter(pair for r in runs for pair in combinations(sorted(r['skill_ids'] & eligible), 2))
        return [{'cards': [self.card(cid) for cid in pair], 'count': n, 'frequency': n/len(runs)}
                for pair,n in sorted(pairs.items(), key=lambda x: (-x[1],x[0]))[:6] if n >= 10]

    def query(self, card_ids, hero='', rank='all', days=None, sort='reliable'):
        anchors = frozenset(card_ids)
        if not anchors or len(anchors) > 5 or any(c not in self.item_index and c not in self.catalog for c in anchors):
            raise ValueError('请选择 1–5 张有效物品')
        if any(str((self.catalog.get(c) or {}).get('type', '')).lower() == 'skill' or
               (c in self.skill_ids and c not in self.item_index) for c in anchors):
            raise ValueError('阵容分析仅支持物品筛选，技能作为搭配结果展示')
        if hero and hero not in HEROES:
            raise ValueError('未知英雄')
        if rank not in ('all', 'legendary') or sort not in ('reliable', 'rate', 'appearance'):
            raise ValueError('无效筛选或排序')
        if days is not None and (type(days) is not int or not 1 <= days <= 365):
            raise ValueError('最近天数须为 1–365 的整数')
        # Live rolling windows advance even during long periods without inserts.
        reference = int(time.time() // 60) * 60 if self.live and days else None
        key = (tuple(sorted(anchors)), hero, rank, days, sort, reference)
        with self.lock:
            cached = self.cache.get(key)
            existing = self.inflight.get(key)
            if cached is None and existing is None:
                self.inflight[key] = future = Future()
        if cached is not None:
            return cached
        if existing is not None:
            return existing.result()
        try:
            result = self._compute(anchors, hero, rank, days, sort, reference)
            with self.lock:
                if len(self.cache) >= 64:
                    self.cache.pop(next(iter(self.cache)))
                self.cache[key] = result
            future.set_result(result)
            return result
        except BaseException as exc:
            future.set_exception(exc)
            raise
        finally:
            with self.lock:
                self.inflight.pop(key, None)

    def _compute(self, anchors, hero, rank, days, sort, reference):
        indices = set.intersection(*(self.item_index.get(c, set()) for c in anchors))
        captured = reference if reference is not None else timestamp(self.payload['captured_at'])
        cutoff = captured - days*86400 if days else None
        runs = [self.runs[i] for i in sorted(indices) if
                (not hero or self.runs[i]['hero'] == hero) and
                (rank != 'legendary' or self.runs[i].get('rank') == 'Legendary') and
                (cutoff is None or (timestamp(self.runs[i].get('created_at')) or 0) >= cutoff)]
        base = metric(runs, len(runs))
        by_hero = defaultdict(list)
        for row in runs:
            by_hero[row['hero']].append(row)
        groups, group_runs = [], {}
        for h, pool in by_hero.items():
            # Enumerate at most three additional items. Each run contributes once per combination.
            buckets = defaultdict(list)
            for row in pool:
                extras = sorted(row['item_ids'] - anchors)
                for length in range(1, min(3, len(extras)) + 1):
                    for extra in combinations(extras, length):
                        buckets[extra].append(row)
            candidates = [(extra, rows) for extra, rows in buckets.items() if len(rows) >= 10]
            # Remove a shorter pattern only when a superset has exactly the same matching runs.
            redundant = set()
            for extra, rows in candidates:
                if len(extra) > 1:
                    for length in range(1, len(extra)):
                        for subset in combinations(extra, length):
                            if len(buckets[subset]) == len(rows):
                                redundant.add(subset)
            for extra, rows in candidates:
                if extra in redundant:
                    continue
                gid = h + ':' + ','.join(extra)
                groups.append(metric(rows, len(runs)) | {'id': gid, 'hero': h, 'hero_name': HEROES.get(h,h),
                              'core': [self.card(cid) for cid in sorted(anchors) + list(extra)],
                              'extra_ids': list(extra)})
                group_runs[gid] = rows
        if sort == 'appearance':
            ordering = lambda g: (-g['count'], -g['lower_bound'], g['id'])
        elif sort == 'rate':
            ordering = lambda g: (-g['rate'], -g['count'], g['id'])
        else:
            ordering = lambda g: (-g['lower_bound'], -g['rate'], -g['count'], g['id'])
        selected = []
        selected_counts = {'regular': 0, 'explore': 0}
        # Diversify display only; do not merge observations or metrics.
        for group in sorted(groups, key=ordering):
            level = group['sample_level']
            if selected_counts[level] >= (8 if level == 'regular' else 4):
                continue
            ids = {r['id'] for r in group_runs[group['id']]}
            if any(group['hero'] == previous['hero'] and len(ids & prev_ids)/len(ids | prev_ids) >= .85
                   for previous,prev_ids in selected):
                continue
            selected.append((group, ids))
            selected_counts[level] += 1
        regular = [(g,ids) for g,ids in selected if g['count'] >= 30][:8]
        exploratory = [(g,ids) for g,ids in selected if g['count'] < 30][:4]
        output = []
        for group,_ in regular + exploratory:
            rows = group_runs[group['id']]
            core_ids = anchors | set(group['extra_ids'])
            output.append(group | {'winning_items': self.companions(rows, core_ids, 'items', True),
                                  'winning_skills': self.companions(rows, anchors, 'skills', True),
                                  'examples': self.examples(rows)})
        result = {'scope': self.metadata() | {'hero': hero, 'rank': rank, 'days': days},
                  'anchors': [self.card(cid) for cid in sorted(anchors)], 'baseline': base,
                  'groups': output,
                  'candidate_count': len(groups),
                  'total_runs': len(runs), 'season': self.payload['season'], 'phase': self.payload['phase'],
                  'thresholds': {'explore': 10, 'regular': 30, 'extra_items_max': 3}}
        if not output:
            result['examples'] = self.examples(runs)
        return result
