import asyncio
from copy import deepcopy
import importlib
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

from PIL import Image
import pytest
from test_monster_db import setup_monsters


@pytest.fixture
def cards(setup_monsters):
    s = setup_monsters
    s.sheet = importlib.import_module('plugins.bazaar_plugin.card_sheet')
    with sqlite3.connect(s.db) as db:
        raw = json.loads(db.execute('SELECT Data FROM cards WHERE Id="item"').fetchone()[0])
        raw['Tiers']['Silver']['Attributes']['FreezeAmount'] = 1000
        raw['Enchantments'] = {'Icy': {'Auras': {'double': {'Action': {
            '$type': 'TAuraActionCardModifyAttribute', 'AttributeType': 'FreezeAmount',
            'Operation': 'Multiply', 'Target': {'$type': 'TTargetCardSelf'},
            'Value': {'$type': 'TFixedValue', 'Value': 2}}}},
            'Localization': {'Tooltips': [{'Content': {'Text': '冻结时长翻倍'}}]}}}
        db.execute('UPDATE cards SET Data=? WHERE Id="item"', (json.dumps(raw),))
    s.raw = raw
    return s


def test_all_qualities_and_enchant_self_modifiers_use_consistent_attributes(cards):
    doc = cards.sheet.build_document(cards.raw, cards.db, repository=cards.repo.preview)
    assert [v['quality'] for v in doc['variants']] == ['Silver', 'Gold']
    assert next(r for r in doc['comparison'] if r['label'] == '伤害')['values'] == ['10', '20']
    selected = cards.sheet.build_document(cards.raw, cards.db, quality='黄金', enchantment='Icy', repository=cards.repo.preview)
    assert [v['quality'] for v in selected['variants']] == ['Gold']
    assert next(r for r in selected['comparison'] if r['label'] == '冻结时长')['values'] == ['2秒']


def test_hidden_tags_reach_image_text_and_enchant_summary_without_mixing_base(cards):
    raw = deepcopy(cards.raw)
    raw['HiddenTags'] = ['Shield', 'ShieldReference']
    raw['Enchantments']['Icy']['HiddenTags'] = ['Freeze', 'FreezeReference', 'Shield']
    with sqlite3.connect(cards.db) as db:
        db.execute('UPDATE cards SET Data=? WHERE Id="item"', (json.dumps(raw),))
    doc = cards.sheet.build_document(raw, cards.db, show_enchants=True, repository=cards.repo.preview)
    assert doc['hiddenTags'] == ['护盾', '护盾相关']
    assert doc['enchants'][0]['hiddenTags'] == ['冻结', '冻结相关']
    text = cards.sheet.format_document_text(doc)
    assert '隐藏标签：护盾、护盾相关' in text
    assert '新增隐藏标签：冻结、冻结相关' in text
    blocks = cards.sheet._blocks(doc)
    assert blocks[0]['kind'] == 'tags' and blocks[0]['rows'] == [['护盾', '护盾相关']]
    enchant_text = ''.join(t for b in blocks if b['kind'] == 'enchant' for line in b['lines'] for t, _ in line)
    assert '新增隐藏标签：冻结 · 冻结相关' in enchant_text
    selected = cards.sheet.build_document(raw, cards.db, enchantment='Icy', repository=cards.repo.preview)
    assert selected['hiddenTags'] == ['护盾', '护盾相关', '冻结', '冻结相关']


def test_empty_hidden_tags_are_explicit_and_many_tags_wrap_and_paginate(cards):
    doc = cards.sheet.build_document(cards.raw, cards.db, repository=cards.repo.preview)
    assert '隐藏标签：无' in cards.sheet.format_document_text(doc)
    doc['hiddenTags'] = [f'测试关联标签{i}' for i in range(180)]
    pages = cards.sheet.paginate(doc)
    assert len(pages) > 1
    assert all(p['end']+120 <= 2400 for p in pages)
    tags = [tag for p in pages for b in p['blocks'] if b['kind'] == 'tags' for row in b['rows'] for tag in row]
    assert tags == doc['hiddenTags']


def test_legacy_text_fallback_includes_native_tags_and_enchant_additions(cards, monkeypatch):
    trans = importlib.import_module('plugins.bazaar_plugin.translations')
    monkeypatch.setattr(trans, 'get_zh_by_text_key', lambda tag: {'Shield': '护盾', 'Poison': '剧毒'}.get(tag))
    raw = deepcopy(cards.raw)
    raw['HiddenTags'] = ['Shield', 'ShieldReference', 'Shield']
    raw['Enchantments']['Icy']['HiddenTags'] = ['PoisonReference', 'Shield']
    text = cards.sheet.game.format_card_from_raw(raw, show_enchants=True)
    assert '隐藏标签: 护盾、护盾相关' in text
    assert '新增隐藏标签: 剧毒相关' in text


def test_effect_merge_retains_each_quality_even_when_values_repeat(cards):
    effects = [[{'text': f'冻结{n}件物品1秒', 'type': 'Active', 'source': 'base'}] for n in [1, 1, 2]]
    result = cards.sheet._segments(effects, ['Silver', 'Gold', 'Diamond'])
    line = ''.join(text for text, _ in result[0][1])
    assert line == '冻结1 / 1 / 2件物品1秒'
    effects[2][0]['condition'] = '冰冻时'
    assert cards.sheet._segments(effects, ['Silver', 'Gold', 'Diamond']) is None


