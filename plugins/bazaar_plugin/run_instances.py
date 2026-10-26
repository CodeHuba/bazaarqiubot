"""Recover terminal instance metadata without guessing from encyclopedia tiers."""
import json
from collections import defaultdict


def json_value(value, fallback):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return fallback
    return value if value is not None else fallback


def terminal_instances(compact, raw, kind):
    raw = json_value(raw, {})
    originals = raw.get(kind, []) if isinstance(raw, dict) else []
    originals = originals if isinstance(originals, list) else []
    cards = json_value(compact, None)
    if cards is None:
        cards = originals
    if not isinstance(cards, list):
        return []

    def normalize(card):
        if isinstance(card, str):
            if kind != 'skills':
                return None
            card = {'cardId': card}
        if not isinstance(card, dict):
            return None
        cid = card.get('cardId') or card.get('skillId')
        if not isinstance(cid, str) or not cid:
            return None
        result = dict(card, cardId=cid)
        if 'tier' not in result and 'tierOverride' in result:
            result['tier'] = result['tierOverride']
        return result

    by_id = defaultdict(list)
    for original in originals:
        candidate = normalize(original)
        if candidate:
            by_id[candidate['cardId']].append(candidate)
    result = []
    for source in cards:
        card = normalize(source)
        if card is None:
            continue
        matches = by_id[card['cardId']]
        for identity in ('slotPosition', 'enchantment'):
            if identity in card:
                matches = [c for c in matches if c.get(identity) == card[identity]]
        for field in ('tier', 'isLegendary', 'slotPosition', 'enchantment'):
            # Explicit null wins; only unanimous raw values can fill a gap.
            if field not in card and matches and all(field in c for c in matches):
                value = matches[0][field]
                if all(c[field] == value for c in matches):
                    card[field] = value
        result.append(card)
    return result
