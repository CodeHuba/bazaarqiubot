import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace,ModuleType

import pytest

@pytest.fixture
def setup(monkeypatch):
    package=ModuleType('items_test');package.__path__=[str(Path(__file__).parent)]
    monkeypatch.setitem(sys.modules,'items_test',package)
    helper=ModuleType('items_test.card_image_helper')
    monkeypatch.setitem(sys.modules,'items_test.card_image_helper',helper)
    spec=importlib.util.spec_from_file_location('items_test.command',Path(__file__).resolve().parents[1]/'plugins/bazaar_plugin/winrate_command.py')
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    records={'ship':('Shipwreck','船骸','Item'),'anchor':('Anchor','船锚','Item'),
        'event':('Library','图书馆','EventEncounter'),'library':('Library','图书馆','Item'),
        'skill':('Library Arsonist','图书馆纵火者','Skill'),
        'debug':('[DEBUG] Shipwreck','船骸','Item'),'clock':('Atomic Clock','原子钟','Item')}
    helper._load=lambda:{'cards':{cid:dict(internalName=en,name=zh,type=kind) for cid,(en,zh,kind) in records.items()}}
    def display(cid):
        en,zh,kind=records[cid];return dict(name_en=en,name=zh)
    q=SimpleNamespace(conn=object(),name_to_ids={'shipwreck':['ship','debug'],'anchor':['anchor'],
        'library':['event','library'],'library arsonist':['skill'],'[DEBUG] Shipwreck':['debug'],
        'atomic clock':['clock']},card_mapping={cid:dict(name=en,type=kind) for cid,(en,zh,kind) in records.items()},
        card_aliases={'破船':'船骸','纵火':'Library Arsonist'},card_display_info=display)
    return mod,q

def test_exact_items_and_aliases_query_after_type_and_debug_filter(setup):
    mod,q=setup
    assert mod.resolve_cards(q,['破船','船锚','图书馆'])==['Shipwreck','Anchor','Library']
    assert q.name_to_ids['library']==['library']
    assert q.name_to_ids['shipwreck']==['ship']
    assert 'library arsonist' not in q.name_to_ids
    assert not any('[debug]' in key.lower() for key in q.name_to_ids)

@pytest.mark.parametrize('word,expected',[('船锚锚','Anchor'),('原子','Atomic Clock'),('图书','Library')])
def test_unique_item_candidate_is_automatically_resolved(setup,word,expected):
    mod,q=setup
    assert mod.resolve_cards(q,[word])==[expected]

def test_multiple_item_candidates_require_selection(setup):
    mod,q=setup
    with pytest.raises(mod.WinrateInputError,match='请选择物品') as error:
        mod.resolve_cards(q,['船'])
    assert '图书馆纵火者' not in str(error.value)
    assert '（物品）' in str(error.value)

@pytest.mark.parametrize('word',['图书馆纵火者','纵火','不存在的卡牌000000000'])
def test_skills_and_unknown_names_never_resolve(setup,word):
    mod,q=setup
    with pytest.raises(mod.WinrateInputError) as error:mod.resolve_cards(q,[word])
    if word in ('图书馆纵火者','纵火'):
        assert '不是物品' in str(error.value)

def test_mixed_combination_stops_and_keeps_other_conditions_in_instruction(setup):
    mod,q=setup
    with pytest.raises(mod.WinrateInputError,match='保留组合中的其他物品及筛选参数'):
        mod.resolve_cards(q,['图书馆','船'])

def test_combination_auto_resolves_unique_candidates_and_deduplicates(setup):
    mod,q=setup
    assert mod.resolve_cards(q,['船骸','原子','Atomic Clock'])==['Shipwreck','Atomic Clock']

def test_candidate_selection_considers_all_matches_before_display_limit(setup):
    mod,q=setup
    for index in range(10):
        cid='ship'+str(index)
        q.card_mapping[cid]=dict(type='Item',name='Ship'+str(index))
        q.name_to_ids['ship'+str(index)]=[cid]
    original=q.card_display_info
    q.card_display_info=lambda cid:dict(name_en=q.card_mapping[cid]['name'],name=q.card_mapping[cid]['name']) if cid.startswith('ship') and cid!='ship' else original(cid)
    with pytest.raises(mod.WinrateInputError,match='请选择物品'):
        mod.resolve_cards(q,['shi'])

def test_exact_english_name_and_duplicates_still_work(setup):
    mod,q=setup
    assert mod.resolve_cards(q,['Atomic','Clock'],space_mode=True)==['Atomic Clock']
    assert mod.resolve_cards(q,['船骸','Shipwreck','破船'])==['Shipwreck']


@pytest.mark.parametrize('kind',['Skill','TCardSkill','EventEncounter','Monster','Merchant','other',''])
def test_fuzzy_candidate_list_filters_type_before_matching(setup,kind):
    mod,q=setup
    helper=sys.modules['items_test.card_image_helper']
    catalog=helper._load()['cards']
    catalog['nonitem']=dict(internalName='Ship Spell',name='船技能或其他类型',type=kind)
    helper._load=lambda:{'cards':catalog}
    q.card_mapping['nonitem']=dict(name='Ship Spell',type=kind)
    q.name_to_ids['ship spell']=['nonitem']
    original=q.card_display_info
    q.card_display_info=lambda cid:dict(name_en='Ship Spell',name='船技能或其他类型') if cid=='nonitem' else original(cid)
    with pytest.raises(mod.WinrateInputError) as error:
        mod.resolve_cards(q,['船'])
    suggestions=[line.split('. ',1)[1] for line in str(error.value).splitlines() if '. ' in line]
    assert set(suggestions)=={'船骸（物品）','船锚（物品）'}


