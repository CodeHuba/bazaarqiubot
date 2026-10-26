import asyncio
from datetime import datetime
import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image, ImageDraw

from test_bz_db_command import load_plugin_module


@pytest.fixture
def setup_winrate(monkeypatch, tmp_path):
    plugin_module = load_plugin_module(monkeypatch)
    renderer = importlib.import_module('plugins.bazaar_plugin.winrate_image')
    queries = importlib.import_module('plugins.bazaar_plugin.runs_query')
    result = dict(card_names=['原子钟'], card_details=[dict(name='原子钟', name_en='Atomic Clock',
                  img='https://s.bazaardb.gg/v1/test@400.webp', size='Medium')],
                  total=431, ten_win=273, rate=273/431, not_found=[])
    monkeypatch.setattr(renderer, 'OUTPUT', tmp_path)
    monkeypatch.setattr(renderer, 'load_art', lambda *a, **k: Image.new('RGBA', (400, 400), '#cd863b'))
    calls = []
    connections = []

    class Query:
        def __init__(self):
            self.conn = SimpleNamespace(close=lambda: connections.append('closed'))

        def winrate(self, **kwargs):
            calls.append(kwargs)
            return result

    monkeypatch.setattr(queries, 'RunsQuery', Query)
    plugin = plugin_module.BazaarPlugin()
    plugin.api = SimpleNamespace(post_group_msg=AsyncMock(), post_private_msg=AsyncMock())
    event = SimpleNamespace(message_type='group', group_id=123, user_id=456)
    return plugin_module, renderer, result, plugin, event, calls, connections


def test_single_card_keeps_accepted_layout_and_only_requested_information(setup_winrate, monkeypatch):
    _, renderer, result, _, _, _, _ = setup_winrate
    labels = []
    original = ImageDraw.ImageDraw.text

    def capture(self, xy, text, *args, **kwargs):
        labels.append(str(text))
        return original(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, 'text', capture)
    path = renderer.render_winrate(result, queried_at=datetime(2026, 10, 27, 1, 2))
    with Image.open(path) as image:
        assert image.size == (1080, 1200)
        assert image.getpixel((540, 400)) == (205, 134, 59)
    assert {'原子钟', '63.3', '431', '273'} <= set(labels)
    assert not any(word in ' '.join(labels) for word in (
        'Atomic Clock', '中型物品', '当前阶段', '全部英雄', '全部段位', '效果预览'))


@pytest.mark.parametrize('total,wins,expected', [(0, 0, '暂无数据'), (10, 0, '0.0'), (10, 10, '100.0')])
def test_empty_and_extreme_rates_render_without_division_or_geometry_errors(setup_winrate, monkeypatch, total, wins, expected):
    _, renderer, result, _, _, _, _ = setup_winrate
    labels = []
    original = ImageDraw.ImageDraw.text

    def capture(self, xy, text, *args, **kwargs):
        labels.append(str(text))
        return original(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, 'text', capture)
    result.update(total=total, ten_win=wins, rate=.75)  # Counts remain the source of truth.
    path = renderer.render_winrate(result)
    assert expected in labels
    with Image.open(path) as image:
        assert image.size == (1080, 1200)


def test_native_size_combinations_and_missing_art_remain_readable(setup_winrate, monkeypatch):
    _, renderer, result, _, _, _, _ = setup_winrate
    seen = []

    def art(url, size, **kwargs):
        seen.append(size)
        return None if size == 'Small' else Image.new('RGBA', (600, 400), 'orange')

    monkeypatch.setattr(renderer, 'load_art', art)
    result['card_names'] = ['原子钟', '船锚', '蓝宝石', '原子钟', '船锚']
    result['card_details'] = [dict(img='url', size=size) for size in ('Medium', 'Large', 'Small', 'Medium', 'Large')]
    with Image.open(renderer.render_winrate(result)) as image:
        assert image.width == 1080
        assert image.height > 1200
    assert seen == ['Medium', 'Large', 'Small', 'Medium', 'Large']


@pytest.mark.parametrize('kind', ['group', 'private'])
def test_dispatch_sends_actual_image_bytes_to_matching_destination(setup_winrate, kind):
    import base64
    import io
    _, _, _, plugin, event, calls, connections = setup_winrate
    event.message_type = kind
    assert asyncio.run(plugin._dispatch('winrate', '原子钟', event)) is None
    api = plugin.api.post_private_msg if kind == 'private' else plugin.api.post_group_msg
    other = plugin.api.post_group_msg if kind == 'private' else plugin.api.post_private_msg
    kwargs = api.await_args.kwargs
    assert kwargs['user_id' if kind == 'private' else 'group_id'] == (456 if kind == 'private' else 123)
    assert kwargs['image'].startswith('base64://')
    with Image.open(io.BytesIO(base64.b64decode(kwargs['image'][9:]))) as image:
        assert image.size == (1080, 1200)
    other.assert_not_called()
    assert calls[0]['cards'] == ['原子钟'] and connections == ['closed']


@pytest.mark.parametrize('failure', ['render', 'upload', 'file'])
def test_image_failures_preserve_full_text_result(setup_winrate, monkeypatch, failure):
    module, renderer, _, plugin, event, _, _ = setup_winrate
    if failure == 'render':
        monkeypatch.setattr(renderer, 'render_winrate', lambda *a, **k: (_ for _ in ()).throw(OSError('font failure')))
    elif failure == 'upload':
        plugin.api.post_group_msg.side_effect = RuntimeError('upload rejected')
    else:
        monkeypatch.setattr(module, '_image_upload_value', lambda path: None)
    result = asyncio.run(plugin._cmd_winrate('原子钟', event))
    assert '63.3%' in result and '273 / 431' in result


@pytest.mark.parametrize('options', ['--hero Dooley --days 7 --legendary', '--days 7 --legendary --hero Dooley'])
def test_filter_combinations_are_removed_without_corrupting_card_query(setup_winrate, options):
    _, _, _, plugin, event, calls, _ = setup_winrate
    asyncio.run(plugin._cmd_winrate('原子钟 '+options, event))
    assert calls == [dict(cards=['原子钟'], hero='Dooley', days=7, rank_filter='legendary')]


def test_unknown_card_never_sends_partial_combination_image(setup_winrate):
    _, _, result, plugin, event, _, _ = setup_winrate
    result['not_found'] = ['不存在的卡']
    text = asyncio.run(plugin._cmd_winrate('原子钟 不存在的卡', event))
    assert '找不到卡牌' in text
    plugin.api.post_group_msg.assert_not_called()


def test_old_text_caller_remains_supported(setup_winrate):
    _, _, _, plugin, _, _, connections = setup_winrate
    text = asyncio.run(plugin._cmd_winrate('原子钟'))
    assert '63.3%' in text and connections == ['closed']
