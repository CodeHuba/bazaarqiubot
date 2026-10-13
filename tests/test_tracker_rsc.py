import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def tracker_rsc():
    path = Path(__file__).parents[1] / "tools" / "tracker_rsc.py"
    spec = importlib.util.spec_from_file_location("tracker_rsc_reference_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _flight(*chunks):
    return "\n".join(f"{chunk_id:x}:{json.dumps(value, separators=(',', ':'))}" for chunk_id, value in chunks)


def test_resolves_hex_chunks_colon_paths_arrays_and_objects(tracker_rsc):
    tracker_data = {
        "snapshots": [{
            "eventId": "combat-start", "context": "CombatStart", "day": 2, "hour": 6,
            "playerBoard": [{"baseId": "from-board"}], "playerSkills": ["skill"],
        }],
        "combats": [{"start_event_id": "combat-start", "is_pvp": True}],
    }
    source = _flight(
        (0x60, {"props": {"children": ["ignored", {"props": {"children": {"props": {"snap": {
            "playerBoard": "$61:props:children:0:props:board",
            "playerSkills": "$61:props:children:0:props:skills",
        }}}}}]}}),
        (0x61, {"props": {"children": [{"props": {"board": tracker_data["snapshots"][0]["playerBoard"], "skills": tracker_data["snapshots"][0]["playerSkills"]}}]}}),
        (0x62, tracker_data),
    )
    # The tracker payload is reachable through a nested props path, while the
    # rest of its fields are supplied by another chunk.
    source = _flight(
        (0x60, {"props": {"children": ["ignored", {"props": {"children": {"props": {"snap": {
            "snapshots": [{
                "eventId": "combat-start", "context": "CombatStart", "day": 2, "hour": 6,
                "playerBoard": "$61:props:children:0:props:board",
                "playerSkills": "$61:props:children:0:props:skills",
            }],
            "combats": "$62:combats",
        }}}}}]}}),
        (0x61, {"props": {"children": [{"props": {"board": tracker_data["snapshots"][0]["playerBoard"], "skills": tracker_data["snapshots"][0]["playerSkills"]}}]}}),
        (0x62, tracker_data),
    )

    parsed = tracker_rsc._parse_tracker_payload(source)
    assert parsed["snapshots"][0]["playerBoard"] == [{"baseId": "from-board"}]
    assert parsed["snapshots"][0]["playerSkills"] == ["skill"]
    assert tracker_rsc.extract_zaza_combat_snapshots(source)[0]["eventId"] == "combat-start"


def test_unresolved_and_cyclic_references_are_explicit(tracker_rsc):
    with pytest.raises(tracker_rsc.RSCReferenceError, match="未解析"):
        tracker_rsc._parse_tracker_payload('60:' + json.dumps({"snapshots": "$ff:x", "combats": []}))

    with pytest.raises(tracker_rsc.RSCReferenceError, match="循环"):
        tracker_rsc._parse_tracker_payload(_flight(
            (0x60, {"snapshots": "$61:value", "combats": []}),
            (0x61, {"value": "$60:snapshots"}),
        ))


def test_legacy_inline_payload_remains_compatible(tracker_rsc):
    data = {"snapshots": [], "combats": []}
    assert tracker_rsc._parse_tracker_payload("1:" + json.dumps(data)) == data


@pytest.mark.parametrize('wrapped', [False, True])
def test_length_prefixed_text_handles_utf8_newlines_and_adjacent_rows(tracker_rsc, wrapped):
    text = '卡牌描述\n66:{"not_a_chunk":true}'
    data = {'snapshots': [{'context': 'CombatStart', 'eventId': 'e', 'day': 1, 'hour': 6,
                          'playerBoard': [], 'playerSkills': []}],
            'combats': [{'start_event_id': 'e', 'is_pvp': True}], 'boardKey': '$64'}
    source = f'64:T{len(text.encode("utf-8")):x},' + text + '60:' + json.dumps(data)
    if wrapped:
        cut = source.index('卡') + 2
        source = ''.join('self.__next_f.push([1,' + json.dumps(part) + '])'
                         for part in (source[:cut], source[cut:]))
    parsed = tracker_rsc._parse_tracker_payload(source)
    assert parsed['boardKey'] == text
    assert tracker_rsc.extract_zaza_combat_snapshots(source)[0]['eventId'] == 'e'


def test_truncated_text_reference_still_fails_closed(tracker_rsc):
    source = '60:' + json.dumps({'snapshots': [], 'combats': [], 'key': '$64'}) + '\n64:Tff,short'
    with pytest.raises(tracker_rsc.RSCReferenceError, match='未解析'):
        tracker_rsc._parse_tracker_payload(source)
