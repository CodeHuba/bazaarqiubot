import importlib.util
import json
from pathlib import Path


def _extract_zaza_combat_snapshots(source):
    path = Path(__file__).parents[1] / 'tools' / 'tracker_rsc.py'
    spec = importlib.util.spec_from_file_location('tracker_rsc_under_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.extract_zaza_combat_snapshots(source)


def _snapshot(event_id, day, hour):
    return {
        'eventId': event_id,
        'context': 'CombatStart',
        'day': day,
        'hour': hour,
        'playerBoard': [{'baseId': event_id}],
    }


def _wrapped_html(tracker_data):
    payload = '61:["$","$L63",null,' + json.dumps(tracker_data, separators=(',', ':')) + ']'
    return '<script>self.__next_f.push([1,' + json.dumps(payload) + '])</script>'


def test_extract_zaza_combat_snapshots_keeps_hour_six_pvp_before_max_hour_filter():
    source = '1:' + json.dumps({
        'snapshots': [
            _snapshot('pvp-six', 2, 6),
            _snapshot('pve-six', 2, 6),
            _snapshot('later-pvp', 2, 8),
            _snapshot('other-hour', 3, 5),
            _snapshot('missing-join', 4, 6),
        ],
        'combats': [
            {'start_event_id': 'pvp-six', 'is_pvp': 'true', 'outcome': 'Win'},
            {'start_event_id': 'pve-six', 'is_pvp': 'false', 'outcome': 'Win'},
            {'start_event_id': 'later-pvp', 'is_pvp': 1, 'outcome': 'Win'},
            {'start_event_id': 'other-hour', 'is_pvp': True, 'outcome': 'Win'},
        ],
    }, separators=(',', ':'))

    snapshots = _extract_zaza_combat_snapshots(source)

    assert [snapshot['eventId'] for snapshot in snapshots] == ['pvp-six']
    assert snapshots[0]['combat']['outcome'] == 'Win'


def test_extract_combat_snapshots_retains_legacy_max_hour_default():
    source = '1:' + json.dumps({
        'snapshots': [_snapshot('hour-six', 2, 6), _snapshot('hour-eight', 2, 8)],
        'combats': [
            {'start_event_id': 'hour-six', 'is_pvp': True},
            {'start_event_id': 'hour-eight', 'is_pvp': True},
        ],
    }, separators=(',', ':'))
    path = Path(__file__).parents[1] / 'tools' / 'tracker_rsc.py'
    spec = importlib.util.spec_from_file_location('tracker_rsc_legacy_under_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    snapshots = module.extract_combat_snapshots(source)

    assert [snapshot['eventId'] for snapshot in snapshots] == ['hour-eight']


def test_extract_zaza_combat_snapshots_accepts_wrapped_html_and_strict_true_values():
    source = _wrapped_html({
        'snapshots': [
            _snapshot('bool-true', 1, 6),
            _snapshot('number-true', 2, 6),
            _snapshot('zero-false', 3, 6),
            _snapshot('no-false', 4, 6),
            _snapshot('other-hour', 5, 7),
        ],
        'combats': [
            {'start_event_id': 'bool-true', 'is_pvp': True},
            {'start_event_id': 'number-true', 'is_pvp': 1},
            {'start_event_id': 'zero-false', 'is_pvp': 0},
            {'start_event_id': 'no-false', 'is_pvp': 'no'},
            {'start_event_id': 'other-hour', 'is_pvp': True},
        ],
    })

    snapshots = _extract_zaza_combat_snapshots(source)

    assert [snapshot['eventId'] for snapshot in snapshots] == ['bool-true', 'number-true']
