import asyncio
from datetime import datetime
import importlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from test_bz_db_command import FakeEvent, load_plugin_module


@pytest.fixture
def setup_watch(monkeypatch, tmp_path):
    module = load_plugin_module(monkeypatch)
    service = importlib.import_module('plugins.bazaar_plugin.watch_service')
    monkeypatch.setattr(service.store, 'SUBS_FILE', tmp_path/'subscriptions.json')
    monkeypatch.setattr(service, 'OUTPUT', tmp_path/'images')
    plugin = module.BazaarPlugin()
    plugin.client = SimpleNamespace(get_player_stat=AsyncMock(return_value={'ratingHistory': [
        dict(timestamp='2026-10-25 09:55:00', rating=938, position=1140),
        dict(timestamp='2026-10-26 09:55:00', rating=967, position=863),
    ]}), get_player=AsyncMock(return_value={}))
    plugin.api = SimpleNamespace(post_group_msg=AsyncMock(), post_private_msg=AsyncMock())
    event = FakeEvent()
    event.message_type, event.group_id = 'group', 123
    return module, service, plugin, event, tmp_path


def test_subscription_command_uses_image_and_case_insensitive_identity(setup_watch):
    _, service, plugin, event, _ = setup_watch
    async def run():
        assert await plugin._dispatch('watch', 'Koucha_', event) is None
        assert await plugin._dispatch('watch', 'KOUCHA_', event) is None
        assert service.store.get_subscriptions('group:123') == ['Koucha_']
        assert await plugin._dispatch('watchlist', '', event) is None
        assert await plugin._dispatch('unwatch', 'koucha_', event) is None
        assert service.store.get_subscriptions('group:123') == []
    asyncio.run(run())
    assert plugin.api.post_group_msg.await_count == 4
    assert all(call.kwargs['image'].startswith('base64://') for call in plugin.api.post_group_msg.await_args_list)


def test_json_migration_preserves_original_and_does_not_resurrect_removals(setup_watch):
    _, service, _, _, tmp = setup_watch
    source = tmp/'subscriptions.json'
    original = json.dumps({'group:123': ['koucha_', 'hyx499']})
    source.write_text(original, encoding='utf-8')
    assert service.store.get_subscriptions('group:123') == ['koucha_', 'hyx499']
    assert service.store.remove_subscription('group:123', 'Koucha_')
    assert service.store.get_subscriptions('group:123') == ['hyx499']
    assert source.read_text(encoding='utf-8') == original


def test_broken_json_does_not_silently_reset_subscriptions(setup_watch):
    _, service, _, _, tmp = setup_watch
    (tmp/'subscriptions.json').write_text('{broken', encoding='utf-8')
    with pytest.raises(json.JSONDecodeError):
        service.store.add_subscription('group:123', 'Koucha_')


def test_daily_deduplicates_across_groups_and_repeated_runs(setup_watch, monkeypatch):
    _, service, plugin, _, _ = setup_watch
    async def no_sleep(_):
        pass
    monkeypatch.setattr(service.asyncio, 'sleep', no_sleep)
    service.store.add_subscription('group:123', 'Koucha_')
    service.store.add_subscription('group:456', 'KOUCHA_')
    now = datetime(2026, 10, 26, 10, 1, tzinfo=service.cards.CST)
    async def run():
        watch = plugin._watch_service()
        await watch.daily(now)
        await watch.daily(now)
    asyncio.run(run())
    assert plugin.client.get_player_stat.await_count == 1
    assert plugin.api.post_group_msg.await_count == 2
    assert all(row[3] == 'sent' for row in service.store.delivery_pages('group:123', '20:2026-10-26'))


def test_failed_page_retries_without_resending_completed_pages(setup_watch, monkeypatch):
    _, service, plugin, _, tmp = setup_watch
    async def no_sleep(_):
        pass
    monkeypatch.setattr(service.asyncio, 'sleep', no_sleep)
    service.store.add_subscription('group:123', 'Koucha_')
    paths = [str(tmp/f'{i}.png') for i in (1, 2)]
    for path in paths:
        Path(path).write_bytes(b'image')
    service.store.prepare_delivery('group:123', '20:2026-10-26', paths, 'fallback')
    plugin.api.post_group_msg.side_effect = [None, RuntimeError('offline'), None]
    now = datetime(2026, 10, 26, 10, 1, tzinfo=service.cards.CST)
    async def run():
        watch = plugin._watch_service()
        await watch.daily(now)
        rows = service.store.delivery_pages('group:123', '20:2026-10-26')
        assert [r[3] for r in rows] == ['sent', 'failed']
        monkeypatch.setattr(service.time, 'time', lambda: rows[1][5]+61)
        # Simulate a restart with a fresh service; resume the persisted second page.
        restarted = service.WatchService(plugin.client, plugin.api)
        await restarted.daily(now)
    asyncio.run(run())
    assert plugin.api.post_group_msg.await_count == 3
    assert plugin.client.get_player_stat.await_count == 0
    assert [r[3] for r in service.store.delivery_pages('group:123', '20:2026-10-26')] == ['sent', 'sent']


