import asyncio
import base64
from datetime import datetime, timezone
import hashlib
import importlib
import io
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from PIL import Image, ImageDraw

from test_bz_db_command import load_plugin_module


@pytest.fixture
def f(monkeypatch, tmp_path):
    plugin_module = load_plugin_module(monkeypatch)
    stats = importlib.import_module('plugins.bazaar_plugin.secondary_hero_stats')
    service = importlib.import_module('plugins.bazaar_plugin.secondary_hero_service')
    cards = service.cards
    names = service.HERO_EN_TO_ZH
    event = dict(id='test-event', name='赛季末副角色活动', season=19, phases=['19.4'],
                 start_at='2026-10-28T16:00:00+00:00',end_at='2026-10-30T16:00:00+00:00',min_wins=0)
    now = datetime(2026,10,31,tzinfo=timezone.utc)
    path = tmp_path/'runs.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE runs(id TEXT PRIMARY KEY,hero TEXT,skills_json TEXT,stat_wins INTEGER,season INTEGER,phase TEXT,created_at TEXT)')
    api = SimpleNamespace(post_group_msg=AsyncMock(),post_private_msg=AsyncMock())
    api.post_group_msg.return_value = api.post_private_msg.return_value = None
    plugin = plugin_module.BazaarPlugin()
    plugin.api = api
    monkeypatch.setattr(cards,'OUTPUT',tmp_path/'images')
    return SimpleNamespace(module=plugin_module,stats=stats,service=service,cards=cards,names=names,
                           event=event,now=now,path=path,tmp=tmp_path,api=api,plugin=plugin,
                           event_obj=SimpleNamespace(message_type='group',group_id=123,user_id=456),
                           ids={hero:sid for sid,hero in stats.SKILL_HERO.items()})


def add(f, skills, wins=10, **overrides):
    values = dict(hero='Dooley',skills_json=json.dumps(skills),stat_wins=wins,
                  season=19,phase='19.4',created_at='2026-10-29T08:00:00Z')
    values.update(overrides)
    with sqlite3.connect(f.path) as db:
        ident = str(db.execute('SELECT COUNT(*) FROM runs').fetchone()[0])
        db.execute('INSERT INTO runs VALUES (?,?,?,?,?,?,?)',(ident,*values.values()))


def query(f):
    return f.stats.query_stats(f.names,f.path,event=f.event,now=f.now)


def dooley(result):
    return next(r for r in result['heroes'] if r['hero']=='Dooley')


def test_real_sql_scope_denominators_zero_win_missing_conflicting_and_dedup(f):
    for wins in (10,9,0):
        add(f,[{'cardId':f.ids['Mak']}],wins)
    add(f,[f.ids['Vanessa']],11)
    add(f,[{'cardId':f.ids['Stelle']},{'cardId':f.ids['Stelle']}])
    add(f,[{'skillId':f.ids['Karnok']}],3)
    for raw in (None,'not-json','{}',json.dumps([{'cardId':'ordinary-skill'},None,2])):
        add(f,[],skills_json=raw)
    add(f,[f.ids['Mak'],f.ids['Stelle']])
    add(f,[f.ids['Dooley']])
    for changes in (dict(season=18),dict(phase='19.3'),dict(phase='19.5'),
                    dict(created_at='2026-10-28T15:59:59Z'),dict(created_at=f.event['end_at']),
                    dict(created_at='not-a-time'),dict(created_at=None),dict(hero='Common')):
        add(f,[f.ids['Mak']],**changes)
    for wins in (None,-1,'unknown'):
        add(f,[f.ids['Mak']],wins)
    before = hashlib.sha256(f.path.read_bytes()).hexdigest()
    result = query(f)
    record = dooley(result)
    assert {k:record[k] for k in ('total','valid','wins','missing','conflict')} == dict(total=12,valid=6,wins=3,missing=4,conflict=2)
    mak = next(r for r in record['rows'] if r['hero']=='Mak')
    assert (mak['total'],mak['wins']) == (3,1)
    assert result['ended'] and len(result['heroes'])==8
    assert sum(r['valid'] for r in result['heroes'])==6
    assert hashlib.sha256(f.path.read_bytes()).hexdigest()==before
    doc = f.stats.build_document(result,hero='Dooley')
    assert dooley(doc)['rows'][0]['pick_rate']==.5
    assert dooley(doc)['rows'][0]['win_rate']==1/3
    f.event['min_wins'] = 1
    assert (dooley(query(f))['total'],dooley(query(f))['valid'])==(11,5)


def test_event_timestamp_offsets_and_future_data_excluded_before_current_time(f):
    f.event['end_at'] = None
    f.now = datetime(2026,10,29,tzinfo=timezone.utc)
    add(f,[f.ids['Mak']],created_at='2026-10-29T00:00:00+08:00')  # exact inclusive start
    add(f,[f.ids['Mak']],created_at='2026-10-29T08:00:00+08:00')  # exact exclusive now
    add(f,[f.ids['Mak']],created_at='2026-11-01T00:00:00Z')
    assert dooley(query(f))['valid']==1
    assert not query(f)['ended']