def test_inherent_lifesteal_is_shown_without_mutating_shared_preview_cache(cards):
    with sqlite3.connect(cards.db) as db:
        raw = deepcopy(cards.raw)
        raw['Tiers']['Silver']['Attributes']['Lifesteal'] = 100
        db.execute('UPDATE cards SET Data=? WHERE Id="item"', (json.dumps(raw),))
    doc = cards.sheet.build_document(raw, cards.db, repository=cards.repo.preview)
    assert all(any(e['text'] == '吸血' for e in v['effects']) for v in doc['variants'])
    cached = cards.repo.preview.get('item', 'Silver')
    assert not any(e['text'] == '吸血' for e in cached['effects'])


@pytest.mark.parametrize('heroes,expected', [(['Vanessa', 'Mak', 'Karnok'], '瓦内莎、马克、卡诺克'),
                                           (['Common'], '全部英雄（通用）'), ([], '资料暂缺')])
def test_skill_applicable_heroes_are_explicit_including_text_fallback(cards, heroes, expected):
    with sqlite3.connect(cards.db) as db:
        skill = json.loads(db.execute('SELECT Data FROM cards WHERE Id="skill"').fetchone()[0])
        skill['Heroes'] = heroes
        db.execute('UPDATE cards SET Data=? WHERE Id="skill"', (json.dumps(skill),))
    doc = cards.sheet.build_document(skill, cards.db, repository=cards.repo.preview)
    assert cards.sheet.hero_label(doc) == expected
    assert '适用英雄：'+expected in cards.sheet.format_document_text(doc)


def test_enchant_total_includes_base_effect_changes(cards):
    with sqlite3.connect(cards.db) as db:
        raw = deepcopy(cards.raw)
        raw['Enchantments']['Icy']['Attributes'] = {'DamageAmount': 25}
        db.execute('UPDATE cards SET Data=? WHERE Id="item"', (json.dumps(raw),))
    doc = cards.sheet.build_document(raw, cards.db, show_enchants=True, repository=cards.repo.preview)
    assert len(doc['enchants']) == 1
    groups = doc['enchants'][0]['groups']
    assert len(groups) == 2
    assert all(g['changes'][0]['text'] == '造成25伤害' for g in groups)
    assert '原有效果变更' in cards.sheet.format_document_text(doc)
    with pytest.raises(ValueError):
        cards.sheet.build_document(raw, cards.db, quality='不存在', repository=cards.repo.preview)


def test_long_tooltips_and_enchants_are_paginated_without_losing_content(cards, monkeypatch, tmp_path):
    doc = cards.sheet.build_document(cards.raw, cards.db, show_enchants=True, repository=cards.repo.preview)
    for v in doc['variants']:
        v['effects'] = [{'text': '很长的官方说明。'*600, 'type': 'Passive', 'source': 'quest', 'condition': '任务目标'}]
    pages = cards.sheet.paginate(doc)
    assert len(pages) >= 3
    assert all(p['end']+120 <= 2400 for p in pages)
    rendered = ''.join(t for p in pages for b in p['blocks'] if b['kind'] == 'effect' for line in b['lines'] for t, _ in line)
    assert rendered.count('很长的官方说明。') == 600
    assert any(b['kind'] == 'enchant' for p in pages for b in p['blocks'])
    monkeypatch.setattr(cards.sheet, 'load_art', lambda *a, **k: None)
    images = cards.sheet.render_document(doc, tmp_path/'render', tmp_path/'cache')
    assert len(images) == len(pages)
    for path in images:
        with Image.open(path) as image:
            assert image.width == 1440 and image.height <= 2400


@pytest.mark.parametrize('message_type', ['private', 'group'])
def test_card_command_uploads_all_pages_and_falls_back_when_later_page_fails(cards, monkeypatch, tmp_path, message_type):
    s = cards
    plugin = s.plugin.BazaarPlugin()
    plugin._card_sheet_repository = s.repo.preview
    plugin.api = SimpleNamespace(post_private_msg=AsyncMock(), post_group_msg=AsyncMock())
    monkeypatch.setattr(s.plugin.card_data_paths, 'get_gamedata_db_path', lambda *a, **k: s.db)
    monkeypatch.setattr(s.sheet, 'render_document', lambda *a: [str(tmp_path/'1.png'), str(tmp_path/'2.png')])
    for name in ['1.png', '2.png']:
        Image.new('RGB', (20, 40)).save(tmp_path/name)
    event = SimpleNamespace(message_type=message_type, user_id=1, group_id=2)
    assert asyncio.run(plugin._cmd_db('Ice Blade -e', event)) is None
    sender = plugin.api.post_private_msg if message_type == 'private' else plugin.api.post_group_msg
    assert sender.await_count == 2
    assert all(call.kwargs['image'].startswith('base64://') for call in sender.call_args_list)
    sender.side_effect = [None, ValueError('image rejected')]
    result = asyncio.run(plugin._cmd_db('Ice Blade -e', event))
    assert '冰刃' in result and '附魔' in result


def test_explicit_quality_and_enchant_are_parsed_and_removed_from_card_name(cards, monkeypatch):
    s = cards
    plugin = s.plugin.BazaarPlugin()
    monkeypatch.setattr(s.plugin.card_data_paths, 'get_gamedata_db_path', lambda *a, **k: s.db)
    plugin._cmd_card_db = AsyncMock(return_value='rendered')
    event = SimpleNamespace()
    assert asyncio.run(plugin._cmd_db('物品 Ice Blade --品质 黄金 --附魔 寒冰', event)) == 'rendered'
    kwargs = plugin._cmd_card_db.call_args.kwargs
    assert kwargs == {'quality': '黄金', 'enchantment': '寒冰'}
    assert plugin._cmd_card_db.call_args.args[2] == 'Ice Blade'
    assert '指定一次' in asyncio.run(plugin._cmd_db('Ice Blade --品质 黄金 --品质 白银', event))
    assert '请用 -e' in asyncio.run(plugin._cmd_db('Ice Blade -e --附魔 寒冰', event))
