import importlib

from PIL import Image, ImageDraw
from test_bz_db_command import load_plugin_module


def test_scope_labels_follow_result_including_all_season_and_days(monkeypatch):
    load_plugin_module(monkeypatch)
    renderer=importlib.import_module('plugins.bazaar_plugin.winrate_image')
    assert renderer.scope_label(dict(season=20,phase='20.1',days=None))=='统计范围：S20 · 20.1赛段'
    assert renderer.scope_label(dict(season=20,phase='20.2',days=3))=='统计范围：S20 · 20.2赛段 · 最近3天'
    assert renderer.scope_label(dict(season=20,phase=None))=='统计范围：S20 全赛季'
    assert renderer.scope_label(None)==''


def test_phase_scope_is_visible_on_empty_results_too(monkeypatch,tmp_path):
    load_plugin_module(monkeypatch)
    renderer=importlib.import_module('plugins.bazaar_plugin.winrate_image')
    monkeypatch.setattr(renderer,'load_art',lambda *a,**k:Image.new('RGBA',(400,400),'orange'))
    labels=[];original=ImageDraw.ImageDraw.text
    def capture(self,xy,text,*args,**kwargs):
        labels.append(str(text))
        return original(self,xy,text,*args,**kwargs)
    monkeypatch.setattr(ImageDraw.ImageDraw,'text',capture)
    result=dict(card_names=['原子钟'],card_details=[dict(size='Medium',img='url')],total=0,ten_win=0,
                scope=dict(season=20,phase='20.1'))
    with Image.open(renderer.render_winrate(result,tmp_path)) as image:
        assert image.size==(1080,1260)
    assert '统计范围：S20 · 20.1赛段' in labels
    assert any('仅统计本赛段已收录对局' in label for label in labels)
    assert '暂无数据' in labels


def test_time_range_is_beijing_time_with_precise_boundaries(monkeypatch):
    load_plugin_module(monkeypatch)
    renderer=importlib.import_module('plugins.bazaar_plugin.winrate_image')
    scope=dict(start_at='2026-11-06T22:00:00+00:00',end_at='2026-11-08T04:10:00Z')
    assert renderer.time_range_label(scope)=='数据时间：2026-11-07 06:00 — 2026-11-08 12:10（北京时间）'
    assert '起点未记录' in renderer.time_range_label(dict(end_at=scope['end_at']))
    assert renderer.time_range_label(dict(start_at='bad',end_at=scope['end_at']))==''


def test_both_scope_and_time_are_drawn_without_covering_art(monkeypatch,tmp_path):
    load_plugin_module(monkeypatch)
    renderer=importlib.import_module('plugins.bazaar_plugin.winrate_image')
    monkeypatch.setattr(renderer,'load_art',lambda *a,**k:Image.new('RGBA',(400,400),'orange'))
    result=dict(card_names=['原子钟'],card_details=[dict(size='Medium',img='url')],total=167,ten_win=100,
        scope=dict(season=20,phase='20.1',start_at='2026-11-06T22:00:00Z',end_at='2026-11-08T04:10:00Z'))
    with Image.open(renderer.render_winrate(result,tmp_path)) as image:
        assert image.size==(1080,1304)
        assert image.getpixel((540,450))==(255,165,0)
