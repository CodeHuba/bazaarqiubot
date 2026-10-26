"""
大巴扎 (The Bazaar) QQ 群插件
- bz me <用户名>      mrmao 玩家信息
- bz item <名字>      物品百科
- bz skill <名字>     技能百科
- bz npc <名字>       商人百科
- bz day <1-10|event> 当日 encounter 列表
- bz boss <名字>      encounter 详情
- bz search <关键词>  跨物品/技能搜索
- bz status / refresh 缓存状态 / 强制刷新
"""
import asyncio
import base64
import difflib
import os
import re
import time
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv

from ncatbot.plugin_system import NcatBotPlugin, on_message
from ncatbot.core.event import BaseMessageEvent

from .data_client import BazaarDataClient
from . import formatter as fmt, subscriptions as subs, tooltip_translations as tt_trans
from . import chart as chart_mod
from . import history_chart as hist_chart_mod
from . import bazaardb_client as bdb
from . import card_data_paths

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

# 管理员 QQ（用于 /bz refresh），从环境变量读，未设置则禁用
ADMIN_QQ = os.getenv("BAZAAR_ADMIN_QQ", os.getenv("ROOT_QQ", "")).strip()

# CQ 码处理
CQ_AT_ANY_RE = re.compile(r"\[CQ:at,qq=\d+[^\]]*\]")
CQ_ANY_RE = re.compile(r"\[CQ:[^\]]+\]")

# 触发前缀：#bz（避免 /bz 触发QQ表情），同时保留中文别名
TRIGGER_RE = re.compile(r"^\s*(#bz\b\s*|[/／]\s*(巴扎|大巴扎)\b\s*)", re.IGNORECASE)

# 冷却（秒）
COOLDOWN_PLAYER = 10   # /bz me 同 user
COOLDOWN_DEFAULT = 3   # 其他指令同 user

# 单条回复最大字符数（兜底）
MAX_REPLY_LEN = 3500
DB_CANDIDATE_LIMIT = 10


def _db_type(card):
    value = card.get('Type') or card.get('$type') or card.get('type') or ''
    value = str(value).lower()
    if value in ('eventencounter', 'tcardencounterevent'):
        return '商店' if 'Merchant' in (card.get('Tags') or []) else '遭遇事件'
    return {'item': '物品', 'tcarditem': '物品', 'skill': '技能', 'tcardskill': '技能',
            'monster': '野怪', 'combatencounter': '野怪', 'tcardencountercombat': '野怪',
            'merchant': '商店', 'event': '遭遇事件', 'pedestalencounter': '遭遇事件'}.get(value, '未知类型')


def _db_title(card):
    from . import translations as trans
    title = (card.get('Localization') or {}).get('Title') or card.get('Title') or {}
    if isinstance(title, str):
        title = {'Text': title}
    text = title.get('Text') or card.get('title') or card.get('InternalName') or ''
    return trans.get_zh_by_hash(title.get('Key', '')) or trans.get_zh(text) or text


def _db_candidates(query, cards=(), monsters=(), kind=''):
    """Rank one deduplicated, typed candidate list across all supported sources."""
    from .gamedata_client import normalize_card_name
    labels = {'item': '物品', 'skill': '技能', 'monster': '野怪', 'merchant': '商店', 'event': '遭遇事件'}
    entries = []
    for card in cards:
        label, name = _db_type(card), _db_title(card)
        entries.append({'name': name, 'type': label, 'id': card.get('Id') or card.get('id') or card.get('urlId'),
                        'aliases': [name, card.get('InternalName', ''), card.get('title', ''),
                                    ((card.get('Localization') or {}).get('Title') or {}).get('Text', '')]})
    for monster in monsters:
        entries.append({'name': monster['name'], 'type': '野怪', 'id': monster.get('id'),
                        'aliases': [monster['name'], monster.get('name_en', '')]+list(monster.get('aliases') or []),
                        'day': monster.get('day'), 'level': monster.get('level'), 'tier': monster.get('tier')})
    normalized = normalize_card_name(query)
    ranked, seen = [], set()
    for entry in entries:
        if not entry['name'] or (kind and entry['type'] != labels.get(kind)):
            continue
        identities = [(entry['type'], 'id', entry['id'])] if entry['id'] else []
        if entry['type'] == '野怪':
            # Identical visible variants share one fuzzy suggestion. Querying their
            # full name still opens the complete, ID-selectable form list.
            identities.append(('野怪', 'variant', normalize_card_name(entry['name']),
                               str(entry.get('day')), entry.get('level'), entry.get('tier')))
        if entry['type'] != '野怪' or not entry['id']:
            identities.append((entry['type'], 'name', normalize_card_name(entry['name'])))
        if any(identity in seen for identity in identities):
            continue
        seen.update(identities)
        aliases = [normalize_card_name(a) for a in entry['aliases'] if a]
        entry['score'] = max((difflib.SequenceMatcher(None, normalized, a).ratio()
                              + (0.4 if normalized in a or a in normalized else 0) for a in aliases), default=0)
        ranked.append(entry)
    ranked.sort(key=lambda e: -e['score'])
    return ranked[:DB_CANDIDATE_LIMIT]


