"""Independent transport/reference regressions; fixtures are synthetic."""
import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def parser():
    path = Path(__file__).parents[1] / 'tools/tracker_rsc.py'
    spec = importlib.util.spec_from_file_location('day13_review_parser', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _payload():
    return {'snapshots': [{'eventId': 'e', 'context': 'CombatStart', 'day': 13,
                          'hour': 6, 'playerBoard': [{'baseId': 'card'}],
                          'playerSkills': [{'baseId': 'skill'}]}],
            'combats': [{'start_event_id': 'e', 'is_pvp': True}]}


def _html(parts):
    return ''.join('<script>self.__next_f.push([1,' + json.dumps(part) + '])</script>'
                   for part in parts)


@pytest.mark.parametrize('split', [1, 5, 27, 90, -1])
def test_html_push_split_inside_flight_line(parser, split):
    payload = _payload()
    payload['snapshots'][0]['playerBoard'] = '$a:board'
    stream = '61:' + json.dumps(payload) + '\na:' + json.dumps({'board': [{'baseId': 'card'}]}) + '\n'
    source = _html([stream[:split], stream[split:]])
    assert parser.extract_zaza_combat_snapshots(source)[0]['playerBoard'] == [{'baseId': 'card'}]


def test_html_push_split_every_character(parser):
    stream = '61:' + json.dumps(_payload(), ensure_ascii=False) + '\n'
    assert parser._parse_tracker_payload(_html(stream)) == _payload()


@pytest.mark.parametrize('style', ['pretty', 'reordered', 'wrapped'])
def test_bare_json_fallback_not_sensitive_to_key_order_or_whitespace(parser, style):
    data = _payload()
    if style == 'reordered':
        data = {'combats': data['combats'], 'snapshots': data['snapshots']}
    elif style == 'wrapped':
        data = {'unrelated': '$ff', 'props': {'tracker': data}}
    parsed = parser._parse_tracker_payload(json.dumps(data, indent=2))
    assert parsed == _payload()


def test_same_chunk_different_paths_not_a_cycle(parser):
    data = _payload()
    data['snapshots'][0]['playerBoard'] = '$a:board'
    source = '61:' + json.dumps(data) + '\na:' + json.dumps({
        'board': ['$a:card'], 'card': {'baseId': 'card'}, 'unused': '$ff'})
    assert parser.extract_zaza_combat_snapshots(source)[0]['playerBoard'] == [{'baseId': 'card'}]


def test_shared_reference_in_siblings_not_a_cycle(parser):
    data = _payload()
    data['snapshots'][0]['playerBoard'] = ['$a:card', '$a:card']
    data['snapshots'][0]['playerSkills'] = ['$a:card']
    source = '61:' + json.dumps(data) + '\na:' + json.dumps({'card': {'baseId': 'shared'}})
    result = parser.extract_zaza_combat_snapshots(source)[0]
    assert result['playerBoard'] == [{'baseId': 'shared'}] * 2
    assert result['playerSkills'] == [{'baseId': 'shared'}]


@pytest.mark.parametrize('chunk', [
    {'board': '$a:board'},
    {'board': '$a:other', 'other': '$a:board'},
    {'board': ['$a:board']},
])
def test_real_same_chunk_cycles_rejected(parser, chunk):
    data = _payload()
    data['snapshots'][0]['playerBoard'] = '$a:board'
    with pytest.raises(parser.RSCReferenceError, match='循环'):
        parser._parse_tracker_payload('61:' + json.dumps(data) + '\na:' + json.dumps(chunk))


def test_bare_json_reference_is_not_silently_returned(parser):
    data = _payload()
    data['snapshots'][0]['playerBoard'] = '$ff:board'
    with pytest.raises(parser.RSCReferenceError, match='未解析'):
        parser._parse_tracker_payload(json.dumps(data, indent=2))
