"""Manual-only event queries. No subscriptions, timers or production mutations."""
import asyncio
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time

from . import secondary_hero_stats as stats, secondary_hero_image as cards
from .runs_query import HERO_EN_TO_ZH, HERO_ZH_TO_EN


class DeliveryRejected(RuntimeError):
    """QQ explicitly rejected the request; unlike a timeout this is safe to retry."""


def check_response(response):
    if isinstance(response, dict):
        code, status = response.get('retcode', 0), response.get('status')
    else:
        code, status = getattr(response, 'retcode', 0), getattr(response, 'status', None)
    if code not in (0, '0', None) or status == 'failed':
        raise DeliveryRejected('QQ 返回发送失败')


class SecondaryHeroService:
    def __init__(self, api):
        self.api = api
        self.lock = asyncio.Lock()
        self.snapshot = None
        self.scope_key = None
        self.expires = 0
        self.reports = {}

    async def report(self, hero=None, order='pick'):
        async with self.lock:
            event = await asyncio.to_thread(stats.load_event)
            now = datetime.now(timezone.utc)
            ended = bool(event.get('end_at') and now >= stats.aware_time(event['end_at']))
            key = (json.dumps(event,sort_keys=True),os.getenv('BAZAAR_SECONDARY_RUNS_DB_PATH',''),
                   os.getenv('RUNS_DB_PATH',''),ended)
            if self.snapshot is None or self.scope_key != key or time.monotonic() >= self.expires:
                self.snapshot = await asyncio.to_thread(stats.query_stats,HERO_EN_TO_ZH,event=event,now=now)
                self.scope_key = key
                self.expires = time.monotonic()+60
                self.reports.clear()
            report_key = hero,order
            if report_key in self.reports:
                return self.reports[report_key]
            document = stats.build_document(self.snapshot,hero=hero,order=order)
            fallback = stats.format_report(document)

            def render_upload():
                path = None
                try:
                    path = Path(cards.render_report(document))
                    return 'base64://'+base64.b64encode(path.read_bytes()).decode('ascii')
                finally:
                    if path is not None:
                        path.unlink(missing_ok=True)

            try:
                image_value = await asyncio.to_thread(render_upload)
            except Exception as exc:
                print(f'[secondary-heroes] 图片生成失败，保留文字结果: {exc}',flush=True)
                image_value = None
            report = document,image_value,fallback
            # At most 8 heroes plus overview, in two sort modes; reset every query.
            self.reports[report_key] = report
            return report

    async def manual(self, arg, event):
        try:
            hero,order = stats.parse_args(arg,HERO_EN_TO_ZH,HERO_ZH_TO_EN)
        except ValueError as exc:
            return str(exc)
        try:
            _,image_value,fallback = await self.report(hero,order)
        except Exception as exc:
            print(f'[secondary-heroes] 查询失败: {exc}',flush=True)
            return '[巴扎] 副角色活动数据暂时查询失败，请稍后重试。'
        private = getattr(event,'message_type',None)=='private'
        payload = {'image':image_value} if image_value else {'text':fallback}
        try:
            if private:
                response = await asyncio.wait_for(self.api.post_private_msg(user_id=event.user_id,**payload),30)
            else:
                response = await asyncio.wait_for(self.api.post_group_msg(group_id=event.group_id,**payload),30)
            check_response(response)
        except DeliveryRejected:
            return fallback
        except Exception as exc:
            print(f'[secondary-heroes] 发送结果待确认: {exc}',flush=True)
            return '[巴扎] 副角色活动图片发送结果待确认，请先查看聊天消息。'
        return None
