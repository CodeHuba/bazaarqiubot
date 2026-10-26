import importlib.util
import json
import sqlite3
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "plugins" / "bazaar_plugin" / "gamedata_client.py"


def load_gamedata_module():
    spec = importlib.util.spec_from_file_location("gamedata_client_under_test", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_cards_db(tmp_path, cards):
    path = tmp_path / "GameData.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE cards (Data TEXT)")
    for card in cards:
        conn.execute("INSERT INTO cards VALUES (?)", (json.dumps(card),))
    conn.commit()
    conn.close()
    return path


def test_query_raw_by_name_ignores_spacing_and_case(tmp_path):
    gdc = load_gamedata_module()
    path = make_cards_db(tmp_path, [{
        "Id": "pipe-organ",
        "Type": "Item",
        "InternalName": "Pipe Organ",
        "Localization": {"Title": {"Text": "Pipe Organ"}},
    }])

    result = gdc.query_raw_by_name("  pipe   organ ", path)

    assert result is not None
    assert result["Id"] == "pipe-organ"


def test_query_raw_by_name_prefers_item_or_skill_over_other_matching_card(tmp_path):
    gdc = load_gamedata_module()
    path = make_cards_db(tmp_path, [
        {"Type": "EventEncounter", "InternalName": "Pipe Organ"},
        {"Type": "Item", "InternalName": "Pipe Organ", "Id": "item-1"},
    ])

    result = gdc.query_raw_by_name("Pipe Organ", path)

    assert result is not None
    assert result["Type"] == "Item"


def test_card_name_index_contains_normalized_aliases(tmp_path):
    gdc = load_gamedata_module()
    path = make_cards_db(tmp_path, [{
        "Id": "pipe-organ",
        "Type": "Item",
        "InternalName": "Pipe Organ",
        "Localization": {"Title": {"Text": "Pipe Organ"}},
    }])

    index = gdc.build_card_name_index(path)

    assert index[gdc.normalize_card_name("PIPE   ORGAN")][0]["Id"] == "pipe-organ"


def test_card_name_index_is_reused_for_unchanged_database(tmp_path):
    gdc = load_gamedata_module()
    path = make_cards_db(tmp_path, [{"Id": "one", "Type": "Item", "InternalName": "One"}])

    first = gdc.build_card_name_index(path)
    second = gdc.build_card_name_index(path)

    assert first is second


def test_suggest_cards_returns_close_normalized_name(tmp_path):
    gdc = load_gamedata_module()
    path = make_cards_db(tmp_path, [
        {"Id": "pipe-organ", "Type": "Item", "InternalName": "Pipe Organ"},
        {"Id": "pipe-wrench", "Type": "Item", "InternalName": "Pipe Wrench"},
        {"Id": "other", "Type": "Item", "InternalName": "Other"},
    ])

    suggestions = gdc.suggest_cards("pipe orgn", path, limit=3)

    assert [card["Id"] for card in suggestions] == ["pipe-organ"]


def test_name_lookup_and_suggestions_filter_kind_before_limit(tmp_path):
    gdc = load_gamedata_module()
    cards = [{'Id': f'item-{i}', 'Type': 'Item', 'InternalName': f'Test {i}'} for i in range(12)]
    cards += [{'Id': f'skill-{i}', 'Type': 'Skill', 'InternalName': f'Test {i}'} for i in range(2)]
    path = make_cards_db(tmp_path, cards)

    assert gdc.query_raw_by_name('Test 0', path, 'skill')['Id'] == 'skill-0'
    assert gdc.query_raw_by_name('Test 0', path, 'item')['Id'] == 'item-0'
    assert gdc.query_raw_by_name('Test 2', path, 'skill') is None
    suggestions = gdc.suggest_cards('Test', path, limit=10, kind='skill')
    assert {card['Id'] for card in suggestions} == {'skill-0', 'skill-1'}


def test_merchant_name_prefers_regular_record_over_tutorial_or_debug(tmp_path):
    gdc = load_gamedata_module()
    cards = [
        {'Id': 'tutorial', 'Type': 'EventEncounter', 'InternalName': 'Tutorial Day 1 - Mittel',
         'Localization': {'Title': {'Text': 'Mittel'}}},
        {'Id': 'debug', 'Type': 'EventEncounter', 'InternalName': '[DEBUG] Mittel',
         'Localization': {'Title': {'Text': 'Mittel'}}},
        {'Id': 'normal', 'Type': 'EventEncounter', 'InternalName': 'Mittel',
         'Localization': {'Title': {'Text': 'Mittel'}}},
    ]
    path = make_cards_db(tmp_path, cards)
    assert gdc.query_raw_by_name('Mittel', path)['Id'] == 'normal'
    assert gdc.query_raw_by_name('Tutorial Day 1 - Mittel', path)['Id'] == 'tutorial'


def test_valid_merchant_named_the_tester_is_not_treated_as_debug(tmp_path):
    gdc = load_gamedata_module()
    path = make_cards_db(tmp_path, [
        {'Id': 'tutorial', 'Type': 'EventEncounter', 'InternalName': 'Tutorial The Tester',
         'Localization': {'Title': {'Text': 'The Tester'}}},
        {'Id': 'normal', 'Type': 'EventEncounter', 'InternalName': 'The Tester'},
    ])
    assert gdc.query_raw_by_name('The Tester', path)['Id'] == 'normal'
