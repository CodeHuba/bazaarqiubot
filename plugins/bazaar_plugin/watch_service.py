"""Watch command orchestration and durable, per-page daily delivery."""
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
import time
import uuid

import httpx

from . import subscriptions as store, watch_cards as cards
from .data_client import CURRENT_SEASON_ID, PLAYER_STAT_SEASON_ID

OUTPUT = Path(__file__).resolve().parents[2]/'data/charts/watch'


def split_text(text):
    """Bound emergency text without dropping subscribed players."""
    parts, current = [], ''
    for line in text.splitlines():
        if current and len(current)+len(line)+1 > 3400:
            parts.append(current)
            current = ''
        current += ('\n' if current else '')+line
    if current:
        parts.append(current)
    return parts or ['暂无可用记录']


def valid_username(value):
    return bool(value.strip()) and len(value) <= 30 and not any(c in value for c in '<>"\'\\\n\r')


class WatchService:
    def __init__(self, client, api):
        self.client, self.api = client, api
        self.query_slots = asyncio.Semaphore(3)
        self.render_slots = asyncio.Semaphore(1)
        self.daily_lock = asyncio.Lock()

    async def render(self, function, *args, **kwargs):
        async with self.render_slots:
            return await asyncio.to_thread(function, *args, **kwargs)

    async def send(self, gid, paths, fallback):
        """Text is only a fallback when the image cannot be constructed/uploaded."""
        import base64
        if not paths:
            for part in split_text(fallback):
                await self.api.post_group_msg(group_id=gid, text=part)
            return
        for path in paths:
            try:
                value = 'base64://'+base64.b64encode(Path(path).read_bytes()).decode('ascii')
            except OSError:
                await self.api.post_group_msg(group_id=gid, text=fallback[:3500])
                continue
            # Do not immediately resend as text after an uncertain transport acknowledgement.
            await self.api.post_group_msg(group_id=gid, image=value)

    async def status(self, event, title, message):
        path = OUTPUT/(uuid.uuid4().hex+'.png')
        try:
            image = await self.render(cards.status_card, title, message, path)
        except Exception as exc:
            print(f'[watch] 状态图生成失败: {exc}')
            return f'{title}\n{message}'
        gid = getattr(event, 'group_id', None)
        if gid and getattr(event, 'message_type', None) != 'private':
            await self.send(int(gid), [image], f'{title}\n{message}')
        else:
            import base64
            value = 'base64://'+base64.b64encode(Path(image).read_bytes()).decode('ascii')
            await self.api.post_private_msg(user_id=event.user_id, image=value)
        return None

    async def query(self, username, now):
        async with self.query_slots:
            try:
                for attempt in range(2):
                    try:
                        data = await self.client.get_player_stat(username)
                        break
                    except (httpx.TimeoutException, httpx.NetworkError):
                        if attempt:
                            raise
                        await asyncio.sleep(.4)
                summary = cards.summarize(data.get('ratingHistory') or [], now-timedelta(days=1), now)
                latest = summary['latest']
                current = dict(rating=latest['rating'], position=latest['position'],
                               timestamp=latest['timestamp'].strftime('%Y-%m-%d %H:%M:%S')) if latest else {}
                return dict(username=username, data=data, current=current)
            except Exception as exc:
                print(f'[watch] 查询 {username} 失败: {exc}')
                return dict(username=username, data={}, current={}, error=True)

    async def collect(self, usernames, now):
        unique = {}
        for name in usernames:
            unique.setdefault(name.strip().casefold(), name.strip())
        players = await asyncio.gather(*(self.query(name, now) for name in unique.values()))
        return dict(zip(unique, players))

    async def watch(self, event, username):
        username = username.strip()
        if not valid_username(username):
            return await self.status(event, '无法订阅', '用户名格式不合法，请检查后重试')
        current, note = {}, ''
        try:
            data = await self.client.get_player_stat(username)
            now = datetime.now(cards.CST)
            latest = cards.summarize(data.get('ratingHistory') or [], now-timedelta(days=1), now)['latest']
            if latest:
                current = dict(rating=latest['rating'], position=latest['position'], timestamp=latest['timestamp'].strftime('%Y-%m-%d %H:%M:%S'))
            if not data.get('ratingHistory'):
                # An empty season response alone does not establish that the account exists.
                await self.client.get_player(username)
        except RuntimeError as exc:
            if str(exc) == '玩家不存在或赛季无数据':
                try:
                    await self.client.get_player(username)
                    note = '本赛季暂无记录，已有数据后会出现在每日战报中'
                except Exception:
                    return await self.status(event, '无法订阅', f'玩家 {username} 不存在或暂时无法查询，请稍后重试')
            else:
                return await self.status(event, '无法订阅', f'玩家 {username} 暂时无法查询，请稍后重试')
        except Exception:
            return await self.status(event, '无法订阅', f'玩家 {username} 暂时无法查询，请稍后重试')
        chat = f'group:{int(event.group_id)}'
        added = await asyncio.to_thread(store.add_subscription, chat, username)
        count = len(await asyncio.to_thread(store.get_subscriptions, chat))
        fallback = f"{'订阅成功' if added else '已在本群订阅'} {username}，本群 {count} 人，每天北京时间 10:00 推送。{note}"
        try:
            path = await self.render(cards.subscription_card, username, current, datetime.now(cards.CST),
                                     OUTPUT/(uuid.uuid4().hex+'.png'), count=count, preview=False,
                                     season=CURRENT_SEASON_ID, added=added)
        except Exception as exc:
            print(f'[watch] 订阅图生成失败: {exc}')
            return fallback
        await self.send(int(event.group_id), [path], fallback)
        return None

    async def unwatch(self, event, username):
        if not valid_username(username):
            return await self.status(event, '无法取消订阅', '用户名格式不合法')
        chat = f'group:{int(event.group_id)}'
        removed = await asyncio.to_thread(store.remove_subscription, chat, username)
        count = len(await asyncio.to_thread(store.get_subscriptions, chat))
        return await self.status(event, '已取消订阅' if removed else '本群未订阅该玩家',
                                 f'{username.strip()}\n本群剩余 {count} 位玩家')

    async def watchlist(self, event):
        names = await asyncio.to_thread(store.get_subscriptions, f'group:{int(event.group_id)}')
        if not names:
            return await self.status(event, '本群暂无订阅', '用 #bz watch <用户名> 添加关注')
        now = datetime.now(cards.CST)
        players = list((await self.collect(names, now)).values())
        try:
            paths = await self.render(cards.render_pages, 'list', players, now, OUTPUT)
        except Exception as exc:
            print(f'[watch] 名单图生成失败: {exc}')
            return '本群订阅：\n'+'\n'.join(names)
        await self.send(int(event.group_id), paths, '本群订阅：\n'+'\n'.join(names))
        return None

    async def report(self, names, now, collected=None, test=False):
        start, end = cards.report_window(now)
        collected = collected if collected is not None else await self.collect(names, now)
        players = []
        for name in names:
            player = dict(collected[name.strip().casefold()])
            summary = cards.summarize(player['data'].get('ratingHistory') or [], start, end)
            if player.get('error'):
                summary['state'] = 'query_failed'
            player['summary'] = summary
            players.append(player)
        players.sort(key=lambda p: (p['summary']['rating_delta'] is None, -(p['summary']['rating_delta'] or 0)))
        text = [f'大巴扎近24小时战报 {start:%m/%d %H:%M} → {end:%m/%d %H:%M}（北京时间）']
        for player in players:
            result = player['summary']
            if result['state'] == 'ok':
                gain = result['rank_gain']
                rank_label = f'上升 {gain}' if gain > 0 else f'下降 {abs(gain)}' if gain < 0 else '持平 0'
                text.append(f"{player['username']}: {result['latest']['rating']} 分 ({result['rating_delta']:+d})，排名 #{result['latest']['position']}，{rank_label} 位")
            else:
                labels = {'query_failed': '暂时无法更新', 'no_records': '本赛季无记录', 'no_new_records': '窗口内无新增记录', 'baseline_missing': '起点数据不足'}
                text.append(f"{player['username']}: {labels[result['state']]}")
        fallback = '\n'.join(text)
        try:
            paths = await self.render(cards.render_pages, 'daily', players, now, OUTPUT,
                                      start=start, end=end, season=CURRENT_SEASON_ID, test=test)
        except Exception as exc:
            print(f'[watch] 战报图生成失败: {exc}')
            paths = []
        return paths, fallback

    async def testpush(self, event):
        names = await asyncio.to_thread(store.get_subscriptions, f'group:{int(event.group_id)}')
        if not names:
            return await self.status(event, '本群暂无订阅', '用 #bz watch <用户名> 添加关注后再测试')
        paths, fallback = await self.report(names, datetime.now(cards.CST), test=True)
        await self.send(int(event.group_id), paths, fallback)
        return None

    async def daily(self, now=None):
        now = now or datetime.now(cards.CST)
        # Polling uses explicit China time, independent of the deployment host timezone.
        # Startup recovery/retries are limited to the first hour after the scheduled cutoff.
        if not 10 <= now.astimezone(cards.CST).hour < 11 or self.daily_lock.locked():
            return
        async with self.daily_lock:
            await self._daily(now)

    async def _daily(self, now):
        all_subs = await asyncio.to_thread(store.get_all_subscriptions)
        groups = {chat: names for chat, names in all_subs.items()
                  if chat.startswith('group:') and chat[6:].isdigit() and names}
        key = f'{PLAYER_STAT_SEASON_ID}:{cards.report_window(now)[1].date()}'
        print(f'[watch] 日报检查 {key}：{len(groups)} 个订阅群', flush=True)
        batches = {chat: await asyncio.to_thread(store.delivery_pages, chat, key) for chat in groups}
        needed = [name for chat, names in groups.items() if not batches[chat] for name in names]
        collected = await self.collect(needed, now) if needed else {}
        for chat, names in groups.items():
            try:
                if not batches[chat]:
                    paths, fallback = await self.report(names, now, collected)
                    if paths:
                        lines = fallback.splitlines()
                        per_page = cards.REPORT_PLAYERS_PER_PAGE
                        texts = [lines[0]+'\n'+'\n'.join(lines[1+i*per_page:1+(i+1)*per_page]) for i in range(len(paths))]
                    else:
                        texts = split_text(fallback)
                        paths = ['']*len(texts)
                    await asyncio.to_thread(store.prepare_delivery, chat, key, paths, texts)
                rows = await asyncio.to_thread(store.delivery_pages, chat, key)
                for page, path, fallback, state, attempts, updated in rows:
                    if not await asyncio.to_thread(store.claim_delivery, chat, key, page, time.time()):
                        if state == 'sending':
                            print(f'[watch] {chat} {key} 第{page}页发送结果待确认，未自动重发')
                        if state != 'sent':
                            break  # Preserve page order while a previous page needs attention.
                        continue
                    try:
                        await self.send(int(chat[6:]), [path] if path else [], fallback)
                    except Exception as exc:
                        await asyncio.to_thread(store.finish_delivery, chat, key, page, time.time(), False)
                        print(f'[watch] {chat} 第{page}页发送失败: {exc}')
                        break
                    await asyncio.to_thread(store.finish_delivery, chat, key, page, time.time(), True)
                    print(f'[watch] {chat} {key} 第{page}页发送成功', flush=True)
                    await asyncio.sleep(1)
            except Exception as exc:
                print(f'[watch] {chat} 日报失败: {exc}')