def test_manual_preview_does_not_mark_scheduled_report_sent(setup_watch, monkeypatch):
    module, service, plugin, event, _ = setup_watch
    service.store.add_subscription('group:123', 'Hyx499')
    monkeypatch.setattr(module, 'ADMIN_QQ', str(event.user_id))
    asyncio.run(plugin._dispatch('testpush', '', event))
    assert plugin.api.post_group_msg.await_count == 1
    assert service.store.delivery_pages('group:123', '20:2026-10-26') == []


def test_private_subscription_replies_with_image_without_writing(setup_watch):
    _, service, plugin, event, _ = setup_watch
    event.message_type = 'private'
    asyncio.run(plugin._dispatch('watch', 'Koucha_', event))
    assert plugin.api.post_private_msg.await_count == 1
    assert service.store.get_all_subscriptions() == {}


def test_query_failure_does_not_prevent_other_players_rendering(setup_watch):
    _, service, plugin, _, _ = setup_watch
    data = plugin.client.get_player_stat.return_value
    plugin.client.get_player_stat.side_effect = [RuntimeError('down'), data]
    paths, text = asyncio.run(plugin._watch_service().report(['Koucha_', 'Hyx499'], datetime(2026, 10, 26, 10, 1, tzinfo=service.cards.CST)))
    assert len(paths) == 2
    assert 'Koucha_: 暂时无法更新' in text
    assert 'Hyx499: 967 分' in text
    assert all(Image.open(path).size == (1080, 1305) for path in paths)


def test_detailed_report_paginates_three_players(setup_watch):
    _, service, plugin, _, _ = setup_watch
    paths, _ = asyncio.run(plugin._watch_service().report(['a', 'b', 'c'], datetime(2026, 10, 26, 10, 1, tzinfo=service.cards.CST)))
    assert len(paths) == 3
    assert all(Image.open(path).size == (1080, 1305) for path in paths)


def test_sent_images_keep_identical_dimensions(setup_watch):
    import base64
    import io
    _, service, plugin, _, _ = setup_watch
    async def run():
        watch = plugin._watch_service()
        paths, text = await watch.report(['Koucha_', 'Hyx499'], datetime(2026, 10, 26, 10, 1, tzinfo=service.cards.CST))
        await watch.send(123, paths, text)
    asyncio.run(run())
    assert plugin.api.post_group_msg.await_count == 2
    sizes = [Image.open(io.BytesIO(base64.b64decode(call.kwargs['image'][9:]))).size
             for call in plugin.api.post_group_msg.await_args_list]
    assert sizes == [(1080, 1305), (1080, 1305)]


def test_daily_missing_image_fallback_matches_each_player_page(setup_watch, monkeypatch, tmp_path):
    _, service, plugin, _, _ = setup_watch
    for name in ['alpha', 'beta', 'gamma']:
        service.store.add_subscription('group:123', name)
    monkeypatch.setattr(service.cards, 'render_pages', lambda *a, **k: [str(tmp_path/f'missing-{i}.png') for i in range(3)])
    async def no_sleep(_):
        pass
    monkeypatch.setattr(service.asyncio, 'sleep', no_sleep)
    asyncio.run(plugin._watch_service().daily(datetime(2026, 10, 26, 10, 1, tzinfo=service.cards.CST)))
    messages = plugin.api.post_group_msg.await_args_list
    assert len(messages) == 3
    for call, expected in zip(messages, ['alpha', 'beta', 'gamma']):
        assert expected+':' in call.kwargs['text']
        assert sum(name+':' in call.kwargs['text'] for name in ['alpha', 'beta', 'gamma']) == 1


def test_scheduler_is_quiet_outside_china_ten_oclock(setup_watch):
    _, service, plugin, _, _ = setup_watch
    service.store.add_subscription('group:123', 'Koucha_')
    asyncio.run(plugin._watch_service().daily(datetime(2026, 10, 26, 9, 59, tzinfo=service.cards.CST)))
    plugin.client.get_player_stat.assert_not_awaited()
    plugin.api.post_group_msg.assert_not_awaited()


def test_uncertain_send_is_not_automatically_duplicated(setup_watch, monkeypatch):
    _, service, plugin, _, tmp = setup_watch
    service.store.add_subscription('group:123', 'Koucha_')
    service.store.prepare_delivery('group:123', '20:2026-10-26', [str(tmp/'page.png')], 'text')
    assert service.store.claim_delivery('group:123', '20:2026-10-26', 1, 1)
    assert not service.store.claim_delivery('group:123', '20:2026-10-26', 1, 9999)
    asyncio.run(plugin._watch_service().daily(datetime(2026, 10, 26, 10, 5, tzinfo=service.cards.CST)))
    plugin.api.post_group_msg.assert_not_awaited()


