"""Parser for BazaarDB tracker RSC payloads; no network or authentication code."""

from __future__ import annotations

import json
import re


def _find_json_object(text: str, start: int) -> str | None:
    depth = 0
    in_string = False
    escaped = False
    for index, char in enumerate(text[start:], start):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == '{':
            depth += 1
        elif char == '}':
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    return None


def extract_combat_snapshots(page_source: str) -> list[dict]:
    """Extract CombatStart snapshots and their matching combat outcomes from RSC or HTML."""
    payload = None
    for match in re.finditer(r'self\.__next_f\.push\(\[1,"((?:\\.|[^"\\])*)"\]\)', page_source):
        try:
            candidate = json.loads(f'"{match.group(1)}"')
        except json.JSONDecodeError:
            continue
        if '"snapshots":' in candidate and '"combats":' in candidate:
            payload = candidate
            break
    if payload is None:
        marker = '{"snapshots":'
        start = page_source.find(marker)
        while start >= 0:
            candidate = _find_json_object(page_source, start)
            if candidate:
                try:
                    parsed = json.loads(candidate)
                except json.JSONDecodeError:
                    parsed = None
                if isinstance(parsed, dict) and 'combats' in parsed:
                    payload = candidate
                    break
            start = page_source.find(marker, start + len(marker))
    if not payload:
        return []
    if isinstance(payload, str):
        start = payload.find('{"snapshots":')
        if start < 0:
            return []
        payload = _find_json_object(payload, start)
    try:
        tracker_data = payload if isinstance(payload, dict) else json.loads(payload)
    except (TypeError, json.JSONDecodeError):
        return []
    combats_by_start_event = {
        combat.get('start_event_id'): combat
        for combat in tracker_data.get('combats', [])
        if combat.get('start_event_id')
    }
    return [
        {
            'eventId': snapshot.get('eventId'),
            'ts': snapshot.get('ts'),
            'day': snapshot.get('day'),
            'hour': snapshot.get('hour'),
            'playerLevel': snapshot.get('playerLevel'),
            'playerBoard': snapshot.get('playerBoard', []),
            'playerSkills': snapshot.get('playerSkills', []),
            'opponentBoard': snapshot.get('opponentBoard', []),
            'opponentSkills': snapshot.get('opponentSkills', []),
            'combat': combats_by_start_event.get(snapshot.get('eventId'), {}),
        }
        for snapshot in tracker_data.get('snapshots', [])
        if snapshot.get('context') == 'CombatStart'
    ]
