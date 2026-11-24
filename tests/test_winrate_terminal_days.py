import importlib
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

from test_bz_db_command import load_plugin_module


@pytest.fixture
def setup(monkeypatch, tmp_path):
    load_plugin_module(monkeypatch)
    query = importlib.import_module('plugins.bazaar_plugin.runs_query')
    stats = importlib.import_module('plugins.bazaar_plugin.winrate_stats')
    stats.CACHE.clear()
    path = tmp_path/'runs.db'
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('CREATE TABLE phases(season,phase,start_time)')
    conn.execute('INSERT INTO phases VALUES(?,?,?)', (query.RUNS_SEASON_ID, query.CURRENT_PHASE, '2026-11-01T00:00:00Z'))
    conn.execute('CREATE TABLE runs(id TEXT PRIMARY KEY,card_ids_text,stat_wins,combats_json,season,phase,hero,player_rank,created_at)')
    def insert(rid, ids='a', wins=10, combats=None, **kw):
        raw = json.dumps(combats if combats is not None else [{'d': 10}])
        conn.execute('INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?)',
                     (rid, ids, wins, raw, kw.get('season', query.RUNS_SEASON_ID),
                      kw.get('phase', query.CURRENT_PHASE), kw.get('hero', 'Vanessa'),
                      kw.get('rank', 'Legendary'), kw.get('created_at', '2090-01-01T00:00:00')))
        conn.commit()
    obj = SimpleNamespace(conn=conn, db_path=str(path),
        find_card_ids=lambda name: {'a':['a','a-variant'], 'b':['b']}.get(name, []),
        card_display_info=lambda cid: dict(name=cid, img='', size='Medium'))
    def run(cards=None, **kwargs):
        return query.RunsQuery.winrate(obj, cards or ['a'], include_terminal_days=True, **kwargs)
    yield SimpleNamespace(run=run, insert=insert, obj=obj, conn=conn, stats=stats, query=query)
    conn.close()


def test_distribution_unknown_and_overall_reconcile(setup):
    setup.insert('one', wins=10, combats=[{'d': 1}, {'d': 11}, {'d': 9}])
    setup.insert('two', wins=8, combats=[{'d': 11}])
    setup.insert('three', wins=10, combats=[{'d': 14}])
    setup.insert('unknown', wins=10, combats=[{'d': True}, {'d': -1}, {'d': '12'}])
    setup.insert('unrelated', ids='b', combats=[{'d': 16}])
    setup.insert('early', wins=0, combats=[{'d': 9}])
    setup.insert('first_valid', wins=10, combats=[{'d': 10}])
    result = setup.run()
    assert (result['total'],result['ten_win']) == (6,4)
    assert result['day_distribution'] == [dict(day=10,total=1,ten_win=1,rate=1),
                                         dict(day=11,total=2,ten_win=1,rate=.5),
                                         dict(day=14,total=1,ten_win=1,rate=1)]
    assert result['unknown_day'] == dict(total=1,ten_win=1,rate=1)


@pytest.mark.parametrize('raw', ['{broken', 'null', '{}', 'true', '[null,1,"x",{}, {"d":true}]'])
def test_invalid_combats_are_unknown_without_losing_overall(setup, raw):
    setup.insert('one')
    setup.conn.execute('UPDATE runs SET combats_json=?', (raw,)); setup.conn.commit()
    result = setup.run()
    assert result['total'] == 1 and result['unknown_day']['total'] == 1


def test_only_matching_runs_parse_combats_and_secondary_ids_work(setup, monkeypatch):
    setup.insert('joint', ids='a-variant b')
    setup.insert('single', ids='a')
    setup.insert('unrelated', ids='b')
    calls=[]; original=setup.stats.terminal_day
    monkeypatch.setattr(setup.stats, 'terminal_day', lambda raw: calls.append(raw) or original(raw))
    result=setup.run(['b','a'])
    assert result['total']==1 and len(calls)==1


def test_existing_card_index_matches_scan_results_for_a_combination(setup):
    setup.insert('joint', ids='a-variant b')
    setup.insert('single', ids='a')
    setup.insert('unrelated', ids='b')
    scan = setup.run(['a', 'b'])
    setup.conn.execute('CREATE TABLE runs_query_cards_ready_v1(version INTEGER)')
    setup.conn.execute('CREATE TABLE runs_query_cards_v1(card_id TEXT, enchantment TEXT, run_id TEXT)')
    setup.conn.executemany('INSERT INTO runs_query_cards_v1 VALUES(?,?,?)', [
        ('a-variant', '', 'joint'), ('b', '', 'joint'), ('b', 'Heavy', 'joint'),
        ('a', '', 'single'), ('b', '', 'unrelated'),
    ])
    setup.conn.commit()
    indexed = setup.run(['a', 'b'])
    assert indexed['day_distribution'] == scan['day_distribution']
    assert indexed['total'] == scan['total'] == 1


def test_filters_and_threshold_apply_to_both_totals(setup):
    setup.insert('keep', wins=9)
    setup.insert('hero', hero='Dooley')
    setup.insert('rank', rank='Bronze')
    setup.insert('phase', phase='old')
    setup.insert('season', season=-1)
    setup.insert('old', created_at='2000-01-01T00:00:00')
    result=setup.run(hero='Vanessa',days=3,rank_filter='legendary',min_wins_threshold=9)
    assert result['total']==result['ten_win']==1
    assert result['day_distribution'][0]['ten_win']==1


