"""Parser for BazaarDB tracker RSC payloads; no network or authentication code."""

from __future__ import annotations

import json
import re


class RSCReferenceError(ValueError):
    """A Flight chunk reference could not be resolved safely."""


_RSC_REFERENCE = re.compile(r"^\$([0-9a-fA-F]+)(?::(.*))?$")
_RSC_CHUNK = re.compile(rb"[ \t]*([0-9a-fA-F]+):")
_RSC_TEXT = re.compile(rb"T([0-9a-fA-F]+),")


def _rsc_chunks(page_source: str) -> dict[str, object]:
    """Collect JSON models and length-prefixed UTF-8 text Flight chunks."""
    chunks: dict[str, object] = {}
    candidates = [page_source]
    wrapped_parts = []
    for match in re.finditer(r'self\.__next_f\.push\(\[1,"((?:\\.|[^"\\])*)"\]\)', page_source):
        try:
            wrapped_parts.append(json.loads(f'"{match.group(1)}"'))
        except json.JSONDecodeError:
            continue
    candidates.extend(wrapped_parts)
    if wrapped_parts:
        candidates.append(''.join(wrapped_parts))
    for text in candidates:
        data = text.encode('utf-8')
        offset = 0
        while offset < len(data):
            match = _RSC_CHUNK.match(data, offset)
            if not match:
                newline = data.find(b'\n', offset)
                offset = len(data) if newline < 0 else newline + 1
                continue
            chunk_id = match.group(1).decode('ascii').lower()
            start = match.end()
            text_header = _RSC_TEXT.match(data, start)
            if text_header:
                # Flight T rows have a hexadecimal byte length and no required
                # newline after their body. Text can contain newlines or row-like
                # content; neither is a model boundary within this body.
                start = text_header.end()
                end = start + int(text_header.group(1), 16)
                if end > len(data):
                    break  # An individual HTML push may split this row.
                try:
                    chunks[chunk_id] = data[start:end].decode('utf-8')
                except UnicodeDecodeError:
                    pass
                offset = end
                continue
            newline = data.find(b'\n', start)
            end = len(data) if newline < 0 else newline
            try:
                chunks[chunk_id] = json.loads(data[start:end])
            except (json.JSONDecodeError, UnicodeDecodeError):
                # Not every RSC line is a JSON model (for example a module
                # reference); the tracker data itself must still be JSON.
                pass
            offset = end + 1
    return chunks


def _resolve_rsc(value, chunks: dict[str, object], trail: tuple[str, ...] = (), seen=()):
    """Resolve Flight ``$id:path`` values, including nested references."""
    if isinstance(value, str):
        match = _RSC_REFERENCE.fullmatch(value)
        if not match:
            return value
        chunk_id, path = match.groups()
        chunk_id = chunk_id.lower()
        reference = (chunk_id, path or '')
        if reference in seen:
            raise RSCReferenceError(f"循环 RSC 引用: {' -> '.join((*trail, chunk_id))}")
        if chunk_id not in chunks:
            raise RSCReferenceError(f"未解析 RSC chunk: ${chunk_id}")
        current = chunks[chunk_id]
        next_trail = (*trail, value)
        next_seen = (*seen, reference)
        if path:
            for part in path.split(':'):
                if isinstance(current, str) and _RSC_REFERENCE.fullmatch(current):
                    current = _resolve_rsc(current, chunks, next_trail, next_seen)
                if isinstance(current, list) and part == 'props' and len(current) == 4 and current[0] == '$':
                    current = current[3]
                    continue
                if isinstance(current, list):
                    if not part.isdigit() or int(part) >= len(current):
                        raise RSCReferenceError(f"RSC 数组路径不存在: ${chunk_id}:{path}")
                    current = current[int(part)]
                elif isinstance(current, dict):
                    if part not in current:
                        raise RSCReferenceError(f"RSC 对象路径不存在: ${chunk_id}:{path}")
                    current = current[part]
                else:
                    raise RSCReferenceError(f"RSC 路径穿过非容器: ${chunk_id}:{path}")
        if isinstance(current, str) and _RSC_REFERENCE.fullmatch(current):
            return _resolve_rsc(current, chunks, next_trail, next_seen)
        return _resolve_rsc(current, chunks, next_trail, next_seen) if isinstance(current, (dict, list)) else current
    if isinstance(value, list):
        return [_resolve_rsc(item, chunks, trail, seen) for item in value]
    if isinstance(value, dict):
        return {key: _resolve_rsc(item, chunks, trail, seen) for key, item in value.items()}
    return value


