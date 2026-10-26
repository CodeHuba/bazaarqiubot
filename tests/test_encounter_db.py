import asyncio
import base64
from collections import defaultdict
from copy import deepcopy
import importlib
import io
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

from PIL import Image
import pytest
from test_bz_db_command import load_plugin_module


@pytest.fixture
def encounter_setup(monkeypatch,tmp_path):
    plugin=load_plugin_module(monkeypatch)
    data=importlib.import_module('plugins.bazaar_plugin.encounter_data')
    image=importlib.import_module('plugins.bazaar_plugin.encounter_image')
    def raw(entity_id,name,typ='EventEncounter',**values):
        return {'Id':entity_id,'Type':typ,'InternalName':name,'StartingTier':'Bronze','Size':'Medium',
                'Heroes':['Common'],'ArtKey':entity_id+'-art','Localization':{'Title':{'Text':name,'Key':entity_id},
                'Description':{'Text':'description','Key':entity_id+'-desc'}},**values}
    def context(entity_ids,free=False,multi=False,rerolls=None):
        return {'SpawnContext':{'$type':'TSpawnContextQuery','Limit':{'$type':'TFixedValue','Value':len(entity_ids)},
                'Groups':[{'Filters':[{'$type':'TSpawnFilterIdList','Ids':[i]}],'SelectionMethod':'Sequential'} for i in entity_ids]},
                'Rules':{'CanSelectMultiple':multi,'SelectionIsFree':free,'CanExit':True,'RerollRules':rerolls}}
    shop=raw('shop','Mittel',Tags=['Merchant'],SelectionContext=context([],multi=True,rerolls={'TotalAllowedRerolls':1,'StartingCost':2,'CostIncrease':0,'CostMax':None}))
    shop['SelectionContext']['SpawnContext']={'$type':'TSpawnContextQuery','Limit':{'$type':'TFixedValue','Value':3},'Groups':[
        {'Filters':[{'$type':'TSpawnFilterQuery','Constraints':{'$type':'ConstraintAnd','Constraints':[
            {'$type':'ConstraintSize','Sizes':['Medium']},{'$type':'ConstraintCardType','Types':['Item']} ]}}]}]}
    item=raw('ticket','Ticket','Item',Size='Small',StartingTier='Legendary',Tiers={'Legendary':{'Attributes':{'BuyPrice':12},'TooltipIds':[]}})
    travel=raw('travel','Travel Agent',SelectionContext=context(['ticket'],multi=True))
    def reward(entity_id,value):
        return raw(entity_id,'Salve','EncounterStep',Attributes={'ShieldApplyAmount':value},Abilities={'0':{
            'Action':{'$type':'TActionCardModifyAttribute','Value':{'$type':'TFixedValue','Value':value},'AttributeType':'ShieldApplyAmount',
                      'Target':{'$type':'TTargetCardSection','TargetSection':'SelfHandAndStash','Conditions':{'$type':'TCardConditionalHiddenTag','Tags':['Shield']}},
                      'Operation':'Add','Duration':None}}})
    first,second=reward('salve-1',5),reward('salve-2',10)
    upgrade=raw('upgrade','Ask','EncounterStep',Attributes={'BuyPrice':5},Abilities={'0':{
        'Action':{'$type':'TActionGameDealCards','SpawnMode':'Replace','SpawnContext':context(['salve-2'])['SpawnContext'],
                  'SelectionContextRules':context([])['Rules']}}})
    event=raw('event','Apothecary',SelectionContext=context(['salve-1','upgrade']))
    item_collision=raw('collision','Apothecary','Item',Tiers={'Bronze':{'Attributes':{},'TooltipIds':[]}})
    tutorial=deepcopy(shop);tutorial['Id']='tutorial';tutorial['InternalName']='Tutorial Mittel'
    database=tmp_path/'GameData.db'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE cards (Id TEXT,Data TEXT)');db.execute('CREATE TABLE monsters (Data TEXT)')
        db.executemany('INSERT INTO cards VALUES (?,?)',[(r['Id'],json.dumps(r)) for r in [shop,item,travel,first,second,upgrade,event,item_collision,tutorial]])
    translations={'shop':'米透','shop-desc':'出售中型物品','travel':'旅行代理人','ticket':'探险券',
                  'event':'药房','collision':'药房','event-desc':'提供增益','salve-1':'强化药膏','salve-2':'强化药膏',
                  'salve-1-desc':'己方物品永久获得{ability.0}','salve-2-desc':'己方物品永久获得{ability.0}',
                  'upgrade':'索要好货','upgrade-desc':'提升选项效果'}
    zh=tmp_path/'zh.json';zh.write_text(json.dumps(translations),encoding='utf-8')
    sources=tmp_path/'sources.json';sources.write_text(json.dumps({'entries':{},'appearance':{
        'event':{'day_label':'Day 3+','source_title':'官方补丁'},'travel':{'day_label':'Day 2–5'}}}),encoding='utf-8')
    images=tmp_path/'images.json';images.write_text('{}')
    repo=data.EncounterRepository(database,zh,images,sources)
    instance=plugin.BazaarPlugin();instance._encounter_repository=repo;instance._cooldown=defaultdict(float)
    instance.api=SimpleNamespace(post_private_msg=AsyncMock(),post_group_msg=AsyncMock())
    monkeypatch.setattr(plugin.card_data_paths,'get_gamedata_db_path',lambda *a,**k:database)
    monkeypatch.setattr(plugin.bdb,'query_card_by_name',lambda *a:pytest.fail('不可使用远程兜底'))
    monkeypatch.setattr(image,'_load',lambda url,fallback='',size=None,cache=None:Image.new('RGBA',{'Small':(200,400),'Large':(600,400)}.get(size,(400,400)),(60,120,160,255)))
    real_render=image.render_document
    monkeypatch.setattr(image,'render_document',lambda d:real_render(d,tmp_path/'renders',tmp_path/'art'))
    return SimpleNamespace(plugin=plugin,instance=instance,repo=repo,data=data,image=image,db=database,shop=shop,event=event,tmp=tmp_path)