def _format_db_candidates(query, candidates):
    from .monster_data import day_label
    lines = [f"🔍 未精确匹配『{query}』，找到以下候选（最多 {DB_CANDIDATE_LIMIT} 个）："]
    for index, entry in enumerate(candidates[:DB_CANDIDATE_LIMIT], 1):
        detail = entry['type']
        if entry['type'] == '野怪' and entry.get('level') is not None:
            detail += f"｜{day_label(entry.get('day'))}｜等级 {entry['level']}"
        lines.append(f"{index}. {entry['name']}（{detail}）")
    example = candidates[0]
    prefix = ('事件 ' if example['type']=='遭遇事件' else example['type']+' ') if example['type'] in ('物品', '技能', '野怪', '商店', '遭遇事件') else ''
    lines.append(f"请使用完整名称和类型重试，例如：#bz db {prefix}{example['name']}")
    return '\n'.join(lines)



def _image_upload_value(img_path: str | None) -> str | None:
    """将本地图片转换为 NapCat 可接收的 base64 上传值。"""
    if not img_path:
        return None
    path = Path(img_path)
    if not path.is_file():
        return None
    return "base64://" + base64.b64encode(path.read_bytes()).decode("ascii")


class BazaarPlugin(NcatBotPlugin):
    name = "BazaarPlugin"
    version = "1.0.0"

    async def on_load(self):
        self.client = BazaarDataClient()
        self._cooldown: dict[tuple, float] = defaultdict(float)
        # 启动时异步加载（不阻塞插件 on_load 完成）
        asyncio.create_task(self._init_data())
        # 注册每日 10:00 推送任务
        self.add_scheduled_task(
            job_func=self._daily_watch_task,
            name="bazaar_daily_watch",
            interval="10:00",  # 每天 10:00
        )
        print(f"[{self.name}] 已加载 v{self.version}, 每日 10:00 推送已启用")

    async def _init_data(self):
        try:
            await self.client.bootstrap()
            s = self.client.status()
            print(f"[{self.name}] 数据就绪: items={s['items']} skills={s['skills']} merchants={s['merchants']} days={s['encounter_days']}")
            # 同步 tooltip 翻译缓存的版本号(items+skills 版本变了就清空旧翻译)
            combined_ver = s["versions"].get("items", "") + "|" + s["versions"].get("skills", "")
            tt_trans.set_version(combined_ver)
        except Exception as e:
            print(f"[{self.name}] 数据加载失败: {e}")

    # ===== 主入口 =====
    @on_message
    async def handle(self, event: BaseMessageEvent):
        raw = event.raw_message or ""
        # 剥掉 @CQ 码（群里被 @ 也允许，但纯文本要带 /bz）
        text = CQ_AT_ANY_RE.sub("", raw).strip()
        # 不剥其他 CQ（图片之类）→ 直接看是否以前缀开头
        m = TRIGGER_RE.match(text)
        if not m:
            return
        body = text[m.end():].strip()
        # 把残留的 CQ 码全清掉
        body = CQ_ANY_RE.sub("", body).strip()

        if not body:
            await event.reply(fmt.format_help())
            return

        # 解析子命令
        parts = body.split(maxsplit=1)
        sub = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        user_id = getattr(event, "user_id", 0)

        # 冷却检查
        cd_key = (user_id, sub)
        cd_window = COOLDOWN_PLAYER if sub == "me" else COOLDOWN_DEFAULT
        now = time.time()
        if (now - self._cooldown[cd_key]) < cd_window:
            remain = int(cd_window - (now - self._cooldown[cd_key]))
            await event.reply(f"指令冷却中，{remain}s 后再试")
            return

        try:
            reply = await self._dispatch(sub, arg, event)
        except Exception as e:
            import traceback as _tb
            print(f"[{self.name}] 处理 bz {sub} 失败: {e}\n{_tb.format_exc()}")
            reply = f"[巴扎] 处理失败: {e}"

        # reply=None 表示子命令已自行发送（如图片），仍需记录冷却
        self._cooldown[cd_key] = now
        if reply:
            if len(reply) > MAX_REPLY_LEN:
                reply = reply[:MAX_REPLY_LEN] + "\n...(已截断)"
            print(f"[{self.name}] bz {sub} reply({len(reply)}chars): {reply[:200]!r}")
            try:
                await event.reply(reply)
            except Exception as reply_error:
                import traceback as _tb2
                print(f"[{self.name}] event.reply 失败: {reply_error}\n{_tb2.format_exc()}")
                # 图片 CQ 发送失败时，去掉图片后重试纯文字，避免整条查询无回复。
                if "[CQ:image," in reply:
                    text_fallback = re.sub(r"\[CQ:image,[^\]]*\]", "", reply).strip()
                    if text_fallback:
                        try:
                            await event.reply(text_fallback)
                            print(f"[{self.name}] 已降级发送纯文字回复")
                        except Exception as fallback_error:
                            print(f"[{self.name}] 纯文字降级回复也失败: {fallback_error}")

    # ===== 子命令分发 =====
    @staticmethod
    def _parse_player_season_arg(arg: str) -> tuple[str, int | None, str | None]:
        """解析用户名和可选实际赛季；接口 seasonId 始终比实际赛季大 1。"""
        username = arg.strip()
        if not username:
            return "", None, "[巴扎] 请提供用户名"
        parts = username.rsplit(maxsplit=1)
        if len(parts) == 2 and re.fullmatch(r"[+-]?[0-9]+", parts[1]):
            try:
                season = int(parts[1])
            except ValueError:
                return "", None, "[巴扎] 赛季号必须为正整数"
            if season <= 0:
                return "", None, "[巴扎] 赛季号必须为正整数"
            return parts[0].strip(), season + 1, None
        return username, None, None

    async def _dispatch(self, sub: str, arg: str, event: BaseMessageEvent) -> str:
        if sub in {"help", "帮助", "?"}:
            return fmt.format_help()

        if sub == "refresh":
            user_id = getattr(event, "user_id", 0)
            if not ADMIN_QQ or str(user_id) != str(ADMIN_QQ):
                return "[巴扎] 只有管理员能强制刷新"
            try:
                await self.client.refresh(force=True)
                return "✅ 巴扎百科已强制刷新\n" + fmt.format_status(self.client.status())
            except Exception as e:
                return f"[巴扎] 刷新失败: {e}"

        if sub == "me":
            if not arg:
                return "用法: #bz me <用户名>"
            return await self._cmd_player(arg)

        if sub == "stat":
            if not arg:
                return "用法: #bz stat <用户名> [赛季号]"
            username, season_id, error = self._parse_player_season_arg(arg)
            if error:
                return error
            return await self._cmd_player_stat(username, event, season_id)

        if sub in {"history", "hist", "历史"}:
            if not arg:
                return "用法: #bz history <用户名> [赛季号] [--cb]"
            # 解析 --cb 色盲模式标志
            colorblind = "--cb" in arg
            season_arg = arg.replace("--cb", "").strip()
            username_h, season_id, error = self._parse_player_season_arg(season_arg)
            if error:
                return error
            return await self._cmd_player_history(username_h, event, season_id, colorblind=colorblind)

        if sub in {"db", "数据库", "card", "卡牌"}:
            if not arg:
                return "用法: #bz db <物品、技能、野怪、商店或事件名>（支持官方中文、英文）\n示例: #bz db 米透 / #bz db 事件 药房"
            return await self._cmd_db(arg, event)


        if sub == "watch":
            if not arg:
                return "用法: #bz watch <用户名>"
            return await self._cmd_watch(event, arg)

        if sub == "unwatch":
            if not arg:
                return "用法: #bz unwatch <用户名>"
            return await self._cmd_unwatch(event, arg)

        if sub == "watchlist":
            return self._cmd_watchlist(event)

        if sub in {"winrate", "胜率"}:
            return await self._cmd_winrate(arg, event)

        if sub == "testpush":
            user_id = getattr(event, "user_id", 0)
            if not ADMIN_QQ or str(user_id) != str(ADMIN_QQ):
                return "[巴扎] 只有管理员能触发测试推送"
            return await self._cmd_testpush(event)

        return f"未知子命令: {sub}\n\n" + fmt.format_help()

    # ===== 各子命令 =====
    async def _cmd_player(self, username: str) -> str:
        # 用户名安全：只允许常见字符 + 中文
        if len(username) > 30 or any(c in username for c in "<>\"'\\"):
            return "[巴扎] 用户名格式不合法"
        try:
            data = await self.client.get_player(username)
        except Exception as e:
            return f"[巴扎] 查询失败: {e}"
        return fmt.format_player(username, data)

    async def _cmd_player_stat(self, username: str, event: BaseMessageEvent, season_id: int | None = None) -> str | None:
        if len(username) > 30 or any(c in username for c in "<>\"'\\"):
            return "[巴扎] 用户名格式不合法"
        try:
            data = await self.client.get_player_stat(username, season_id=season_id) if season_id is not None else await self.client.get_player_stat(username)
        except Exception as e:
            return f"[巴扎] 查询失败: {e}"

        rh = data.get("ratingHistory") or []
        if not rh:
            return f"📊 {username} · 本赛季无记录"

        # 生成图表
        try:
            img_path = await asyncio.get_event_loop().run_in_executor(
                None, chart_mod.generate_stat_chart, username, rh
            )
        except Exception as e:
            print(f"[{self.name}] 图表生成失败，fallback 文字: {e}")
            return fmt.format_player_stat(username, data)

        # 发图片（群聊/私聊分别处理）
        is_private = getattr(event, "message_type", None) == "private"
        try:
            image_value = _image_upload_value(img_path)
            if image_value is None:
                return fmt.format_player_stat(username, data)
            if is_private:
                user_id = getattr(event, "user_id", 0)
                await self.api.post_private_msg(user_id=user_id, image=image_value)
            else:
                group_id = getattr(event, "group_id", 0)
                await self.api.post_group_msg(group_id=group_id, image=image_value)
        except Exception as e:
            print(f"[{self.name}] 图片发送失败，fallback 文字: {e}")
            return fmt.format_player_stat(username, data)

        return None  # 图已发，handle 不再 reply

    async def _cmd_player_history(self, username: str, event: BaseMessageEvent, season_id: int | None = None, colorblind: bool = False) -> str | None:
        if len(username) > 30 or any(c in username for c in "<>\"'\\"):
            return "[巴扎] 用户名格式不合法"
        try:
            data = await self.client.get_player_stat(username, season_id=season_id) if season_id is not None else await self.client.get_player_stat(username)
        except Exception as e:
            return f"[巴扎] 查询失败: {e}"

        rh = data.get("ratingHistory") or []
        if not rh:
            return f"📊 {username} · 本赛季无记录"

        try:
            img_path = await asyncio.get_event_loop().run_in_executor(
                None, hist_chart_mod.generate_history_chart, username, rh, colorblind
            )
        except Exception as e:
            print(f"[{self.name}] history图表生成失败: {e}")
            return f"[巴扎] 图表生成失败: {e}"

        is_private = getattr(event, "message_type", None) == "private"
        try:
            image_value = _image_upload_value(img_path)
            if image_value is None:
                return "[巴扎] 图片文件生成失败"
            if is_private:
                user_id = getattr(event, "user_id", 0)
                await self.api.post_private_msg(user_id=user_id, image=image_value)
            else:
                group_id = getattr(event, "group_id", 0)
                await self.api.post_group_msg(group_id=group_id, image=image_value)
        except Exception as e:
            print(f"[{self.name}] history图片发送失败: {e}")
            return f"[巴扎] 图片发送失败: {e}"

        return None


    async def _cmd_winrate(self, arg: str, event=None) -> str | None:
        """#bz winrate <卡牌1> [卡牌2] ... [--hero 英雄] [--days N] [--legendary]"""
        if not arg:
            return "用法: #bz winrate <卡牌名> [卡牌2] [--hero 英雄] [--days N] [--legendary]\n示例: #bz winrate 万剑之王 --hero Pygmalien"

        import re as _re
        from .runs_query import RunsQuery

        # 解析参数
        hero = None
        days = None
        rank_filter = "all"
        m_hero = _re.search(r'--hero\s+(\S+)', arg)
        m_days = _re.search(r'--days\s+(\d+)', arg)
        if m_hero:
            hero = m_hero.group(1)
        if m_days:
            days = int(m_days.group(1))
        if '--legendary' in arg:
            rank_filter = 'legendary'
        arg = _re.sub(r'--hero\s+\S+|--days\s+\d+|--legendary\b', '', arg)

        cards = arg.split()
        if not cards:
            return "用法: #bz winrate <卡牌名> [卡牌2] [--hero 英雄] [--days N] [--legendary]"

        def query():
            rq = RunsQuery()
            try:
                return rq.winrate(cards=cards, hero=hero, days=days, rank_filter=rank_filter)
            finally:
                if rq.conn is not None:
                    rq.conn.close()

        try:
            result = await asyncio.to_thread(query)
        except Exception as e:
            return f"[巴扎] 胜率查询失败: {e}"

        fallback = fmt.format_winrate(result, hero=hero, days=days, rank_filter=rank_filter)
        if event is None or result.get('not_found') or not result.get('card_names'):
            return fallback
        try:
            from .winrate_image import render_winrate
            if not hasattr(self, '_winrate_render_slots'):
                self._winrate_render_slots = asyncio.Semaphore(2)
            async with self._winrate_render_slots:
                path = await asyncio.to_thread(render_winrate, result)
            upload = _image_upload_value(path)
            if upload is None:
                return fallback
            if getattr(event, 'message_type', None) == 'private':
                await self.api.post_private_msg(user_id=event.user_id, image=upload)
            else:
                await self.api.post_group_msg(group_id=event.group_id, image=upload)
        except Exception as error:
            print(f'[{self.name}] 胜率图片失败，fallback 文字: {error}')
            return fallback
        return None


    async def _cmd_monster_db(self, matches, repository, event=None):
        from .monster_data import day_label, format_monster_text
        if len(matches) != 1:
            lines = ["找到多个野怪形态，请使用以下名称查询："]
            for entry in matches[:DB_CANDIDATE_LIMIT]:
                lines.append(f"• {entry['name']}（野怪｜{day_label(entry.get('day'))}，等级 {entry.get('level')}）\n#bz db 野怪 {entry['name']} --形态 {entry['id']}")
            return '\n'.join(lines)
        detail = await asyncio.to_thread(repository.detail, matches[0])
        fallback = format_monster_text(detail)
        if event is None:
            return fallback
        try:
            from .monster_image import render_monster
            # Bound expensive renders/downloads while keeping the event loop responsive.
            if not hasattr(self, '_monster_render_slots'):
                self._monster_render_slots = asyncio.Semaphore(2)
            async with self._monster_render_slots:
                path = await asyncio.to_thread(render_monster, detail)
            upload = _image_upload_value(path)
            if not upload:
                return fallback
            if getattr(event, 'message_type', None) == 'private':
                await self.api.post_private_msg(user_id=event.user_id, image=upload)
            else:
                await self.api.post_group_msg(group_id=event.group_id, image=upload)
            return None
        except Exception as error:
            print(f"[bz db] 野怪图片发送失败，降级文字: {error}")
            return fallback


    async def _cmd_card_db(self, raw, db_path, name, show_enchants=False, event=None, quality='', enchantment=''):
        from . import gamedata_client as gdc, translations as trans, card_image_helper as cih
        fallback = gdc.format_card_from_raw(raw, zh_name=trans.get_zh(name) or '',
                                            db_path=str(db_path), show_enchants=show_enchants)
        if event is None:
            art = cih.get_art_url(card_id=raw.get('Id', ''), internal_name=name, size='artLarge')
            return (f'[CQ:image,file={art}]\n' if art else '')+fallback
        try:
            from .card_preview import CardPreviewRepository
            from .card_sheet import build_document, render_document, format_document_text
            repository = getattr(self, '_card_sheet_repository', None)
            if repository is None or repository.database != Path(db_path):
                repository = self._card_sheet_repository = CardPreviewRepository(db_path)
            try:
                document = await asyncio.to_thread(build_document, raw, db_path, quality, enchantment,
                                                  show_enchants, repository)
            except ValueError as error:
                return str(error)
            fallback = format_document_text(document)
            if not hasattr(self, '_card_render_slots'):
                self._card_render_slots = asyncio.Semaphore(2)
            async with self._card_render_slots:
                paths = await asyncio.to_thread(render_document, document)
            for path in paths:
                upload = _image_upload_value(path)
                if not upload:
                    raise OSError('图片文件暂不可用')
                if getattr(event, 'message_type', None) == 'private':
                    await self.api.post_private_msg(user_id=event.user_id, image=upload)
                else:
                    await self.api.post_group_msg(group_id=event.group_id, image=upload)
            return None
        except Exception as error:
            print(f'[bz db] 卡牌图片发送失败，降级文字: {str(error)[:400]}')
            return fallback


    async def _cmd_encounter_db(self, matches, repository, event=None):
        from .encounter_data import format_document_text
        regular=[entry for entry in matches if entry.get('raw',{}).get('SpawningEligibility')=='Always']
        if len(matches)>1 and regular:matches=regular
        if len(matches)!=1:
            lines=['找到多个遭遇版本，请指定版本查询（最多 10 个）：']
            for entry in matches[:DB_CANDIDATE_LIMIT]:
                label='商店' if entry['kind']=='merchant' else '遭遇事件'
                prefix='商店' if entry['kind']=='merchant' else '事件'
                lines.append(f"• {entry['name']}（{label}｜{entry['heroes']}）\n#bz db {prefix} {entry['name']} --形态 {entry['id']}")
            return '\n'.join(lines)
        document=await asyncio.to_thread(repository.detail,matches[0])
        fallback=format_document_text(document)
        if event is None:return fallback
        try:
            from .encounter_image import render_document
            if not hasattr(self,'_encounter_render_slots'):
                self._encounter_render_slots=asyncio.Semaphore(2)
            async with self._encounter_render_slots:
                paths=await asyncio.to_thread(render_document,document)
            for path in paths:
                upload=_image_upload_value(path)
                if not upload:raise OSError('图片文件暂不可用')
                if getattr(event,'message_type',None)=='private':
                    await self.api.post_private_msg(user_id=event.user_id,image=upload)
                else:
                    await self.api.post_group_msg(group_id=event.group_id,image=upload)
            return None
        except Exception as error:
            print(f'[bz db] 商店/事件图片发送失败，降级文字: {str(error)[:400]}')
            return fallback


    async def _cmd_db(self, arg: str, event=None) -> str | None:
        """查询卡牌和野怪数据，野怪使用官中、实际阵容与原比例卡图。"""
        from . import gamedata_client as gdc
        from . import translations as trans
        from . import card_image_helper as cih
        import asyncio
        loop = asyncio.get_event_loop()

        # 解析参数
        flag_re = r'(?<!\S)(?:--enchants|-e)(?!\S)'
        show_enchants = bool(re.search(flag_re, arg))
        query_arg = re.sub(flag_re, '', arg).strip()
        card_options = {'quality': '', 'enchantment': ''}
        for option, flags in (('quality', '品质|quality'), ('enchantment', '附魔|enchant')):
            pattern = r'(?<!\S)--(?:'+flags+r')\s+(\S+)'
            values = re.findall(pattern, query_arg)
            if len(values) > 1:
                return '品质、附魔参数各只能指定一次。'
            if values:
                card_options[option] = values[0]
                query_arg = re.sub(pattern, '', query_arg).strip()
        if re.search(r'(?<!\S)--(?:品质|quality|附魔|enchant)(?!\S)', query_arg):
            return '用法: #bz db <名字> [--品质 黄金] [--附魔 寒冰]，或使用 -e 查看全部附魔。'
        kind_match = re.match(r'^(野怪|monster|物品|item|技能|skill|商店|商人|merchant|事件|遭遇|遭遇事件|event)\s+(.+)$', query_arg, re.I)
        kind = ''
        if kind_match:
            kind = {'野怪': 'monster', 'monster': 'monster', '物品': 'item', 'item': 'item', '技能': 'skill', 'skill': 'skill',
                    '商店':'merchant','商人':'merchant','merchant':'merchant','事件':'event','遭遇':'event','遭遇事件':'event','event':'event'}[kind_match[1].lower()]
            query_arg = kind_match[2].strip()
        form = re.search(r'\s+--形态\s+([\w-]+)$', query_arg)
        form_id = form[1] if form else ''
        if form:
            query_arg = query_arg[:form.start()].strip()
        if not query_arg:
            return '用法: #bz db <卡牌名或野怪名>'
        if show_enchants and card_options['enchantment']:
            return '请用 -e 查看全部附魔，或用 --附魔 指定一种附魔。'

        # 官方中文全名只做精确转换；非完整中文名留给候选搜索。
        en_name = query_arg.strip()
        if trans.has_chinese(query_arg):
            exact_en = trans.get_en(query_arg)
            if exact_en:
                en_name = exact_en

        db_path = card_data_paths.get_gamedata_db_path(
            Path(__file__).resolve().parent / "cache" / "GameData.db",
            require_exists=True,
        )
        raw = None
        repository, monsters, monster_candidates = None, [], []
        encounter_repository,encounters,encounter_candidates=None,[],[]
        if db_path and kind not in ('item','skill','monster'):
            try:
                from .encounter_data import EncounterRepository
                encounter_repository=getattr(self,'_encounter_repository',None)
                if encounter_repository is None or encounter_repository.database!=Path(db_path):
                    encounter_repository=self._encounter_repository=EncounterRepository(db_path)
                encounter_kind=kind if kind in ('merchant','event') else ''
                encounters=await asyncio.to_thread(encounter_repository.search,query_arg,True,encounter_kind)
                if form_id and encounters:
                    encounters=[e for e in encounters if e['id']==form_id]
                    if not encounters:return '未找到该商店或事件版本，请重新查询完整名称。'
                if not encounters:
                    encounter_candidates=await asyncio.to_thread(encounter_repository.search,query_arg,False,encounter_kind)
            except Exception as error:
                print(f'[bz db] 商店/事件查询失败: {error}')
                encounter_repository=None
        if kind in ('merchant','event'):
            if show_enchants or any(card_options.values()):
                return '商店和事件按实际规则展示，请直接查询名称；品质和附魔参数用于物品、技能。'
            if encounters:return await self._cmd_encounter_db(encounters,encounter_repository,event)
            candidates=_db_candidates(query_arg,[encounter_repository.candidate(e) for e in encounter_candidates],kind=kind) if encounter_repository else []
            if len(candidates)==1:
                matches=[e for e in encounter_candidates if e['name']==candidates[0]['name']]
                return await self._cmd_encounter_db(matches,encounter_repository,event)
            if candidates:return _format_db_candidates(query_arg,candidates)
            return '未找到该商店或遭遇事件，请检查名称或换一个关键词。'
        if db_path:
            if kind not in ('item', 'skill'):
                try:
                    from .monster_data import MonsterRepository
                    repository = getattr(self, '_monster_repository', None)
                    if repository is None or repository.database != Path(db_path):
                        repository = self._monster_repository = MonsterRepository(db_path)
                    monsters = await asyncio.to_thread(repository.search, query_arg)
                    if form_id:
                        monsters = [m for m in monsters if m['id'] == form_id]
                        if not monsters:
                            return '未找到该野怪形态，请重新查询完整野怪名。'
                    if not monsters:
                        monster_candidates = await asyncio.to_thread(repository.search, query_arg, False)
                except Exception as error:
                    print(f"[bz db] 本地野怪查询失败: {error}")
                    repository = None
            if kind == 'monster':
                if any(card_options.values()):
                    return '野怪详情按实际携带品质和附魔展示，请直接查询野怪名。'
                if monsters:
                    return await self._cmd_monster_db(monsters, repository, event)
                if monster_candidates:
                    candidates = _db_candidates(query_arg, monsters=monster_candidates, kind=kind)
                    if len(candidates) == 1:
                        selected = [m for m in monster_candidates if m['name'] == candidates[0]['name']]
                        return await self._cmd_monster_db(selected, repository, event)
                    return _format_db_candidates(query_arg, candidates)
                return '未找到该野怪，请检查名称或等待野怪数据更新。'
            try:
                raw_args = (en_name, db_path, kind) if kind else (en_name, db_path)
                raw = await loop.run_in_executor(None, gdc.query_raw_by_name, *raw_args)
                if raw is None and en_name != query_arg.strip():
                    raw_args = (query_arg.strip(), db_path, kind) if kind else (query_arg.strip(), db_path)
                    raw = await loop.run_in_executor(None, gdc.query_raw_by_name, *raw_args)
            except Exception as e:
                print(f"[bz db] GameData.db 查询失败: {e}")

        if encounters:
            if (raw or {}).get('Type') in ('Item','Skill') or monsters:
                options=[]
                if raw and raw.get('Type') in ('Item','Skill'):options.append('#bz db '+_db_type(raw)+' '+query_arg)
                if monsters:options.append('#bz db 野怪 '+query_arg)
                options.extend('#bz db '+('商店' if e['kind']=='merchant' else '事件')+' '+query_arg for e in encounters)
                return f'『{query_arg}』匹配多个类型，请指定类型：\n'+'\n'.join(dict.fromkeys(options))
            if show_enchants or any(card_options.values()):
                return '商店和事件按实际规则展示，请直接查询名称。'
            return await self._cmd_encounter_db(encounters,encounter_repository,event)
        if monsters:
            card_type = (raw or {}).get('Type')
            if card_type in ('Item', 'Skill'):
                label = '物品' if card_type == 'Item' else '技能'
                return f"『{query_arg}』同时匹配卡牌和野怪，请指定类型：\n#bz db {label} {query_arg}\n#bz db 野怪 {query_arg}"
            if any(card_options.values()):
                return '野怪详情按实际携带品质和附魔展示，请直接查询野怪名。'
            return await self._cmd_monster_db(monsters, repository, event)
        if kind == 'monster':
            return '野怪数据暂不可用，请稍后重试。'

        if raw is not None:
            if raw.get('Type') in ('Item', 'Skill') or raw.get('$type') in ('TCardItem', 'TCardSkill'):
                return await self._cmd_card_db(raw, db_path, en_name, show_enchants, event, **card_options)
            zh = trans.get_zh(en_name) or ""
            text = gdc.format_card_from_raw(raw, zh_name=zh, db_path=str(db_path), show_enchants=show_enchants)
            card_id = raw.get("Id", "")
            art_url = cih.get_art_url(card_id=card_id, internal_name=en_name, size="artLarge")
            if art_url:
                return f"[CQ:image,file={art_url}]\n" + text
            return text

        # 本地精确匹配失败时，优先返回 GameData.db 的相近候选。
        if raw is None and db_path:
            try:
                if trans.has_chinese(query_arg):
                    zh_names = trans.search_zh(query_arg, limit=DB_CANDIDATE_LIMIT)
                    local_candidates = []
                    for candidate_name in zh_names:
                        candidate = await loop.run_in_executor(
                            None, gdc.query_raw_by_name, candidate_name, db_path, kind
                        )
                        if candidate is not None:
                            local_candidates.append(candidate)
                else:
                    local_candidates = await loop.run_in_executor(
                        None, gdc.suggest_cards, query_arg, db_path, DB_CANDIDATE_LIMIT, kind
                    )
            except Exception as e:
                print(f"[bz db] 本地候选搜索失败: {e}")
                local_candidates = []
            encounter_cards=[encounter_repository.candidate(e) for e in encounter_candidates] if encounter_repository else []
            candidates = _db_candidates(query_arg, local_candidates+encounter_cards, monster_candidates, kind)
            if len(candidates) == 1:
                selected = candidates[0]
                if selected['type'] == '野怪':
                    matches = [m for m in monster_candidates if m['name'] == selected['name']]
                    return await self._cmd_monster_db(matches, repository, event)
                if selected['type'] in ('商店','遭遇事件'):
                    matches=[e for e in encounter_candidates if e['name']==selected['name']]
                    return await self._cmd_encounter_db(matches,encounter_repository,event)
                if selected['type'] in ('物品', '技能'):
                    card = next(c for c in local_candidates
                                if _db_type(c) == selected['type'] and _db_title(c) == selected['name'])
                    return await self._cmd_card_db(card, db_path, selected['name'], show_enchants, event, **card_options)
            if candidates:
                return _format_db_candidates(query_arg, candidates)

        # fallback: bazaardb.gg
        try:
            card = await loop.run_in_executor(None, bdb.query_card_by_name, query_arg)
        except Exception as e:
            return f"[巴扎DB] 查询失败: {e}"

        if card is not None:
            # The remote helper can return its first fuzzy hit. Only an actual full-name
            # match may bypass the typed suggestions; never silently pick that first hit.
            names = [_db_title(card), card.get('InternalName', ''),
                     (card.get('Title') or {}).get('Text', '')]
            exact = any(gdc.normalize_card_name(n) == gdc.normalize_card_name(q)
                        for n in names if n for q in (query_arg, en_name))
            if exact and (not kind or _db_type(card) == {'item': '物品', 'skill': '技能'}.get(kind)):
                return bdb.format_card(card)
        remote_candidates = [card] if card else []
        for search_kind in ([kind] if kind in ('item', 'skill') else ['item', 'skill']):
            try:
                results = await loop.run_in_executor(None, bdb.search_cards, query_arg, search_kind)
                # Each endpoint is authoritative for the category, even if search hits
                # contain only title/urlId and omit their Type field.
                remote_candidates.extend({**c, 'Type': search_kind.title()} for c in results)
            except Exception as error:
                print(f"[bz db] 远程候选搜索失败 ({search_kind}): {error}")
        candidates = _db_candidates(query_arg, remote_candidates, kind=kind)
        if candidates:
            return _format_db_candidates(query_arg, candidates)
        return f"未找到「{query_arg}」，请检查名称或换一个关键词。"


    # ===== 订阅相关 =====
    def _ensure_group(self, event: BaseMessageEvent) -> tuple[int | None, str | None]:
        """只允许群聊订阅。返回 (group_id, error_msg)。"""
        is_private = getattr(event, "message_type", None) == "private"
        if is_private:
            return None, "[巴扎] #bz watch 只能在 QQ 群里使用"
        gid = getattr(event, "group_id", None)
        if not gid:
            return None, "[巴扎] 拿不到群号,请在群里使用"
        return int(gid), None

    async def _cmd_watch(self, event: BaseMessageEvent, username: str) -> str:
        gid, err = self._ensure_group(event)
        if err:
            return err
        if len(username) > 30 or any(c in username for c in "<>\"'\\"):
            return "[巴扎] 用户名格式不合法"
        # 验证用户存在(直接查一次,失败就不让订阅)
        try:
            await self.client.get_player(username)
        except Exception as e:
            return f"[巴扎] 玩家『{username}』查询失败,无法订阅: {e}"

        added = subs.add_subscription(f"group:{gid}", username)
        if not added:
            return f"📌 本群已订阅『{username}』,不用重复订阅"
        cur = subs.get_subscriptions(f"group:{gid}")
        return f"✅ 本群已订阅『{username}』,每天 10:00 推送昨日变化\n当前订阅 {len(cur)} 人"

    async def _cmd_unwatch(self, event: BaseMessageEvent, username: str) -> str:
        gid, err = self._ensure_group(event)
        if err:
            return err
        removed = subs.remove_subscription(f"group:{gid}", username)
        if not removed:
            return f"❎ 本群没有订阅『{username}』"
        cur = subs.get_subscriptions(f"group:{gid}")
        return f"🗑️ 已取消订阅『{username}』,本群剩余 {len(cur)} 人"

    def _cmd_watchlist(self, event: BaseMessageEvent) -> str:
        gid, err = self._ensure_group(event)
        if err:
            return err
        cur = subs.get_subscriptions(f"group:{gid}")
        if not cur:
            return "📭 本群还没订阅任何玩家\n用 #bz watch <用户名> 添加"
        lines = [f"📋 本群订阅 ({len(cur)} 人):"]
        for u in cur:
            lines.append(f"  · {u}")
        lines.append("")
        lines.append("每天 10:00 推送 24h 分数变化")
        return "\n".join(lines)

    async def _cmd_testpush(self, event: BaseMessageEvent) -> str:
        """管理员立即触发一次推送(只推当前群)。"""
        gid, err = self._ensure_group(event)
        if err:
            return err
        cur = subs.get_subscriptions(f"group:{gid}")
        if not cur:
            return "📭 本群没有订阅,无法推送"
        
        # 直接构造报告并发送
        text = await self._build_daily_report(cur)
        if not text:
            return "❌ 构造播报失败"
        
        try:
            await self.api.post_group_msg(group_id=gid, text=text)
            return f"✅ 已推送播报到本群 ({len(cur)} 人)"
        except Exception as e:
            return f"❌ 推送失败: {e}"

    # ===== 每日推送任务 =====
    async def _daily_watch_task(self):
        """每天 10:00 触发,遍历所有群订阅,推送 24h 变化。"""
        all_subs = subs.get_all_subscriptions()
        if not all_subs:
            print(f"[{self.name}] 每日推送: 无订阅,跳过")
            return
        print(f"[{self.name}] 每日推送启动,共 {len(all_subs)} 个聊天")

        # 按群推送
        for chat_key, usernames in all_subs.items():
            if not chat_key.startswith("group:"):
                continue  # 只推群,私聊不支持
            try:
                gid = int(chat_key.split(":", 1)[1])
            except Exception:
                continue
            text = await self._build_daily_report(usernames)
            if not text:
                continue
            try:
                await self.api.post_group_msg(group_id=gid, text=text)
                print(f"[{self.name}] 已推送到群 {gid} ({len(usernames)} 人)")
            except Exception as e:
                print(f"[{self.name}] 推送群 {gid} 失败: {e}")
            # 避免短时间内连发
            await asyncio.sleep(1)

    async def _build_daily_report(self, usernames: list[str]) -> str:
        """构造一份每日播报。"""
        from datetime import datetime
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        lines = [f"📊 大巴扎每日播报 · {now_str}"]
        lines.append("━━━━━━━━━━━━━━")

        for u in usernames:
            try:
                data = await self.client.get_player_stat(u)
            except Exception as e:
                lines.append(f"\n{u}: ❌ {e}")
                continue
            block = fmt.format_daily_diff(u, data)
            lines.append("")
            lines.append(block)

        return "\n".join(lines)
