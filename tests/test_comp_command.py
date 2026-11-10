import asyncio
from collections import defaultdict
import importlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import httpx
from PIL import Image, ImageDraw
import pytest

from test_bz_db_command import load_plugin_module, FakeEvent


def sample():
    anchor = dict(cardId='clock', name='原子钟', img='', size='Medium', type='Item')
    other = dict(cardId='core', name='武装核心', img='', size='Medium', type='Item')
    skill = dict(cardId='skill', name='灵感怒火', img='', size='Small', type='Skill', ten_win_frequency=.7)
    groups = [dict(id=str(count), count=count, ten_wins=wins, rate=wins/count,
                   lower_bound=.5, hero_name='杜利', core=[anchor, other],
                   winning_items=[dict(other, ten_win_frequency=.5)], winning_skills=[skill])
              for count, wins in [(72, 56), (138, 105), (90, 62)]]
    return dict(anchors=[anchor], baseline=dict(count=390, ten_wins=237, rate=237/390, average_wins=8.6),
                scope=dict(phase='20.1', hero='', rank='all', days=None, captured_at='2026-11-10T01:22:11+00:00'),
                groups=groups)


@pytest.fixture
def modules(monkeypatch):
    plugin = load_plugin_module(monkeypatch)
    command = importlib.import_module('plugins.bazaar_plugin.comp_command')
    renderer = importlib.import_module('plugins.bazaar_plugin.comp_image')
    return plugin, command, renderer


@pytest.mark.parametrize('arg,name,hero,days,rank', [
    ('原子钟', '原子钟', '', None, 'all'),
    ('Atomic Clock --hero 杜利 --days 7 --legendary', 'Atomic Clock', '杜利', 7, 'legendary'),
    ('--days 365 "Atomic Clock" --hero "The Dragons"', 'Atomic Clock', 'The Dragons', 365, 'all'),
])
def test_parse_single_complete_name_and_filters(modules, arg, name, hero, days, rank):
    assert modules[1].parse_args(arg) == dict(name=name, hero=hero, days=days, rank=rank)


@pytest.mark.parametrize('arg', ['', '原子钟+主板', '原子钟＋主板', '"原子钟',
    '原子钟 --days', '原子钟 --days 0', '原子钟 --days 366', '原子钟 --days -1',
    '原子钟 --days 七', '原子钟 --days 1.5', '原子钟 --hero', '原子钟 --invalid',
    '原子钟 --days 1 --days 2', '原子钟 --legendary --legendary'])
def test_invalid_args_never_query(modules, arg):
    with pytest.raises(modules[1].CompInputError):
        modules[1].parse_args(arg)


def mock_query(monkeypatch, command, response):
    monkeypatch.setattr(command, '_request_slot', lambda: None)
    queries = importlib.import_module('plugins.bazaar_plugin.runs_query')
    resolver = importlib.import_module('plugins.bazaar_plugin.winrate_command')
    closed, calls = [], []
    fake = SimpleNamespace(conn=SimpleNamespace(close=lambda: closed.append(True)), resolve_hero=lambda _: 'Dooley')
    monkeypatch.setattr(queries, 'RunsQuery', lambda: fake)
    monkeypatch.setattr(resolver, 'resolve_cards', lambda q, cards: ['Atomic Clock'])

    def get(url, **kwargs):
        calls.append((url, kwargs))
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(command.httpx, 'get', get)
    return closed, calls


def test_internal_api_uses_complete_filters_and_closes_connection(modules, monkeypatch):
    command = modules[1]
    response = httpx.Response(200, json=sample(), request=httpx.Request('GET', command.API_URL))
    closed, calls = mock_query(monkeypatch, command, response)
    data = command.query_comp(command.parse_args('原子钟 --hero 杜利 --days 7 --legendary'))
    assert data['baseline']['count'] == 390 and closed == [True]
    assert calls[0][1] == dict(params=dict(card='Atomic Clock', sort='appearance', rank='legendary', hero='Dooley', days=7), timeout=30, trust_env=False)


@pytest.mark.parametrize('status,message,error', [
    (400, '阵容分析仅支持物品筛选', 'CompInputError'),
    (404, '未找到物品', 'CompInputError'),
    (503, '正在预热', 'CompUnavailable'),
    (429, '繁忙', 'CompUnavailable'),
    (500, 'internal secret', 'CompUnavailable'),
])
def test_api_failures_are_actionable_without_internal_errors(modules, monkeypatch, status, message, error):
    command = modules[1]
    response = httpx.Response(status, json={'error': message}, request=httpx.Request('GET', command.API_URL))
    closed, _ = mock_query(monkeypatch, command, response)
    with pytest.raises(getattr(command, error)) as caught:
        command.query_comp(command.parse_args('原子钟'))
    assert 'internal secret' not in str(caught.value) and closed == [True]


def test_timeout_and_ambiguous_name_close_connections(modules, monkeypatch):
    command = modules[1]
    closed, calls = mock_query(monkeypatch, command, httpx.ReadTimeout('internal'))
    with pytest.raises(command.CompUnavailable, match='超时'):
        command.query_comp(command.parse_args('原子钟'))
    assert closed == [True]
    resolver = importlib.import_module('plugins.bazaar_plugin.winrate_command')
    monkeypatch.setattr(resolver, 'resolve_cards', lambda *a: (_ for _ in ()).throw(resolver.WinrateInputError('多个候选')))
    with pytest.raises(command.CompInputError, match='多个候选'):
        command.query_comp(command.parse_args('钟'))
    assert closed == [True, True] and len(calls) == 1