def test_merchant_document_has_rules_without_sample_inventory(encounter_setup):
    s=encounter_setup;doc=s.repo.detail(s.repo.search('米透')[0]);text=s.data.format_document_text(doc)
    assert '出售中型物品' in text and '3 项商品' in text and '2 金币' in text
    assert not any(b['kind']=='option' for b in doc['blocks'])
    assert '举例' not in text and '展开商品池' not in text
    assert len(s.repo.search('Mittel'))==1


def test_event_effect_scope_and_paid_replacement_branch_are_preserved(encounter_setup):
    s=encounter_setup;doc=s.repo.detail(s.repo.search('药房')[0]);text=s.data.format_document_text(doc)
    assert 'Day 3+' in text and '场上及储藏区' in text
    assert '5护盾' in text and '5 金币' in text
    table=next(b for b in doc['blocks'] if b['kind']=='table')
    assert table['rows'][0]['values']==['0 金币','5 金币']
    assert table['rows'][1]['values']==['+5','+10']


def test_fixed_shop_uses_actual_merchandise_quality_price_and_native_ratio(encounter_setup):
    s=encounter_setup;doc=s.repo.detail(s.repo.search('旅行代理人')[0])
    option=next(b['option'] for b in doc['blocks'] if b['kind']=='option')
    assert option['quality']=='Legendary' and option['size']=='Small' and option['cost']=='12 金币'
    paths=s.image.render_document(doc)
    with Image.open(paths[0]) as image:assert image.width==1440 and image.height<=2400


@pytest.mark.parametrize('kind',['private','group'])
@pytest.mark.parametrize('query',['米透','商店 米透','商人 Mittel','事件 药房','遭遇 药房'])
def test_real_dispatch_sends_merchant_or_event_images(encounter_setup,kind,query):
    s=encounter_setup
    event=SimpleNamespace(message_type=kind,user_id=42,group_id=99)
    assert asyncio.run(s.instance._cmd_db(query,event)) is None
    api=s.instance.api.post_private_msg if kind=='private' else s.instance.api.post_group_msg
    assert api.await_count>=1
    for call in api.call_args_list:
        upload=call.kwargs['image'];assert upload.startswith('base64://')
        with Image.open(io.BytesIO(base64.b64decode(upload[9:]))) as image:
            assert image.width==1440 and image.height<=2400