def test_query_missing_database_never_creates_an_empty_file(f):
    path = f.tmp/'missing.db'
    with pytest.raises(sqlite3.OperationalError):
        f.stats.query_stats(f.names,path,event=f.event,now=f.now)
    assert not path.exists()


def test_event_has_confirmed_beijing_end_and_supports_independent_history_source(f,monkeypatch):
    config = f.stats.load_event()
    assert f.stats.aware_time(config['end_at']).isoformat()=='2026-11-04T16:00:00+00:00'
    assert config['phases']==['19.4'] and config['min_wins']==1
    add(f,[f.ids['Mak']],1)
    monkeypatch.setenv('BAZAAR_SECONDARY_RUNS_DB_PATH',str(f.path))
    monkeypatch.setenv('RUNS_DB_PATH',str(f.tmp/'nonexistent-current-phase.db'))
    result = f.stats.query_stats(f.names,event=f.event,now=f.now)
    assert dooley(result)['valid']==1
    assert not (f.tmp/'nonexistent-current-phase.db').exists()


def test_empty_heroes_and_zero_sample_rates_are_not_invented(f):
    doc = f.stats.build_document(query(f),hero='Dooley')
    assert len(doc['heroes'])==8 and len(dooley(doc)['rows'])==7
    assert all(r['win_rate'] is None and r['pick_rate'] is None for r in dooley(doc)['rows'])
    assert '0.0%' not in f.stats.format_report(doc)
    add(f,[f.ids['Mak']],0)
    doc = f.stats.build_document(query(f),hero='Dooley')
    assert dooley(doc)['rows'][0]['win_rate']==0
    assert '0.0%' in f.stats.format_report(doc)


def test_win_order_uses_exact_fraction_ties_use_counts_and_zero_sample_last(f):
    snapshot = query(f)
    r = dooley(snapshot)
    r.update(total=10000,valid=10000,wins=5000)
    for row in r['rows']:
        if row['hero']=='Mak': row.update(total=2001,wins=1000)
        if row['hero']=='Stelle': row.update(total=2000,wins=1000)
        if row['hero']=='Vanessa': row.update(total=4000,wins=2000)
    pick = dooley(f.stats.build_document(snapshot,hero='Dooley'))['rows']
    win = dooley(f.stats.build_document(snapshot,hero='Dooley',order='win'))['rows']
    assert [r['hero'] for r in pick[:3]]==['Vanessa','Mak','Stelle']
    assert [r['hero'] for r in win[:3]]==['Vanessa','Stelle','Mak']
    assert all(r['total']==0 for r in win[3:])
    assert all('win_rate' not in r for r in dooley(snapshot)['rows'])


@pytest.mark.parametrize('arg,hero,order',[
    ('',None,'pick'),('胜率',None,'win'),('全部 10胜率',None,'win'),
    ('杜利','Dooley','pick'),('工程师 胜率','Dooley','win'),
    ('dOoLeY 选取率','Dooley','pick'),('The Dragons 胜率','The Dragons','win'),
])
def test_parser_accepts_existing_names_aliases_and_two_sort_modes(f,arg,hero,order):
    assert f.stats.parse_args(arg,f.names,f.service.HERO_ZH_TO_EN)==(hero,order)


@pytest.mark.parametrize('arg',['杜利 unknown','--days 3','杜利 胜率 extra','卡诺克的暗恋对象'])
def test_parser_rejects_unsupported_filters_instead_of_silently_broadening_scope(f,arg):
    with pytest.raises(ValueError,match='用法'):
        f.stats.parse_args(arg,f.names,f.service.HERO_ZH_TO_EN)


@pytest.mark.parametrize('change',[
    {'start_at':'2026-10-29T00:00:00'}, {'end_at':'2026-10-27T00:00:00Z'},
    {'phases':[]}, {'min_wins':True}, {'min_wins':2}, {'season':'19'},
])
def test_invalid_event_config_does_not_fall_back_to_current_season(f,change):
    path = f.tmp/'event.json'
    path.write_text(json.dumps(dict(f.event,**change)),encoding='utf-8')
    with pytest.raises(ValueError):
        f.stats.load_event(path)