def test_unique_item_auto_query_ignores_same_prefix_skills(setup):
    mod,q=setup
    helper=sys.modules['items_test.card_image_helper']
    catalog=helper._load()['cards']
    catalog['spell']=dict(internalName='Atomic Spell',name='原子法术',type='Skill')
    helper._load=lambda:{'cards':catalog}
    q.card_mapping['spell']=dict(name='Atomic Spell',type='Skill')
    q.name_to_ids['atomic spell']=['spell']
    assert mod.resolve_cards(q,['原子'])==['Atomic Clock']


@pytest.mark.parametrize('order',[['community','katana'],['katana','community']])
@pytest.mark.parametrize('word',['武士刀','Katana'])
def test_katana_prefers_ordinary_native_item_over_community_variant(setup,order,word):
    mod,q=setup
    helper=sys.modules['items_test.card_image_helper']
    catalog=helper._load()['cards']
    catalog['community']=dict(type='Item',internalName='Katana',name='武士刀',nativeInternalName='[Community Team] Katana')
    catalog['katana']=dict(type='Item',internalName='Katana',name='武士刀',nativeInternalName='Katana')
    helper._load=lambda:{'cards':catalog}
    q.card_mapping.update({cid:dict(type='item',name='Katana') for cid in ('community','katana')})
    q.name_to_ids.update({'[community team] katana':['community'],'katana':order})
    original=q.card_display_info
    q.card_display_info=lambda cid:dict(name_en='Katana',name='武士刀') if cid in ('community','katana') else original(cid)
    assert mod.resolve_cards(q,[word])==['Katana']
    assert q.name_to_ids['katana']==['katana']
    assert all('community' not in ids for ids in q.name_to_ids.values())


def test_same_chinese_name_with_different_public_english_names_is_not_merged(setup):
    mod,q=setup
    helper=sys.modules['items_test.card_image_helper']
    catalog=helper._load()['cards']
    catalog['second']=dict(type='Item',internalName='Other Library',name='图书馆',nativeInternalName='Other Library')
    helper._load=lambda:{'cards':catalog}
    q.card_mapping['second']=dict(type='item',name='Other Library')
    q.name_to_ids['other library']=['second']
    original=q.card_display_info
    q.card_display_info=lambda cid:dict(name_en='Other Library',name='图书馆') if cid=='second' else original(cid)
    with pytest.raises(mod.WinrateInputError,match='请选择物品'):
        mod.resolve_cards(q,['图书馆'])


@pytest.mark.parametrize('word',['冷库','Freezer','Walk-In Freezer'])
def test_old_and_current_names_for_one_id_are_one_candidate(setup,word):
    mod,q=setup
    helper=sys.modules['items_test.card_image_helper']
    catalog=helper._load()['cards']
    catalog['freezer']=dict(type='Item',internalName='Freezer',name='冷库',nativeInternalName='Walk-In Freezer')
    helper._load=lambda:{'cards':catalog}
    q.card_mapping['freezer']=dict(type='item',name='Freezer')
    q.name_to_ids.update({'walk-in freezer':['freezer'],'freezer':['freezer']})
    original=q.card_display_info
    q.card_display_info=lambda cid:dict(name_en='Freezer',name='冷库') if cid=='freezer' else original(cid)
    assert mod.resolve_cards(q,[word])==['Freezer']
    assert mod.resolve_cards(q,['冷库','Freezer','Walk-In Freezer'])==['Freezer']


def test_overlapping_alias_id_sets_merge_transitively(setup):
    mod,q=setup
    # Aliases with shared identities must not become independent candidates.
    q.name_to_ids.update({'old ship':['ship'],'shipwreck':['ship','anchor'],'older ship':['anchor']})
    assert mod.resolve_cards(q,['船骸','old ship','older ship'])==['Shipwreck']


@pytest.mark.parametrize('arg,expected',[
    ('船骸+原子',['Shipwreck','Atomic Clock']),
    ('图书馆',['Library']),
    ('"Atomic Clock" + 船锚 --hero 海盗 --days 3 --legendary',['Atomic Clock','Anchor']),
    ('船骸+图书馆纵火者',None),
    ('船',None),
])
def test_plugin_uses_item_resolver_before_statistics(setup,monkeypatch,arg,expected):
    import asyncio,importlib
    from test_bz_db_command import load_plugin_module
    _,q=setup
    plugin_module=load_plugin_module(monkeypatch)
    queries=importlib.import_module('plugins.bazaar_plugin.runs_query')
    helper=importlib.import_module('plugins.bazaar_plugin.card_image_helper')
    monkeypatch.setattr(helper,'_load',sys.modules['items_test.card_image_helper']._load)
    closed=[];calls=[]
    q.conn=SimpleNamespace(close=lambda:closed.append(True))
    q.resolve_hero=lambda name:'Vanessa' if name=='海盗' else None
    def winrate(**kwargs):
        calls.append(kwargs)
        if kwargs['cards']==['Library']:assert q.name_to_ids['library']==['library']
        return dict(card_names=['测试物品'],total=2,ten_win=1,rate=.5)
    q.winrate=winrate
    monkeypatch.setattr(queries,'RunsQuery',lambda:q)
    reply=asyncio.run(plugin_module.BazaarPlugin()._cmd_winrate(arg))
    assert closed==[True]
    if expected is None:
        assert not calls and ('不是物品' in reply or '请选择物品' in reply)
    else:
        assert len(calls)==1 and calls[0]['cards']==expected
        assert '50.0%' in reply
        if '--hero' in arg:
            assert calls[0]['hero']=='Vanessa' and calls[0]['days']==3 and calls[0]['rank_filter']=='legendary'
