"""Select frequent, distinct, real ten-win compositions without chaining variants."""
from collections import defaultdict
from datetime import datetime
from fractions import Fraction


def _created(row):
    try:
        value = datetime.fromisoformat(str(row.get('created_at')).replace('Z', '+00:00'))
        return value.timestamp() if value.tzinfo else 0
    except (ValueError, TypeError, OverflowError):
        return 0


def one_item_apart(left, right):
    """Multiset edit distance: one insertion, deletion, or replacement is allowed."""
    i = j = removed = added = 0
    while i < len(left) and j < len(right):
        if left[i] == right[j]:
            i += 1; j += 1
        elif left[i] < right[j]:
            removed += 1; i += 1
        else:
            added += 1; j += 1
        if max(removed, added) > 1:
            return False
    return max(removed + len(left)-i, added + len(right)-j) <= 1


def _variants(signature):
    return {signature, *(signature[:i]+signature[i+1:] for i in range(len(signature)))}


def select_examples(runs, catalog, limit=3):
    exact = defaultdict(list)
    for row in runs:
        if row['wins'] == 10:
            exact[(row['hero'], tuple(sorted(c['cardId'] for c in row['items'])))].append(row)
    if not exact:
        return []
    candidates = []
    for (hero, signature), rows in exact.items():
        native = 'Hero8' if hero == 'The Dragons' else hero
        supported = known = 0
        for cid in signature:
            owners = (catalog.get(cid) or {}).get('heroes')
            if isinstance(owners, list) and owners:
                known += 1
                supported += native in owners or hero in owners or 'Common' in owners
        latest = max(rows, key=lambda row: (_created(row), str(row['id'])))
        candidates.append(dict(hero=hero, signature=signature, count=len(rows), row=latest,
                               supported=Fraction(supported, len(signature)),
                               known=Fraction(known, len(signature)), created=_created(latest)))
    candidates.sort(key=lambda c: (-c['count'], -c['supported'], -c['known'], -c['created'], c['hero'], c['signature']))
    # Shared one-deletion signatures find possible neighbors without comparing
    # every pair of full compositions. Keep duplicates in the physical board.
    postings = defaultdict(set)
    for index, candidate in enumerate(candidates):
        for variant in _variants(candidate['signature']):
            postings[(candidate['hero'], variant)].add(index)
    assigned, families = set(), []
    for index, seed in enumerate(candidates):
        if index in assigned:
            continue
        members = [index]; assigned.add(index)
        neighbors = set()
        for variant in _variants(seed['signature']):
            neighbors.update(postings[(seed['hero'], variant)])
        for other in sorted(neighbors-assigned):
            if all(one_item_apart(candidates[other]['signature'], candidates[member]['signature']) for member in members):
                members.append(other); assigned.add(other)
        families.append((seed, sum(candidates[member]['count'] for member in members)))
    families.sort(key=lambda pair: (-pair[1], -pair[0]['supported'], -pair[0]['known'],
                                    -pair[0]['created'], pair[0]['hero'], pair[0]['signature']))
    selected = []
    for representative, count in families:
        # Complete-link groups need not have distinct representatives; do not
        # spend another display slot on a one-item variant of an earlier case.
        if any(representative['hero'] == old['hero'] and one_item_apart(representative['signature'], old['signature'])
               for old, _ in selected):
            continue
        selected.append((representative, count))
        if len(selected) == limit:
            break
    return [(case['row'], count, case['count']) for case, count in selected]
