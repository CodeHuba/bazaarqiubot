"""Versioned, user-initiated search analytics, independent of legacy request logs."""
import base64
import hashlib
import hmac
import json
import re
import sqlite3
import zlib
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

FEATURES = {'runs': '阵容列表', 'comp_card': '阵容分析', 'winrate': '胜率查询', 'partner': '搭配查询'}
PATHS = {'/api/runs': 'runs', '/api/comp/card': 'comp_card', '/api/winrate': 'winrate', '/api/partner': 'partner'}
OUTCOMES = {'success', 'partial', 'empty', 'insufficient', 'unrecognized', 'error', 'unverified'}
COMPLETED = {'success', 'partial', 'empty', 'insufficient'}
HEROES = {'Vanessa': '瓦内莎', 'Dooley': '杜利', 'Mak': '马克', 'Pygmalien': '皮格马利翁',
          'Stelle': '斯黛尔', 'Jules': '朱尔斯', 'Karnok': '卡诺克', 'The Dragons': '双龙'}
BJ = timezone(timedelta(hours=8))
ENCHANTMENTS = {'Deadly':'致命', 'Fiery':'炽焰', 'Golden':'黄金', 'Heavy':'沉重', 'Icy':'寒冰',
                'Obsidian':'黑曜石', 'Radiant':'辉耀', 'Restorative':'回复', 'Shielded':'护盾',
                'Shiny':'闪亮', 'Toxic':'毒素', 'Turbo':'疾速', 'Mossy':'长青'}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _hash(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@contextmanager
def _connect(path):
    conn = sqlite3.connect(str(path), timeout=.2)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_search_schema(path):
    with _connect(path) as conn:
        conn.execute('PRAGMA journal_mode=WAL')
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS search_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS search_event (
                id TEXT PRIMARY KEY, visitor TEXT NOT NULL, feature TEXT NOT NULL,
                condition_key TEXT NOT NULL, params_json TEXT NOT NULL,
                units_json TEXT NOT NULL, outcome TEXT NOT NULL, source TEXT NOT NULL,
                verified INTEGER NOT NULL, created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_search_time ON search_event(created_at);
            CREATE INDEX IF NOT EXISTS idx_search_dedupe ON search_event(visitor,feature,condition_key,created_at);
        ''')
        conn.execute('INSERT OR IGNORE INTO search_meta VALUES (?,?)', ('tracking_since', _now().isoformat()))


def _text(value, limit=80):
    if not isinstance(value, str) or len(value) > limit or any(ord(ch) < 32 for ch in value):
        raise ValueError('无效的搜索条件')
    return value.strip()


def _integer(value, default=None, maximum=365000, minimum=1):
    if value in ('', None):
        return default
    if isinstance(value, bool):
        raise ValueError('无效数字条件')
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise ValueError('无效数字条件') from None
    if str(result) != str(value) or not minimum <= result <= maximum:
        raise ValueError('无效数字条件')
    return result


def normalize_query(feature, args, resolver=None, season=None, phase=None):
    if feature not in FEATURES or not isinstance(args, dict):
        raise ValueError('无效的搜索功能')
    def card(value, enchantment=None):
        name = _text(value)
        if not name:
            raise ValueError('卡牌名称不能为空')
        resolved = resolver(name) if resolver else None
        key, label = resolved if resolved else ('raw:' + name.casefold(), name)
        enchantment = _text(enchantment, 40) if enchantment else None
        return {'key': key, 'name': label, 'enchantment': enchantment}
    groups = []
    if feature == 'runs':
        filters = args.get('card_filters') or []
        if isinstance(filters, str):
            try:
                filters = json.loads(filters)
            except (TypeError, ValueError):
                raise ValueError('无效卡牌筛选') from None
        if not isinstance(filters, list) or len(filters) > 20 or any(not isinstance(row, dict) for row in filters):
            raise ValueError('无效卡牌筛选')
        cards = [card(row.get('name'), row.get('enchantment')) for row in filters]
        raw = args.get('cards') or ''
        if isinstance(raw, str):
            cards.extend(card(name) for name in raw.split('+') if name.strip())
        else:
            raise ValueError('无效卡牌条件')
        groups = [cards]
    elif feature == 'winrate':
        raw = _text(args.get('cards', ''), 3000)
        if not raw:
            raise ValueError('请指定卡牌')
        parts = raw.split(',') if str(args.get('multi')) == '1' else [raw]
        groups = [[card(name) for name in part.split('+') if name.strip()] for part in parts]
        if any(not group for group in groups):
            raise ValueError('无效比较条件')
    elif feature == 'comp_card':
        raw = args.get('cards') or args.get('card') or ''
        if isinstance(raw, str) and raw.startswith('['):
            try:
                names = json.loads(raw)
            except (TypeError, ValueError):
                raise ValueError('无效物品条件') from None
        else:
            names = raw.split('+') if isinstance(raw, str) else []
        if not isinstance(names, list) or not 1 <= len(names) <= 5:
            raise ValueError('请选择 1–5 张物品')
        groups = [[card(name) for name in names]]
    else:
        groups = [[card(args.get('card', ''))]]
    if sum(map(len, groups)) > 20:
        raise ValueError('最多记录 20 个卡牌条件')
    normalized = []
    for group in groups:
        normalized.append(sorted({_json(c): c for c in group}.values(), key=lambda c: (c['key'], c['enchantment'] or '')))
    # Preserve group order for alignment with batch results; identity sorts groups separately.
    hero = _text(args.get('hero') or '')
    hero = next((key for key, label in HEROES.items() if hero.casefold() == key.casefold() or hero == label), hero)
    rank = args.get('rank') or 'all'
    if rank not in {'all', 'legendary'}:
        raise ValueError('无效段位')
    result = {'groups': normalized, 'hero': hero, 'rank': rank, 'season': season, 'phase': phase,
              'days': _integer(args.get('days'))}
    if feature == 'runs':
        result.update(day=_integer(args.get('day')), min_wins=_integer(args.get('min_wins'), 10, 20, 0))
    if feature == 'comp_card':
        result['all_phases'] = str(args.get('all_phases', '')).lower() == 'true'
    return result


def condition_key(feature, params):
    params = dict(params)
    params['groups'] = sorted(params['groups'], key=_json)
    return _hash([feature, params])


def classify(feature, data, status, count=1):
    """Per comparison-group outcomes; HTTP 200 is not synonymous with a usable result."""
    if status == 429:
        return [['throttled', 0]] * count
    if status == 400:
        return [['invalid', 0]] * count
    if status == 404:
        return [['unrecognized', 0]] * count
    if status >= 400 or not isinstance(data, dict) or data.get('error'):
        return [['error', 0]] * count
    if feature == 'winrate':
        rows = data.get('results', [data])
        if not isinstance(rows, list) or len(rows) != count:
            return [['error', 0]] * count
        return [['unrecognized' if row.get('not_found') else 'success' if row.get('total', 0) > 0 else 'empty',
                 max(0, int(row.get('total', 0)))] if isinstance(row, dict) else ['error', 0] for row in rows]
    if feature == 'runs':
        total = max(0, int(data.get('total', 0)))
        return [['unrecognized' if data.get('not_found') else 'success' if total else 'empty', total]]
    if feature == 'partner':
        if data.get('not_found'):
            return [['unrecognized', 0]]
        total = int(data.get('target_total', 0))
        return [['success' if data.get('by_winrate') or data.get('by_appear') else 'insufficient' if total else 'empty', total]]
    useful = data.get('recommendations') or data.get('layers') or data.get('groups')
    total = int(data['baseline'].get('count', 0) if isinstance(data.get('baseline'), dict)
                else data.get('total_runs', data.get('total', 0)))
    return [['success' if useful else 'insufficient' if total else 'empty', total]]


def _merge(values):
    values = set(values)
    if values == {'success'}:
        return 'success'
    if values & {'success', 'partial'}:
        return 'partial'
    for outcome in ('error', 'unverified', 'unrecognized', 'insufficient', 'empty'):
        if outcome in values:
            return outcome
    return 'error'


def issue_receipt(feature, args, data, status, secret, resolver=None, season=None, phase=None, resolved_cards=None):
    # Only use names actually resolved by the business query; never guess fuzzy aliases.
    aliases = dict(resolved_cards or {})
    if feature == 'comp_card' and status == 200 and isinstance(data, dict) and isinstance(data.get('anchors'), list):
        # The new endpoint accepts card IDs and resolves/reorders/deduplicates items.
        # Bind the receipt to its actual output rather than the raw input aliases.
        args = dict(args, cards=json.dumps([c['name'] for c in data['anchors']], ensure_ascii=False), all_phases='false')
        scope = data.get('scope') or {}
        for key in ('hero', 'rank', 'days'):
            if key in scope:
                args[key] = scope[key]
        season, phase = scope.get('season', season), scope.get('phase', phase)
    if status == 200 and isinstance(data, dict):
        if feature in {'comp_card', 'partner'} and not data.get('not_found'):
            name = data.get('card' if feature == 'comp_card' else 'card_name')
            if isinstance(name, str) and name:
                aliases[args.get('card', '').strip().casefold()] = name
        elif feature == 'winrate':
            raw_groups = (args.get('cards') or '').split(',') if str(args.get('multi')) == '1' else [args.get('cards') or '']
            rows = data.get('results', [data])
            if isinstance(rows, list) and len(rows) == len(raw_groups):
                for raw, row in zip(raw_groups, rows):
                    names = [name.strip() for name in raw.split('+') if name.strip()]
                    resolved = row.get('card_names', []) if isinstance(row, dict) and not row.get('not_found') else []
                    if len(names) == len(resolved):
                        aliases.update((name.casefold(), label) for name, label in zip(names, resolved) if isinstance(label, str))
    original_resolver = resolver
    def resolved_name(name):
        label = aliases.get(name.casefold(), name)
        found = original_resolver(label) if original_resolver else None
        return found or (('raw:' + label.casefold(), label) if label != name else None)
    resolver = resolved_name
    params = normalize_query(feature, args, resolver, season, phase)
    units = classify(feature, data, status, len(params['groups']))
    body = {'feature': feature, 'params': params, 'units': units, 'time': int(_now().replace(tzinfo=timezone.utc).timestamp())}
    encoded = base64.urlsafe_b64encode(zlib.compress(_json(body).encode())).decode().rstrip('=')
    if len(encoded) > 2400:
        raise ValueError('查询凭据超出响应头大小限制')
    signature = hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
    return encoded + '.' + signature


def read_receipt(token, secret):
    if not isinstance(token, str) or len(token) > 24000:
        raise ValueError('无效的查询凭据')
    try:
        encoded, signature = token.rsplit('.', 1)
        expected = hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError()
        data = json.loads(zlib.decompress(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4))))
        age = int(_now().replace(tzinfo=timezone.utc).timestamp()) - data['time']
        if not -60 <= age <= 86400 or data['feature'] not in FEATURES:
            raise ValueError()
        return data
    except (ValueError, TypeError, KeyError, zlib.error):
        raise ValueError('查询凭据无效或已过期') from None


def record_search(path, payload, visitor, secret, resolver=None, season=None, phase=None, now=None):
    if not isinstance(payload, dict) or not isinstance(visitor, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,128}', visitor):
        raise ValueError('缺少匿名访客标识')
    action = payload.get('id')
    if not isinstance(action, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,100}', action):
        raise ValueError('无效的搜索编号')
    feature = payload.get('feature')
    if payload.get('receipt'):
        receipt = read_receipt(payload['receipt'], secret)
        if receipt['feature'] != feature:
            raise ValueError('查询功能不匹配')
        params, statuses, verified = receipt['params'], receipt['units'], True
    else:
        params = normalize_query(feature, payload.get('params'), resolver, season, phase)
        outcome = payload.get('fallback')
        if outcome not in {'error', 'cancelled', 'unverified'}:
            raise ValueError('查询结果需要服务端凭据')
        statuses, verified = [[outcome, 0] for _ in params['groups']], False
    if any(row[0] in {'invalid', 'throttled', 'cancelled'} for row in statuses):
        return {'ok': True, 'ignored': True}
    outcome = _merge(row[0] for row in statuses)
    units = [{'cards': cards, 'outcome': row[0], 'count': row[1]} for cards, row in zip(params['groups'], statuses)]
    key = condition_key(feature, params)
    visitor = hashlib.sha256(visitor.encode()).hexdigest()
    now = now or _now()
    if 'elapsed_ms' in payload:
        elapsed = payload['elapsed_ms']
        if isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)) or not 0 <= elapsed <= 86400000:
            raise ValueError('无效查询耗时')
        # Use elapsed monotonic time instead of trusting the visitor's wall clock.
        now -= timedelta(milliseconds=elapsed)
    source = payload.get('source', 'unknown')
    if source not in {'network', 'browser_cache', 'unknown'}:
        source = 'unknown'
    with _connect(path) as conn:
        conn.execute('BEGIN IMMEDIATE')
        duplicate = conn.execute('SELECT 1 FROM search_event WHERE id=? OR (visitor=? AND feature=? AND condition_key=? AND created_at>? AND created_at<?) LIMIT 1',
                                 (action, visitor, feature, key, (now-timedelta(seconds=10)).isoformat(), (now+timedelta(seconds=10)).isoformat())).fetchone()
        if duplicate:
            return {'ok': True, 'deduplicated': True}
        conn.execute('INSERT INTO search_event VALUES (?,?,?,?,?,?,?,?,?,?)',
                     (action, visitor, feature, key, _json(params), _json(units), outcome, source, int(verified), now.isoformat()))
        conn.execute('DELETE FROM search_event WHERE created_at<?', ((now-timedelta(days=90)).isoformat(),))
    return {'ok': True, 'recorded': True}


def condition_label(feature, params):
    groups = []
    for cards in params['groups']:
        groups.append(' + '.join(c['name'] + (f"（{ENCHANTMENTS.get(c['enchantment'], c['enchantment'])}）" if c['enchantment'] else '') for c in cards) or '不限卡牌')
    labels = [FEATURES[feature], ' / '.join(groups)]
    if params.get('hero'):
        labels.append(HEROES.get(params['hero'], params['hero']))
    if params.get('day'):
        labels.append('Day ' + str(params['day']))
    labels.append('传奇' if params['rank'] == 'legendary' else '全部段位')
    if params.get('days'):
        labels.append('查询最近 ' + str(params['days']) + ' 天')
    if params.get('min_wins') is not None:
        labels.append('至少 ' + str(params['min_wins']) + ' 胜')
    if params.get('all_phases'):
        labels.append('所有赛段')
    elif params.get('phase'):
        labels.append('赛段 ' + str(params['phase']))
    if params.get('season'):
        labels.append('赛季 ' + str(params['season']))
    return ' · '.join(labels)


def search_rankings(path, days=7, board='cards', feature='all', offset=0, limit=20, hero='', rank='', day=None, enchantment='', combos=False, item=None, now=None):
    if board not in {'cards', 'conditions', 'empty'} or feature not in {*FEATURES, 'all'}:
        raise ValueError('无效榜单或功能')
    days, offset, limit = int(days), int(offset), int(limit)
    if not 1 <= days <= 90 or not 0 <= offset <= 100000 or not 1 <= limit <= 100:
        raise ValueError('无效的统计周期或页码')
    if rank not in {'', 'all', 'legendary'} or hero not in {'', *HEROES}:
        raise ValueError('无效筛选条件')
    day = _integer(day)
    enchantment = _text(enchantment, 40)
    now = now or _now()
    today = now.replace(tzinfo=timezone.utc).astimezone(BJ).date()
    cutoff = datetime.combine(today-timedelta(days=days-1), datetime.min.time(), BJ).astimezone(timezone.utc).replace(tzinfo=None)
    with _connect(path) as conn:
        rows = conn.execute('SELECT * FROM search_event WHERE created_at>=? AND created_at<=? ORDER BY created_at,id', (cutoff.isoformat(), now.isoformat())).fetchall()
        since = conn.execute("SELECT value FROM search_meta WHERE key='tracking_since'").fetchone()[0]
    buckets = {}
    matched_visitors, matched_outcomes = set(), Counter()
    for row in rows:
        if feature != 'all' and row['feature'] != feature:
            continue
        params, units = json.loads(row['params_json']), json.loads(row['units_json'])
        if hero and params['hero'] != hero or rank and params['rank'] != rank or day and params.get('day') != day:
            continue
        cards = [c for group in params['groups'] for c in group]
        if enchantment and not any((c['enchantment'] or 'none') == enchantment for c in cards):
            continue
        if combos and not any(len(group) > 1 for group in params['groups']):
            continue
        matched_visitors.add(row['visitor'])
        matched_outcomes[row['outcome']] += 1
        if board == 'cards':
            subjects = {}
            for unit in units:
                for card in unit['cards']:
                    entry = subjects.setdefault(card['key'], {'label':card['name'], 'outcomes':[], 'counts':[]})
                    entry['outcomes'].append(unit['outcome'])
                    entry['counts'].append(unit['count'])
            entries = [(_hash(['card', key]), value['label'], _merge(value['outcomes']), max(value['counts'])) for key, value in subjects.items()]
        else:
            entries = [(row['condition_key'], condition_label(row['feature'],params), row['outcome'], sum(unit['count'] for unit in units))]
        for key, label, outcome, result_count in entries:
            bucket = buckets.setdefault(key, {'key':key, 'label':label, 'searches':0, 'visitors':set(), 'outcomes':Counter(), 'sources':Counter(), 'features':Counter(), 'daily':Counter(), 'conditions':Counter(), 'last_seen':row['created_at'], 'result_counts':[]})
            bucket['searches'] += 1
            bucket['visitors'].add(row['visitor'])
            bucket['outcomes'][outcome] += 1
            bucket['sources'][row['source']] += 1
            bucket['features'][row['feature']] += 1
            if row['verified'] and outcome in COMPLETED:
                bucket['result_counts'].append(result_count)
            date = datetime.fromisoformat(row['created_at']).replace(tzinfo=timezone.utc).astimezone(BJ).date().isoformat()
            bucket['daily'][date] += 1
            bucket['conditions'][condition_label(row['feature'],params)] += 1
            bucket['last_seen'] = max(bucket['last_seen'],row['created_at'])
    def serialize(bucket, detail=False):
        counts = bucket['outcomes']
        completed = sum(counts[key] for key in COMPLETED)
        empty = counts['empty'] + counts['insufficient']
        result = {key:bucket[key] for key in ['key','label','searches','last_seen']}
        result.update(visitors=len(bucket['visitors']),outcomes=dict(counts),completed=completed,
                      empty_rate=round(empty/completed*100,1) if completed else None,
                      features=dict(bucket['features']))
        if detail:
            result_counts = bucket['result_counts']
            result.update(sources=dict(bucket['sources']),daily=[{'date':date,'count':count} for date,count in sorted(bucket['daily'].items())],
                          conditions=[{'label':label,'count':count} for label,count in bucket['conditions'].most_common(10)],
                          result_counts={'min':min(result_counts),'max':max(result_counts)} if result_counts else None)
        return result
    selected = [b for b in buckets.values() if board != 'empty' or sum(b['outcomes'][o] for o in COMPLETED) >= 5 and b['outcomes']['empty']+b['outcomes']['insufficient'] > 0]
    selected.sort(key=lambda b: (-(b['outcomes']['empty']+b['outcomes']['insufficient']) if board=='empty' else -b['searches'], -len(b['visitors']), b['label']))
    details = serialize(buckets[item],True) if item in buckets else None
    return {'version':2,'period_days':days,'board':board,'feature':feature,'tracking_since':since+'Z',
            'summary':{'searches':sum(matched_outcomes.values()),'visitors':len(matched_visitors),'outcomes':dict(matched_outcomes)},
            'total':len(selected),'offset':offset,'limit':limit,'rows':[serialize(b) for b in selected[offset:offset+limit]],
            'detail':details,'comparison_available':False,'min_completed':5}