def test_failure_retry_limit_and_backoff_survive_reconnect(setup_watch):
    _, service, _, _, _ = setup_watch
    service.store.prepare_delivery('group:123', '20:2026-10-26', ['path'], 'text')
    for attempt in range(3):
        now = 100+attempt*61
        assert service.store.claim_delivery('group:123', '20:2026-10-26', 1, now)
        service.store.finish_delivery('group:123', '20:2026-10-26', 1, now, False)
        assert not service.store.claim_delivery('group:123', '20:2026-10-26', 1, now+1)
    assert not service.store.claim_delivery('group:123', '20:2026-10-26', 1, 10000)


def test_concurrent_subscriptions_are_not_lost(setup_watch):
    from concurrent.futures import ThreadPoolExecutor
    _, service, _, _, _ = setup_watch
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda i: service.store.add_subscription('group:123', f'player{i}'), range(12)))
    assert all(results)
    assert len(service.store.get_subscriptions('group:123')) == 12


def test_image_render_failure_still_delivers_bounded_text(setup_watch, monkeypatch):
    _, service, plugin, _, _ = setup_watch
    monkeypatch.setattr(service.cards, 'render_pages', lambda *a, **k: (_ for _ in ()).throw(OSError('font unavailable')))
    async def run():
        watch = plugin._watch_service()
        paths, text = await watch.report(['Koucha_'], datetime(2026, 10, 26, 10, 1, tzinfo=service.cards.CST))
        assert paths == []
        await watch.send(123, paths, text)
    asyncio.run(run())
    assert '967 分 (+29)' in plugin.api.post_group_msg.await_args.kwargs['text']


def test_existing_player_without_season_data_can_subscribe(setup_watch):
    _, service, plugin, event, _ = setup_watch
    plugin.client.get_player_stat.side_effect = RuntimeError('玩家不存在或赛季无数据')
    asyncio.run(plugin._dispatch('watch', 'Hyx499', event))
    plugin.client.get_player.assert_awaited_once_with('Hyx499')
    assert service.store.get_subscriptions('group:123') == ['Hyx499']


def test_nonexistent_player_is_not_written(setup_watch):
    _, service, plugin, event, _ = setup_watch
    plugin.client.get_player_stat.side_effect = RuntimeError('玩家不存在或赛季无数据')
    plugin.client.get_player.side_effect = RuntimeError('not found')
    asyncio.run(plugin._dispatch('watch', 'missing', event))
    assert service.store.get_subscriptions('group:123') == []
    assert plugin.api.post_group_msg.await_count == 1


def test_scheduler_worker_dispatches_back_to_the_bot_event_loop(setup_watch):
    _, _, plugin, _, _ = setup_watch
    async def run():
        loop = asyncio.get_running_loop()
        plugin._watch_event_loop = loop
        seen = []
        async def record_loop():
            seen.append(asyncio.get_running_loop())
        plugin._daily_watch_task = record_loop
        await asyncio.to_thread(plugin._schedule_daily_watch)
        await asyncio.wrap_future(plugin._watch_scheduled_future)
        assert seen == [loop]
    asyncio.run(run())


def test_runtime_survives_framework_temporary_loader_loop(setup_watch, monkeypatch):
    import sys
    from types import ModuleType
    _, _, plugin, _, _ = setup_watch
    utils = ModuleType('ncatbot.utils')
    utils.OFFICIAL_STARTUP_EVENT = 'startup'
    utils.OFFICIAL_HEARTBEAT_EVENT = 'heartbeat'
    monkeypatch.setitem(sys.modules, 'ncatbot.utils', utils)
    handlers, registered, loader_loops = {}, [], []
    plugin.register_handler = lambda event, handler: handlers.setdefault(event, handler)
    plugin.add_scheduled_task = lambda **kw: registered.append(kw)
    plugin._init_data = AsyncMock()
    seen = []
    async def daily():
        seen.append(asyncio.get_running_loop())
    plugin._daily_watch_task = daily
    async def load():
        loader_loops.append(asyncio.get_running_loop())
        await plugin.on_load()
    async def run():
        client_loop = asyncio.get_running_loop()
        # Exactly the loader's worker-thread lifecycle: run init, then close its loop.
        await asyncio.to_thread(lambda: asyncio.run(load()))
        assert loader_loops[0].is_closed()
        assert plugin._watch_event_loop is None
        plugin._init_data.assert_not_awaited()
        await handlers['heartbeat'](None)
        await asyncio.sleep(0)
        assert plugin._watch_event_loop is client_loop
        assert seen == [client_loop]
        # Repeated startup/heartbeat events must not create duplicate background jobs.
        await handlers['startup'](None)
        await handlers['heartbeat'](None)
        plugin._init_data.assert_awaited_once()
        await asyncio.to_thread(registered[0]['job_func'])
        await asyncio.wrap_future(plugin._watch_scheduled_future)
        assert seen == [client_loop, client_loop]
        await plugin.on_close()
    asyncio.run(run())


def test_closed_loader_loop_is_reported_instead_of_silently_dropping_ticks(setup_watch, capsys):
    _, _, plugin, _, _ = setup_watch
    loop = asyncio.new_event_loop()
    loop.close()
    plugin._watch_event_loop = loop
    plugin._schedule_daily_watch()
    plugin._schedule_daily_watch()
    assert capsys.readouterr().out.count('尚未派发定时任务') == 1