def test_handle_does_not_send_a_text_reply_after_merchant_image(encounter_setup):
    s=encounter_setup
    event=SimpleNamespace(raw_message='#bz db 米透',message_type='private',user_id=12,reply=AsyncMock())
    asyncio.run(s.instance.handle(event))
    s.instance.api.post_private_msg.assert_awaited_once()
    event.reply.assert_not_awaited()


def test_merchant_image_upload_failure_falls_back_to_complete_rules(encounter_setup):
    s=encounter_setup;s.instance.api.post_private_msg.side_effect=RuntimeError('image rejected')
    text=asyncio.run(s.instance._cmd_db('米透',SimpleNamespace(message_type='private',user_id=42)))
    assert '米透（商店）' in text and '3 项商品' in text and '2 金币' in text


def test_item_and_event_same_name_require_type(encounter_setup,monkeypatch):
    s=encounter_setup
    translations=importlib.import_module('plugins.bazaar_plugin.translations')
    monkeypatch.setattr(translations,'get_en',lambda name:'Apothecary' if name=='药房' else None)
    result=asyncio.run(s.instance._cmd_db('药房'))
    assert '#bz db 物品 药房' in result and '#bz db 事件 药房' in result


def test_zero_rerolls_is_unavailable_even_when_start_cost_exists(encounter_setup):
    s=encounter_setup
    with sqlite3.connect(s.db) as db:
        shop=deepcopy(s.shop);shop['SelectionContext']['Rules']['RerollRules']['TotalAllowedRerolls']=0
        db.execute('UPDATE cards SET Data=? WHERE Id=?',(json.dumps(shop),shop['Id']))
    doc=s.repo.detail(s.repo.search('米透')[0])
    assert dict(doc['facts'])['刷新规则']=='不可刷新'


def test_long_event_options_paginate_without_omission(encounter_setup):
    s=encounter_setup;doc=s.repo.detail(s.repo.search('药房')[0])
    block=next(b for b in doc['blocks'] if b['kind']=='option')
    block['option']['lines']=['很长的选项说明。'*12 for _ in range(90)]
    pages,description=s.image._prepare(doc)
    original_lines=sum(len(s.image.Sheet(10).wrap(line,960,27)) for line in block['option']['lines'])
    retained=sum(len(b['lines']) for p in pages for b in p['blocks'] if b['kind']=='option' and b['option']['id']==block['option']['id'])
    assert retained==original_lines and len(pages)>1
    assert all(p['end']+118<=2400 for p in pages)


def test_nested_run_conditions_do_not_leak_data_field_names(encounter_setup):
    s=encounter_setup;s.repo._refresh()
    text=s.repo.condition({'$type':'TPrerequisiteRun','Conditions':{
        '$type':'TRunConditionalCurrentDay','CurrentDay':5,'ComparisonOperator':'GreaterThanOrEqual'}})
    assert text=='天数至少 Day 5'
    assert '$type' not in text and 'CurrentDay' not in text


def test_large_random_followup_pool_is_summarized_not_rendered_as_guaranteed_rewards(encounter_setup):
    s=encounter_setup
    with sqlite3.connect(s.db) as db:
        upgrade=json.loads(db.execute('SELECT Data FROM cards WHERE Id=?',('upgrade',)).fetchone()[0])
        loot=[]
        for i in range(20):
            item=json.loads(db.execute('SELECT Data FROM cards WHERE Id=?',('ticket',)).fetchone()[0])
            item['Id']=f'loot-{i}';loot.append(item['Id']);db.execute('INSERT INTO cards VALUES (?,?)',(item['Id'],json.dumps(item)))
        upgrade['Abilities']['0']['Action']['SpawnContext']={'$type':'TSpawnContextQuery',
            'SelectionMethod':'Random','Limit':{'$type':'TFixedValue','Value':1},
            'Groups':[{'Filters':[{'Ids':loot}],'SelectionMethod':'Random'}]}
        db.execute('UPDATE cards SET Data=? WHERE Id=?',(json.dumps(upgrade),'upgrade'))
    doc=s.repo.detail(s.repo.search('药房')[0])
    text=s.data.format_document_text(doc)
    assert '生成 1 项后续选项' in text
    assert len([b for b in doc['blocks'] if b['kind']=='option'])==2
