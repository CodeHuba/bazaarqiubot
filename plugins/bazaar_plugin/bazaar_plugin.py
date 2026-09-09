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
                return "用法: #bz stat <用户名>"
            return await self._cmd_player_stat(arg, event)

        if sub in {"history", "hist", "历史"}:
            if not arg:
                return "用法: #bz history <用户名> [--cb]"
            # 解析 --cb 色盲模式标志
            colorblind = "--cb" in arg
            username_h = arg.replace("--cb", "").strip()
            if not username_h:
                return "用法: #bz history <用户名> [--cb]"
            return await self._cmd_player_history(username_h, event, colorblind=colorblind)

        if sub in {"db", "数据库", "card", "卡牌"}:
            if not arg:
                return "用法: #bz db <卡牌名>  (支持中英文，查 bazaardb.gg 数据)"
            return await self._cmd_db(arg)

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
            return await self._cmd_winrate(arg)

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

    async def _cmd_player_stat(self, username: str, event: BaseMessageEvent) -> str | None:
        if len(username) > 30 or any(c in username for c in "<>\"'\\"):
            return "[巴扎] 用户名格式不合法"
        try:
            data = await self.client.get_player_stat(username)
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

    async def _cmd_player_history(self, username: str, event: BaseMessageEvent, colorblind: bool = False) -> str | None:
        if len(username) > 30 or any(c in username for c in "<>\"'\\"):
            return "[巴扎] 用户名格式不合法"
        try:
            data = await self.client.get_player_stat(username)
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


    async def _cmd_winrate(self, arg: str) -> str:
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
            arg = arg[:m_hero.start()] + arg[m_hero.end():]
        if m_days:
            days = int(m_days.group(1))
            arg = arg[:m_days.start()] + arg[m_days.end():]
        if '--legendary' in arg:
            rank_filter = 'legendary'
            arg = arg.replace('--legendary', '')

        cards = arg.split()
        if not cards:
            return "用法: #bz winrate <卡牌名> [卡牌2] [--hero 英雄] [--days N] [--legendary]"

        try:
            rq = RunsQuery()
            result = await asyncio.get_event_loop().run_in_executor(
                None, lambda: rq.winrate(
                    cards=cards,
                    hero=hero,
                    days=days,
                    rank_filter=rank_filter,
                )
            )
        except Exception as e:
            return f"[巴扎] 胜率查询失败: {e}"

        return fmt.format_winrate(result, hero=hero, days=days, rank_filter=rank_filter)

    async def _cmd_db(self, arg: str) -> str:
        """查询卡牌数据（优先本地 GameData.db，fallback bazaardb.gg）"""
        from . import gamedata_client as gdc
        from . import translations as trans
        from . import card_image_helper as cih
        import asyncio
        loop = asyncio.get_event_loop()

        # 解析参数
        show_enchants = '--enchants' in arg or '-e' in arg
        query_arg = re.sub(r'--enchants|-e', '', arg).strip()

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
        if db_path:
            try:
                raw = await loop.run_in_executor(None, gdc.query_raw_by_name, en_name, db_path)
                if raw is None and en_name != query_arg.strip():
                    raw = await loop.run_in_executor(None, gdc.query_raw_by_name, query_arg.strip(), db_path)
            except Exception as e:
                print(f"[bz db] GameData.db 查询失败: {e}")

        if raw is not None:
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
                    zh_names = trans.search_zh(query_arg, limit=5)
                    local_candidates = []
                    for candidate_name in zh_names:
                        candidate = await loop.run_in_executor(
                            None, gdc.query_raw_by_name, candidate_name, db_path
                        )
                        if candidate is not None:
                            local_candidates.append(candidate)
                else:
                    local_candidates = await loop.run_in_executor(
                        None, gdc.suggest_cards, query_arg, db_path, 5
                    )
            except Exception as e:
                print(f"[bz db] 本地候选搜索失败: {e}")
                local_candidates = []
            if local_candidates:
                lines = [f"🔍 未精确匹配『{query_arg}』，你是否要查询:"]
                for card in local_candidates:
                    title = ((card.get("Localization") or {}).get("Title") or {}).get("Text", "")
                    title_zh = trans.get_zh(title) or title or card.get("InternalName", "")
                    card_type = "技能" if card.get("Type") == "Skill" or card.get("$type") == "TCardSkill" else "物品"
                    lines.append(f"  • {title_zh}（{card_type}）")
                lines.append("请使用完整名称重试，例如: #bz db 物品名")
                return "\n".join(lines)

        # fallback: bazaardb.gg
        try:
            card = await loop.run_in_executor(None, bdb.query_card_by_name, query_arg)
        except Exception as e:
            return f"[巴扎DB] 查询失败: {e}"

        if card is None:
            try:
                results_item = await loop.run_in_executor(None, bdb.search_cards, query_arg, "item")
                results_skill = await loop.run_in_executor(None, bdb.search_cards, query_arg, "skill")
            except Exception:
                results_item, results_skill = [], []
            candidates = results_item + results_skill
            if candidates:
                lines = [f"🔍 未精确匹配『{arg}』，找到以下候选:"]
                for c in candidates[:8]:
                    lines.append(f"  • {c['title']}")
                lines.append("请用更精确的名字重试，如: #bz db 万剑之王")
                return "\n".join(lines)
            return f"未找到「{arg}」，请检查卡牌名称"

        return bdb.format_card(card)

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
