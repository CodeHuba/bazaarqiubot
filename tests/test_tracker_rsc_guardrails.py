"""Synthetic fixtures for reference resolution; not remote acceptance evidence."""
import importlib.util
import json
from pathlib import Path
import pytest

@pytest.fixture
def parser():
    p = Path(__file__).parents[1] / 'tools/tracker_rsc.py'
    spec = importlib.util.spec_from_file_location('guard_parser', p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_unrelated_missing_reference_ignored(parser):
    payload = {'snapshots': [], 'combats': []}
    assert parser._parse_tracker_payload('1:'+json.dumps({'unrelated':'$ff'})+'\n2:'+json.dumps(payload)) == payload


def test_bare_json_bad_board_rejected(parser):
    payload = {'snapshots': [{'context':'CombatStart','day':13,'hour':6,'eventId':'e','playerBoard':'$60:props'}], 'combats':[{'start_event_id':'e','is_pvp':True}]}
    with pytest.raises(parser.RSCReferenceError):
        parser.extract_zaza_combat_snapshots(json.dumps(payload, separators=(',',':')))


def test_react_props_reference(parser):
    snap = {'context':'CombatStart','day':13,'hour':6,'eventId':'e','playerBoard':'$60:props:snap:playerBoard','playerSkills':'$60:props:snap:playerSkills'}
    source = '61:'+json.dumps({'snapshots':[snap],'combats':[{'start_event_id':'e','is_pvp':True}]})+'\n60:'+json.dumps(['$','div',None,{'snap':{'playerBoard':[{'baseId':'real','tierOverride':'Gold'}],'playerSkills':[]}}])
    result = parser.extract_zaza_combat_snapshots(source)
    assert result[0]['playerBoard'] == [{'baseId':'real','tierOverride':'Gold'}]