@pytest.mark.parametrize('hero,height',[('Dooley',1660),(None,2080)])
def test_actual_pngs_include_counts_coverage_exclusions_and_no_clipped_text(f,monkeypatch,hero,height):
    add(f,[f.ids['Mak']],10)
    add(f,[f.ids['Mak']],0)
    add(f,[],2)
    doc = f.stats.build_document(query(f),hero=hero,preview=True)
    labels = []
    original = ImageDraw.ImageDraw.text
    def capture(self, xy, text, *args, **kwargs):
        labels.append(str(text))
        box = self.textbbox(xy,text,*args,**{k:v for k,v in kwargs.items() if k in ('font','anchor')})
        assert box[0]>=0 and box[1]>=0 and box[2]<=self._image.width and box[3]<=self._image.height,(text,box)
        return original(self,xy,text,*args,**kwargs)
    monkeypatch.setattr(ImageDraw.ImageDraw,'text',capture)
    with Image.open(f.cards.render_report(doc)) as image:
        assert image.size==(1080,height)
        assert len(image.crop((120,535,212,622)).getcolors(30000))>100 if hero else True
    joined = ' '.join(labels)
    assert '活动已结束' in joined and '历史数据' in joined and '测试预览' in joined
    assert '66.7%' in joined and '50.0%' in joined
    assert '未识别 1场' in joined and '小样本' in joined and '含0胜' in joined


def test_missing_art_and_empty_data_still_render(f,monkeypatch):
    monkeypatch.setattr(f.cards,'ASSETS',f.tmp/'no-assets')
    for hero in ('Dooley',None):
        with Image.open(f.cards.render_report(f.stats.build_document(query(f),hero=hero))) as image:
            assert image.width==1080


def prepare_service(f,monkeypatch):
    snapshot = query(f)
    q = Mock(return_value=snapshot)
    monkeypatch.setattr(f.stats,'load_event',Mock(return_value=f.event))
    monkeypatch.setattr(f.stats,'query_stats',q)
    return q


@pytest.mark.parametrize('kind',['group','private'])
def test_command_dispatch_sends_png_to_correct_destination_and_reuses_cached_query(f,monkeypatch,kind):
    add(f,[f.ids['Mak']],5)
    q = prepare_service(f,monkeypatch)
    f.event_obj.message_type = kind
    async def run():
        await asyncio.gather(*(f.plugin._dispatch('副角色','杜利',f.event_obj) for _ in range(2)))
        assert await f.plugin._dispatch('secondary','杜利 胜率',f.event_obj) is None
    asyncio.run(run())
    assert q.call_count==1
    api = f.api.post_group_msg if kind=='group' else f.api.post_private_msg
    other = f.api.post_private_msg if kind=='group' else f.api.post_group_msg
    assert api.await_count==3
    payload = api.await_args.kwargs
    assert payload['group_id' if kind=='group' else 'user_id']==(123 if kind=='group' else 456)
    with Image.open(io.BytesIO(base64.b64decode(payload['image'][9:]))) as image:
        assert image.size==(1080,1660)
    other.assert_not_awaited()
    assert not list(f.cards.OUTPUT.glob('*.png'))  # uploaded bytes cached, no image-file leak


def test_config_change_invalidates_statistics_and_images(f,monkeypatch):
    q = prepare_service(f,monkeypatch)
    async def run():
        await f.plugin._dispatch('副角色','杜利',f.event_obj)
        f.event['min_wins'] = 1
        await f.plugin._dispatch('副角色','杜利',f.event_obj)
    asyncio.run(run())
    assert q.call_count==2


@pytest.mark.parametrize('failure',['render','query','rejected','uncertain'])
def test_failures_never_invent_empty_statistics_or_double_send(f,monkeypatch,failure):
    add(f,[f.ids['Mak']],10)
    q = prepare_service(f,monkeypatch)
    if failure=='render':
        monkeypatch.setattr(f.cards,'render_report',Mock(side_effect=OSError('font')))
    elif failure=='query':
        q.side_effect = sqlite3.OperationalError('locked')
    elif failure=='rejected':
        f.api.post_group_msg.return_value = {'retcode':1200,'status':'failed'}
    else:
        f.api.post_group_msg.side_effect = TimeoutError('unknown delivery')
    result = asyncio.run(f.plugin._dispatch('副角色','杜利',f.event_obj))
    if failure=='render':
        assert result is None and '选取率 100.0%' in f.api.post_group_msg.await_args.kwargs['text']
    elif failure=='query':
        assert '查询失败' in result
        f.api.post_group_msg.assert_not_awaited()
    elif failure=='rejected':
        assert '选取率 100.0%' in result
    else:
        assert '待确认' in result
    assert f.api.post_group_msg.await_count<=1


def test_bad_arguments_do_not_query_database_and_help_lists_independent_command(f,monkeypatch):
    q = prepare_service(f,monkeypatch)
    assert '用法' in asyncio.run(f.plugin._dispatch('副角色','bad hero',f.event_obj))
    q.assert_not_called()
    help_text = asyncio.run(f.plugin._dispatch('help','',f.event_obj))
    assert '#bz 副角色' in help_text and '赛季末活动' in help_text
