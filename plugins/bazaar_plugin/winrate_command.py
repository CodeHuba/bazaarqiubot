"""Parse and resolve 1–5 distinct cards without silently dropping conditions."""
import shlex
from difflib import SequenceMatcher

HELP = ('用法: #bz winrate <物品1>+<物品2>（1–5件物品） [--hero 英雄] [--days N] [--legendary]\n'
        '示例: #bz winrate 船骸+船锚\n中文卡名也可用空格分隔；英文卡名可用 + 分隔或加双引号。')


class WinrateInputError(ValueError):
    pass


def parse_winrate_args(arg):
    try:
        lexer = shlex.shlex(arg, posix=True, punctuation_chars='+＋')
        lexer.whitespace_split = True
        lexer.commenters = ''
        tokens = list(lexer)
    except ValueError as error:
        raise WinrateInputError('卡名引号未闭合，请补全双引号后重试。') from error
    cards, hero, days, rank = [], None, None, 'all'
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token in ('--hero', '--days'):
            if i+1 >= len(tokens) or tokens[i+1].startswith('--') or tokens[i+1] in ('+', '＋'):
                raise WinrateInputError('请提供 '+token+' 的参数。')
            value = tokens[i+1]
            if token == '--hero':
                hero = value
            else:
                if not value.isascii() or not value.isdecimal() or int(value) < 1:
                    raise WinrateInputError('--days 必须为正整数。')
                days = int(value)
            i += 2
            continue
        if token == '--legendary':
            rank = 'legendary'
        elif token.startswith('--'):
            raise WinrateInputError('不支持的参数: '+token+'\n'+HELP)
        else:
            cards.append(token)
        i += 1
    explicit = any(t and all(c in '+＋' for c in t) for t in cards)
    if explicit:
        groups, group = [], []
        for token in cards:
            if token and all(c in '+＋' for c in token):
                if len(token) != 1 or not group:
                    raise WinrateInputError('请在每个 + 两侧填写卡牌名称。')
                groups.append(' '.join(group).strip())
                group = []
            else:
                group.append(token)
        if not group:
            raise WinrateInputError('请在每个 + 两侧填写卡牌名称。')
        groups.append(' '.join(group).strip())
        cards = groups
    if not cards or any(not c.strip() for c in cards):
        raise WinrateInputError(HELP)
    return dict(cards=cards, space_mode=not explicit, hero=hero, days=days, rank_filter=rank)


