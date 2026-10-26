import asyncio
from copy import deepcopy
import importlib
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

from PIL import Image
import pytest
from test_bz_db_command import load_plugin_module


@pytest.fixture
def setup_monsters(monkeypatch, tmp_path):
    plugin_module = load_plugin_module(monkeypatch)
    data = importlib.import_module('plugins.bazaar_plugin.monster_data')
    image = importlib.import_module('plugins.bazaar_plugin.monster_image')
    database = tmp_path/'GameData.db'
    item = {'Id': 'item', 'Type': 'Item', 'Size': 'Small', 'StartingTier': 'Silver', 'ArtKey': 'item-art',
            'InternalName': 'Ice Blade', 'Localization': {'Title': {'Key': 'item-name', 'Text': 'Ice Blade'},
            'Tooltips': [{'Content': {'Key': 'damage', 'Text': 'Deal {ability.0} Damage'}}]},
            'Tiers': {'Silver': {'Attributes': {'DamageAmount': 10, 'CooldownMax': 7000}, 'TooltipIds': [0]},
                      'Gold': {'Attributes': {'DamageAmount': 20}, 'TooltipIds': [0]}},
            'Abilities': {'0': {'Action': {'$type': 'TActionPlayerDamage'}}}}
    skill = {'Id': 'skill', 'Type': 'Skill', 'Size': 'Medium', 'StartingTier': 'Diamond', 'ArtKey': 'skill-art',
             'InternalName': 'Ice Mind', 'Localization': {'Title': {'Key': 'skill-name', 'Text': 'Ice Mind'},
             'Tooltips': [{'Content': {'Key': 'skill-tip', 'Text': 'All Charge effects are reduced by half'}}]},
             'Tiers': {'Diamond': {'Attributes': {}, 'TooltipIds': [0]}}}
    encounter = {'Id': 'encounter', 'Type': 'CombatEncounter', 'InternalName': 'Frost Beast', 'Version': 'fixture',
                 'StartingTier': 'Gold', 'CombatantType': {'MonsterTemplateId': 'monster', 'Level': 7},
                 'Localization': {'Title': {'Key': 'monster-name', 'Text': 'Frost Beast'}},
                 'RewardCombatGold': 4, 'RewardCombatXp': 3, 'SandstormEnabled': True}
    monster = {'Id': 'monster', 'InternalName': 'Frost Beast', 'Player': {'Attributes': {'Level': 7, 'HealthMax': 1200},
               'Hand': {'Items': [{'TemplateId': 'item', 'Tier': 'Gold', 'SocketId': 'Socket_3', 'Attributes': {'DamageAmount': 42}}]},
               'Skills': [{'TemplateId': 'skill', 'Tier': 'Diamond'}]}}
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE cards (Id TEXT, Data TEXT)')
        db.execute('CREATE TABLE monsters (Data TEXT)')
        db.executemany('INSERT INTO cards VALUES (?,?)', [(c['Id'], json.dumps(c)) for c in [item, skill, encounter]])
        db.execute('INSERT INTO monsters VALUES (?)', (json.dumps(monster),))
    translated = tmp_path/'zh.json'
    translated.write_text(json.dumps({'item-name': '冰刃', 'damage': '造成{ability.0}伤害', 'skill-name': '冰心',
                                      'skill-tip': '双方所有充能效果减半', 'monster-name': '霜兽'}), encoding='utf-8')
    images = tmp_path/'images.json'
    images.write_text('{}')
    sources = tmp_path/'sources.json'
    sources.write_text(json.dumps({'retrieved_at': '2026-10-26', 'encounters': [
        {'name': 'Frost Beast', 'level': 7, 'tier': 'Gold', 'day': '10+', 'art': 'https://s.bazaardb.gg/v1/test/monster@256.webp'}]}))
    repo = data.MonsterRepository(database, translated, images, sources)
    return SimpleNamespace(plugin=plugin_module, data=data, image=image, repo=repo, db=database, encounter=encounter)


def test_monster_uses_official_names_actual_tiers_skills_overrides_and_days(setup_monsters):
    s = setup_monsters
    assert s.repo.search('FROST   BEAST')[0]['id'] == s.repo.search('霜兽')[0]['id']
    assert s.repo.search('霜') == []
    assert len(s.repo.search('霜', False)) == 1
    detail = s.repo.detail(s.repo.search('霜兽')[0])
    assert detail['day'] == '10+'
    assert detail['health'] == 1200 and detail['gold'] == 4
    assert detail['items'][0]['quality'] == 'Gold'
    assert detail['items'][0]['slot'] == 'Socket_3'
    assert detail['items'][0]['effects'][0]['text'] == '造成42伤害'
    assert detail['skills'][0]['name'] == '冰心'
    assert detail['skills'][0]['quality'] == 'Diamond'
    assert 'Day 10+' in s.data.format_monster_text(detail)


def test_same_named_encounters_keep_distinct_forms_and_missing_day_is_not_guessed(setup_monsters):
    s = setup_monsters
    second = deepcopy(s.encounter)
    second['Id'] = 'second'
    second['CombatantType']['Level'] = 12
    with sqlite3.connect(s.db) as db:
        db.execute('INSERT INTO cards VALUES (?,?)', ('second', json.dumps(second)))
    matches = s.repo.search('霜兽')
    assert len(matches) == 2
    later = next(m for m in matches if m['id'] == 'second')
    assert later['day'] == ''
    assert s.data.day_label(later['day']) == '暂无可靠天数资料'