def test_shared_cache_is_copy_safe_invalidates_on_wal_and_separates_filters(setup):
    setup.insert('one')
    first=setup.run(); first['day_distribution'][0]['total']=999
    statements=[]; setup.conn.set_trace_callback(statements.append)
    assert setup.run()['total']==1
    assert setup.run()['day_distribution'][0]['total']==1
    assert not any('FROM runs' in sql for sql in statements)
    setup.insert('two', wins=0)
    assert setup.run()['total']==2
    assert setup.run(hero='Dooley')['total']==0
    assert setup.run(['b'])['total']==0


def test_cached_time_bounds_remain_the_snapshot_bounds(setup):
    setup.insert('one')
    assert setup.run(days=1)['scope']==setup.run(days=1)['scope']


def test_concurrent_identical_requests_compute_once_and_failures_retry(setup, monkeypatch):
    setup.insert('one')
    original=setup.stats._compute
    entered, release=threading.Event(),threading.Event()
    calls=[]
    def compute(*args):
        calls.append(1); entered.set(); assert release.wait(5)
        return original(*args)
    monkeypatch.setattr(setup.stats,'_compute',compute)
    # Call the coordinator directly so no unrelated phase query shares a connection.
    def run(): return setup.stats.aggregate(setup.obj,
        'SELECT id,card_ids_text,stat_wins FROM runs', [], [{'a'}],10,{},('test',))
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures=[pool.submit(run) for _ in range(4)]
        assert entered.wait(5); release.set()
        assert [f.result()['total'] for f in futures]==[1]*4
    assert len(calls)==1
    setup.stats.CACHE.clear()
    monkeypatch.setattr(setup.stats,'_compute',lambda *a: (_ for _ in ()).throw(RuntimeError('fail')))
    with pytest.raises(RuntimeError,match='fail'): run()
    monkeypatch.setattr(setup.stats,'_compute',original)
    assert run()['total']==1


@pytest.fixture
def renderer(monkeypatch,tmp_path):
    load_plugin_module(monkeypatch)
    module=importlib.import_module('plugins.bazaar_plugin.winrate_image')
    monkeypatch.setattr(module,'OUTPUT',tmp_path)
    monkeypatch.setattr(module,'load_art',lambda *a,**k: Image.new('RGBA',(400,400),'orange'))
    data=dict(card_names=['大钢弩'],card_details=[dict(img='x',size='Medium')],total=108,ten_win=60,
              day_distribution=[dict(day=11,total=72,ten_win=40),dict(day=14,total=36,ten_win=20)],
              unknown_day=dict(total=0,ten_win=0))
    return module,data


def test_image_highlights_exact_ties_and_fills_zero_sample_days(renderer,monkeypatch):
    module,data=renderer
    data['total'] += 20  # Early endings remain in the overall sample only.
    labels=[];original=ImageDraw.ImageDraw.text
    def capture(self,xy,text,*a,**kw): labels.append(str(text)); return original(self,xy,text,*a,**kw)
    monkeypatch.setattr(ImageDraw.ImageDraw,'text',capture)
    path=module.render_winrate(data,preview=True)
    with Image.open(path) as im: assert im.width==1080 and im.height<6000
    assert '最高 10 胜率 · 并列' in labels and '第 11、14 天' in labels
    assert '第 12 天' in labels and '—' in labels
    assert '第 10 天' in labels and '第 9 天' not in labels
    assert '72 局 · 占有效样本 66.7%' in labels
    assert '第 11 天' in labels and '55.6%' in labels


@pytest.mark.parametrize('mode',['empty','unknown','combination','small','zero_wins','early_only'])
def test_report_handles_boundary_cases(renderer,mode):
    module,data=renderer
    if mode in ('empty','unknown'):
        data.update(total=0 if mode=='empty' else 108,ten_win=0 if mode=='empty' else 60,
                    day_distribution=[],unknown_day=dict(total=0 if mode=='empty' else 108,ten_win=0 if mode=='empty' else 60))
    if mode=='combination':
        data.update(card_names=['船骸','船锚','大钢弩','原子钟','图书馆'],card_details=[dict(size='Large')]*5)
    if mode=='small':
        data.update(total=1,ten_win=1,day_distribution=[dict(day=12,total=1,ten_win=1)])
    if mode=='zero_wins':
        data['ten_win']=0
        for row in data['day_distribution']:row['ten_win']=0
    if mode=='early_only':
        data.update(ten_win=0, day_distribution=[])
    with Image.open(module.render_winrate(data,preview=True)) as im:
        assert 1080==im.width and 1000<im.height<6000


def test_image_cache_reuses_file_and_rebuilds_if_deleted(renderer):
    module,data=renderer
    first=module.render_winrate(data)
    assert module.render_winrate(data)==first
    from pathlib import Path
    Path(first).unlink()
    assert module.render_winrate(data)!=first


def test_inconsistent_day_totals_are_rejected(renderer):
    module,data=renderer
    data['total']-=1
    with pytest.raises(ValueError,match='计数'): module.render_winrate(data,preview=True)


def test_text_fallback_uses_only_valid_days_for_distribution(renderer):
    _, data = renderer
    data.update(total=120, rate=0.5)
    formatter = importlib.import_module('plugins.bazaar_plugin.formatter')
    text = formatter.format_winrate(data)
    assert '10胜局: 60 / 120 局' in text
    assert '第 10 天起，有效样本 108 局' in text
    assert '第11天 | 72 / 66.7% | 40 / 55.6%' in text