def resolve_cards(query, cards, *, space_mode=False):
    """Resolve item names automatically only when the item candidate is unique."""
    from . import card_image_helper
    if query.conn is None:
        query.load()
    catalog = card_image_helper._load().get('cards', {}) or {}
    normalize = lambda value: ''.join(str(value).casefold().split())
    # Image metadata can replace a DEBUG card's display name with the public
    # title while leaving its original alias in the index. Exclude its ID from
    # every alias, including the public group used by winrate's SQL filters.
    debug_ids = {cid for label, ids in query.name_to_ids.items()
                 if '[debug]' in normalize(label) for cid in ids}
    for cid, info in query.card_mapping.items():
        native = (catalog.get(cid) or {}).get('nativeInternalName') or ''
        if '[debug]' in normalize((info or {}).get('name', '')) or '[debug]' in normalize(native):
            debug_ids.add(cid)
    # RunsQuery metadata is copied per request. Keep shared/disk catalogs intact.
    query.name_to_ids = {label: [cid for cid in ids if cid not in debug_ids]
                         for label, ids in query.name_to_ids.items()
                         if '[debug]' not in normalize(label)}
    # Prefer the ordinary native item when a Community Team variant shares
    # its public name. Use source identity, never presence/absence of run data.
    public_names = set()
    for cid, image in catalog.items():
        image = image or {}
        mapped = query.card_mapping.get(cid) or {}
        public = normalize(image.get('internalName') or mapped.get('name', ''))
        native = normalize(image.get('nativeInternalName') or mapped.get('name', ''))
        kind = str(image.get('type') or mapped.get('type') or '').casefold()
        if public and native == public and cid not in debug_ids and kind in ('item', 'tcarditem'):
            public_names.add(public)
    internal_variants = set()
    for cid, image in catalog.items():
        image = image or {}
        native = normalize(image.get('nativeInternalName') or (query.card_mapping.get(cid) or {}).get('name', ''))
        public = normalize(image.get('internalName', ''))
        if native.startswith('[communityteam]') and public in public_names:
            internal_variants.add(cid)
    query.name_to_ids = {key: [cid for cid in ids if cid not in internal_variants]
                         for key, ids in query.name_to_ids.items()}
    groups, inputs, item_index = {}, {}, {}
    nonitems = set()
    for cid, mapped in query.card_mapping.items():
        image = catalog.get(cid) or {}
        kind = str(image.get('type') or (mapped or {}).get('type') or '').casefold()
        if kind and kind not in ('item', 'tcarditem'):
            english = (mapped or {}).get('name', '')
            labels = [english, image.get('internalName', ''), image.get('name', ''),
                      getattr(query, 'en_to_zh', {}).get(english, '')]
            nonitems.update(normalize(label) for label in labels if normalize(label))
    for alias, target in query.card_aliases.items():
        if normalize(target) in nonitems:
            nonitems.add(normalize(alias))
    def is_item(cid):
        image = catalog.get(cid) or {}
        mapped = query.card_mapping.get(cid) or {}
        kind = image.get('type') or mapped.get('type') or ''
        return str(kind).casefold() in ('item', 'tcarditem')
    # Index labels are aliases, not separate card identities. Merge overlapping
    # ID groups so old and public names of the same card never compete.
    parents = {}
    def root(cid):
        parents.setdefault(cid, cid)
        if parents[cid] != cid:
            parents[cid] = root(parents[cid])
        return parents[cid]
    for key, ids in query.name_to_ids.items():
        allowed = list(dict.fromkeys(cid for cid in ids if is_item(cid)))
        if not normalize(key) or not allowed:
            continue
        item_index[key] = allowed
        for cid in allowed:
            parents[root(cid)] = root(allowed[0])
    for key, allowed in item_index.items():
        canonical = root(allowed[0])
        group = groups.setdefault(canonical, dict(ids=[], name='', aliases=[]))
        group['aliases'].append(key)
        for cid in allowed:
            if cid not in group['ids']:
                group['ids'].append(cid)
    for canonical, group in groups.items():
        primary = query.card_display_info(group['ids'][0])
        group['name'] = primary['name_en']
        group['display'] = primary['name']
        labels = group['aliases'] + [primary['name'], primary['name_en']]
        for cid in group['ids']:
            image = catalog.get(cid) or {}
            labels.extend((image.get('name', ''), image.get('internalName', '')))
        for label in labels:
            if normalize(label):
                inputs.setdefault(normalize(label), set()).add(canonical)
    for alias, target in query.card_aliases.items():
        keys = inputs.get(normalize(target), set())
        if keys and normalize(alias):
            inputs.setdefault(normalize(alias), set()).update(keys)
    combined = ' '.join(cards)
    if space_mode and len(cards) > 1 and normalize(combined) in inputs:
        cards = [combined]
    resolved, seen = [], set()
    for name in cards:
        key = normalize(name)
        exact = inputs.get(key, set())
        if not exact and key in nonitems:
            raise WinrateInputError('『'+name+'』不是物品。胜率查询仅支持物品，不支持技能、事件或野怪。')
        if len(exact) != 1:
            matches = exact or set().union(*(values for label, values in inputs.items()
                if key and (key in label or label in key)))
            if not matches and key:
                scores = {}
                for label, values in inputs.items():
                    score = SequenceMatcher(None, key, label).ratio()
                    if score >= .55:
                        for value in values:
                            scores[value] = max(score, scores.get(value, 0))
                matches = sorted(scores, key=lambda value: (-scores[value], groups[value]['display']))
            if not matches:
                raise WinrateInputError('未找到物品『'+name+'』。胜率查询仅支持物品，不支持技能、事件或野怪，请检查物品完整名称。')
            if isinstance(matches, set):
                matches = sorted(matches, key=lambda value: groups[value]['display'])
            if len(matches) > 1:
                candidates = [groups[k]['display']+'（物品）' for k in list(matches)[:8]]
                raise WinrateInputError('未精确匹配物品『'+name+'』，请选择物品：\n'
                    +'\n'.join(str(i)+'. '+value for i, value in enumerate(candidates, 1))
                    +'\n请用候选的完整物品名替换后重试，保留组合中的其他物品及筛选参数。')
            exact = set(matches)
        canonical = next(iter(exact))
        if canonical not in seen:
            seen.add(canonical)
            resolved.append(groups[canonical]['name'])
        if len(resolved) > 5:
            raise WinrateInputError('一次最多查询5件不同物品，请缩减组合后重试。')
    # Request-local metadata is copied by RunsQuery. Prevent its later name
    # lookup from adding same-name skills/encounters back into the query.
    query.name_to_ids = item_index
    return resolved