def test_group_order_small_samples_and_link_preserve_query(modules):
    command = modules[1]
    data = sample()
    assert [g['count'] for g in command.selected_groups(data)] == [138, 90, 72]
    data['scope'].update(hero='Dooley', days=7, rank='legendary')
    params = parse_qs(urlparse(command.website_link(data)).query)
    assert params['sort'] == ['appearance'] and params['hero'] == ['Dooley'] and params['days'] == ['7'] and params['rank'] == ['legendary']
    data['groups'] = [dict(data['groups'][0], count=n) for n in [9, 20, 10]]
    assert [g['count'] for g in command.selected_groups(data)] == [20, 10]
    data['groups'] = []
    assert '暂无达到10局' in command.format_text(data)
    data['baseline']['count'] = 0
    assert '暂无数据' in command.format_text(data) and '整体10胜率' not in command.format_text(data)


def test_image_keeps_accepted_metrics_spacing_and_single_watermark(modules, monkeypatch, tmp_path):
    renderer = modules[2]
    labels = []
    original = ImageDraw.ImageDraw.text

    def capture(self, xy, text, *args, **kwargs):
        labels.append((str(text), xy))
        return original(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, 'text', capture)
    monkeypatch.setattr(renderer, 'load_comp_art', lambda _: None)
    path = renderer.render_comp(sample(), tmp_path)
    assert Image.open(path).info['qiubot_watermark'] == 'bazaarqiubot.com|single-footer-v1'
    assert Image.open(path).size == (1080, 2442)
    assert not any(t in ('本地效果预览', '效果预览', '35.4%', '23.1%', '18.5%') for t, _ in labels)
    for value in ('138 局', '90 局', '72 局'):
        _, xy = next((t, xy) for t, xy in labels if t == value)
        lx = next(pos[0] for t, pos in labels if t == '出场局数' and pos[1] == xy[1]+20)
        assert lx-xy[0]-renderer.font(44, True).getlength(value) == pytest.approx(16, abs=1)


@pytest.mark.parametrize('channel', ['group', 'private'])
def test_command_delivers_image_to_correct_channel(modules, monkeypatch, tmp_path, channel):
    plugin_module, command, renderer = modules
    plugin = plugin_module.BazaarPlugin()
    plugin.api = SimpleNamespace(post_group_msg=AsyncMock(return_value={'retcode': 0}), post_private_msg=AsyncMock(return_value={'retcode': 0}))
    image = tmp_path/'test.png'; Image.new('RGB', (20, 20)).save(image)
    monkeypatch.setattr(command, 'query_comp', lambda _: sample())
    monkeypatch.setattr(renderer, 'render_comp', lambda _: str(image))
    event = SimpleNamespace(message_type=channel, user_id=123, group_id=456)
    assert asyncio.run(plugin._cmd_comp('原子钟', event)) is None
    api = plugin.api.post_private_msg if channel == 'private' else plugin.api.post_group_msg
    payload = api.call_args.kwargs
    assert payload['image'].startswith('base64://')
    assert 'text' not in payload and 'message' not in payload
    assert payload['user_id' if channel == 'private' else 'group_id'] == (123 if channel == 'private' else 456)


@pytest.mark.parametrize('failure', ['render', 'upload', 'reject'])
def test_image_failures_return_useful_text(modules, monkeypatch, tmp_path, failure):
    plugin_module, command, renderer = modules
    plugin = plugin_module.BazaarPlugin()
    plugin.api = SimpleNamespace(post_group_msg=AsyncMock(return_value={'retcode': 100}), post_private_msg=AsyncMock())
    monkeypatch.setattr(command, 'query_comp', lambda _: sample())
    image = tmp_path/'test.png'; Image.new('RGB', (20,20)).save(image)
    if failure == 'render':
        monkeypatch.setattr(renderer, 'render_comp', lambda _: (_ for _ in ()).throw(OSError('bad image')))
    else:
        monkeypatch.setattr(renderer, 'render_comp', lambda _: str(image) if failure == 'reject' else 'missing.png')
    result = asyncio.run(plugin._cmd_comp('原子钟', SimpleNamespace(message_type='group', group_id=456)))
    assert '出场 138 局' in result and '76.1%' in result
    assert '完整分析' not in result and 'https://' not in result


@pytest.mark.parametrize('alias', ['阵容', 'comp'])
def test_dispatch_and_help_alias(modules, alias):
    plugin_module = modules[0]
    plugin = plugin_module.BazaarPlugin(); plugin._cooldown = defaultdict(float)
    plugin._cmd_comp = AsyncMock(return_value='阵容分析结果')
    event = FakeEvent('#bz '+alias+' 原子钟')
    asyncio.run(plugin.handle(event))
    assert event.replies == ['阵容分析结果']
    assert '#bz 阵容 <物品名>' in plugin_module.fmt.format_help()


def test_busy_query_does_not_queue_or_hit_service(modules, monkeypatch):
    plugin_module, command, _ = modules
    plugin = plugin_module.BazaarPlugin(); plugin._comp_slots = asyncio.Semaphore(0)
    monkeypatch.setattr(command, 'query_comp', lambda _: pytest.fail('Busy query executed'))
    assert '繁忙' in asyncio.run(plugin._cmd_comp('原子钟'))