def _resolved_tracker_payload(page_source: str):
    chunks = _rsc_chunks(page_source)
    for chunk_id, value in chunks.items():
        stack = [value]
        while stack:
            item = stack.pop()
            if isinstance(item, dict) and 'snapshots' in item and 'combats' in item:
                return _resolve_rsc(item, chunks, (chunk_id,), ())
            if isinstance(item, dict):
                stack.extend(item.values())
            elif isinstance(item, list):
                stack.extend(item)
    return None


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


def _parse_tracker_payload(page_source: str) -> dict | None:
    resolved = _resolved_tracker_payload(page_source)
    if resolved is not None:
        return resolved
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
        decoder = json.JSONDecoder()
        # Decode objects without relying on key order or compact formatting.
        for index, char in enumerate(page_source):
            if char != '{':
                continue
            try:
                parsed, _ = decoder.raw_decode(page_source, index)
            except json.JSONDecodeError:
                continue
            stack = [parsed]
            while stack:
                item = stack.pop()
                if isinstance(item, dict):
                    if 'snapshots' in item and 'combats' in item:
                        payload = item
                        break
                    stack.extend(item.values())
                elif isinstance(item, list):
                    stack.extend(item)
            if payload is not None:
                break
    if not payload:
        return None
    if isinstance(payload, str):
        start = payload.find('{"snapshots":')
        if start < 0:
            return None
        payload = _find_json_object(payload, start)
    try:
        tracker_data = payload if isinstance(payload, dict) else json.loads(payload)
    except (TypeError, json.JSONDecodeError):
        return None
    return _resolve_rsc(tracker_data, _rsc_chunks(page_source))


def _require_array(value, field: str) -> list:
    if not isinstance(value, list):
        raise RSCReferenceError(f"快照字段 {field} 未解析为数组")
    return value


def _combat_start_facts(tracker_data: dict) -> list[dict]:
    combats_by_start_event = {
        combat.get('start_event_id'): combat
        for combat in tracker_data.get('combats', [])
        if combat.get('start_event_id')
    }
    combat_starts = [
        snapshot for snapshot in tracker_data.get('snapshots', [])
        if snapshot.get('context') == 'CombatStart'
    ]
    return [
        {
            'eventId': snapshot.get('eventId'),
            'ts': snapshot.get('ts'),
            'day': snapshot.get('day'),
            'hour': snapshot.get('hour'),
            'playerLevel': snapshot.get('playerLevel'),
            'playerBoard': _require_array(snapshot.get('playerBoard', []), 'playerBoard'),
            'playerSkills': _require_array(snapshot.get('playerSkills', []), 'playerSkills'),
            'opponentBoard': _require_array(snapshot.get('opponentBoard', []), 'opponentBoard'),
            'opponentSkills': _require_array(snapshot.get('opponentSkills', []), 'opponentSkills'),
            'combat': combats_by_start_event.get(snapshot.get('eventId'), {}),
        }
        for snapshot in combat_starts
    ]


def extract_combat_snapshots(page_source: str) -> list[dict]:
    """Extract CombatStart snapshots using the legacy per-day maximum hour rule."""
    tracker_data = _parse_tracker_payload(page_source)
    if tracker_data is None:
        return []
    combat_starts = _combat_start_facts(tracker_data)
    last_hour_by_day = {}
    for snapshot in combat_starts:
        day = snapshot.get('day')
        hour = snapshot.get('hour')
        if isinstance(day, int) and isinstance(hour, int):
            last_hour_by_day[day] = max(last_hour_by_day.get(day, hour), hour)
    return [
        snapshot for snapshot in combat_starts
        if snapshot.get('day') in last_hour_by_day
        and snapshot.get('hour') == last_hour_by_day[snapshot.get('day')]
    ]


def _is_pvp(value) -> bool:
    if value is True or (isinstance(value, int) and not isinstance(value, bool) and value == 1):
        return True
    return isinstance(value, str) and value.strip().lower() in {'true', '1'}


def extract_zaza_combat_snapshots(page_source: str) -> list[dict]:
    """Return hour-6 CombatStart snapshots joined to an explicitly PvP combat.

    Unlike :func:`extract_combat_snapshots`, this applies the Zaza scope directly
    to the complete parsed snapshot set, before any per-day max-hour selection.
    """
    tracker_data = _parse_tracker_payload(page_source)
    if tracker_data is None:
        return []
    return [
        snapshot for snapshot in _combat_start_facts(tracker_data)
        if snapshot.get('hour') == 6
        and _is_pvp((snapshot.get('combat') or {}).get('is_pvp'))
    ]