def test_missing_historical_tier_is_marked_instead_of_using_a_different_tier(setup_monsters):
    s = setup_monsters
    with sqlite3.connect(s.db) as db:
        monster = json.loads(db.execute('SELECT Data FROM monsters').fetchone()[0])
        monster['Player']['Hand']['Items'][0]['Tier'] = 'Bronze'
        db.execute('UPDATE monsters SET Data=?', (json.dumps(monster),))
    detail = s.repo.detail(s.repo.search('霜兽')[0])
    card = detail['items'][0]
    assert card['quality'] == 'Bronze' and card['name'] == '冰刃'
    assert '该品质资料暂缺' in card['effects'][0]['text']


@pytest.mark.parametrize('size,suffix,ratio', [('Small', 'S', .5), ('Medium', '', 1), ('Large', 'L', 1.5)])
def test_native_art_shape_replaces_square_thumbnail(size, suffix, ratio, setup_monsters):
    module = setup_monsters.image
    assert module.native_art_url('https://s.bazaardb.gg/v1/z19.0/art@256.webp?v=8', size) == f'https://s.bazaardb.gg/v1/z19.0/art@400{suffix}.webp?v=8'
    assert module._valid_ratio(Image.new('RGB', (int(400*ratio), 400)), size)
    assert not module.native_art_url('https://example.com/art.webp', size)
    if size != 'Medium':
        assert not module._valid_ratio(Image.new('RGB', (400, 400)), size)


def test_renderer_handles_multiple_board_rows_skills_and_long_effects(setup_monsters, monkeypatch, tmp_path):
    s = setup_monsters
    detail = s.repo.detail(s.repo.search('霜兽')[0])
    detail['items'] = [deepcopy(detail['items'][0]) for _ in range(11)]
    for index, card in enumerate(detail['items']):
        card['slot'] = f'Socket_{index}'
    detail['skills'] = [deepcopy(detail['skills'][0]) for _ in range(5)]
    detail['skills'][0]['effects'][0]['text'] = '很长的官方技能说明。'*30
    monkeypatch.setattr(s.image, 'load_art', lambda *args: None)
    result = s.image.render_monster(detail, tmp_path/'render', tmp_path/'art')
    with Image.open(result) as image:
        assert image.width == 1440 and image.height > 3000


def test_command_dispatches_monsters_and_uploads_base64_without_reply_truncation(setup_monsters, monkeypatch, tmp_path):
    s = setup_monsters
    plugin = s.plugin.BazaarPlugin()
    plugin._monster_repository = s.repo
    plugin.api = SimpleNamespace(post_private_msg=AsyncMock(), post_group_msg=AsyncMock())
    monkeypatch.setattr(s.plugin.card_data_paths, 'get_gamedata_db_path', lambda *a, **k: s.db)
    image = tmp_path/'test.png'
    Image.new('RGB', (120, 240)).save(image)
    monkeypatch.setattr(s.image, 'render_monster', lambda *args: str(image))
    event = SimpleNamespace(message_type='private', user_id=42)
    assert asyncio.run(plugin._cmd_db('霜兽', event)) is None
    args = plugin.api.post_private_msg.call_args.kwargs
    assert args['user_id'] == 42 and args['image'].startswith('base64://')
    plugin.api.post_private_msg.side_effect = RuntimeError('image rejected')
    result = asyncio.run(plugin._cmd_db('野怪 Frost Beast', event))
    assert '冰刃［黄金］' in result and '冰心［钻石］' in result


def test_monster_and_card_name_collision_requires_type(setup_monsters, monkeypatch):
    s = setup_monsters
    plugin = s.plugin.BazaarPlugin()
    plugin._monster_repository = s.repo
    monkeypatch.setattr(s.plugin.card_data_paths, 'get_gamedata_db_path', lambda *a, **k: s.db)
    game = importlib.import_module('plugins.bazaar_plugin.gamedata_client')
    monkeypatch.setattr(game, 'query_raw_by_name', lambda *a: {'Type': 'Item', 'InternalName': 'Frost Beast'})
    result = asyncio.run(plugin._cmd_db('霜兽'))
    assert '#bz db 物品 霜兽' in result and '#bz db 野怪 霜兽' in result


def test_form_id_with_hex_e_is_not_eaten_as_enchant_flag(setup_monsters, monkeypatch):
    s = setup_monsters
    plugin = s.plugin.BazaarPlugin()
    plugin._monster_repository = s.repo
    monkeypatch.setattr(s.plugin.card_data_paths, 'get_gamedata_db_path', lambda *a, **k: s.db)
    new_id = '11111111-eaaa-4444-8888-111111111111'
    with sqlite3.connect(s.db) as db:
        entry = deepcopy(s.encounter)
        entry['Id'] = new_id
        db.execute('INSERT INTO cards VALUES (?,?)', (new_id, json.dumps(entry)))
    result = asyncio.run(plugin._cmd_db('野怪 霜兽 --形态 '+new_id))
    assert '冰刃［黄金］' in result


def test_partial_monster_name_keeps_all_forms_after_suggestion_deduplication(setup_monsters, monkeypatch):
    s = setup_monsters
    plugin = s.plugin.BazaarPlugin()
    plugin._monster_repository = s.repo
    monkeypatch.setattr(s.plugin.card_data_paths, 'get_gamedata_db_path', lambda *a, **k: s.db)
    with sqlite3.connect(s.db) as db:
        extra = deepcopy(s.encounter)
        extra['Id'] = 'another-form'
        db.execute('INSERT INTO cards VALUES (?,?)', (extra['Id'], json.dumps(extra)))
    result = asyncio.run(plugin._cmd_db('野怪 霜'))
    assert '找到多个野怪形态' in result
    assert '--形态 another-form' in result
    assert result.count('（野怪｜') == 2
