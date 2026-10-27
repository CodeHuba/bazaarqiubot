"""Shared image renderer and fixed-window statistics for QQ watch reports."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import uuid
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

CST = timezone(timedelta(hours=8))
IMAGE_WIDTH = 1080
REPORT_PLAYERS_PER_PAGE = 1
BG = '#101621'
PANEL = '#1b2534'
LINE = '#303d50'
TEXT = '#f2f0e9'
MUTED = '#a6b3c5'
GOLD = '#e9c27b'
GREEN = '#8ce1c1'
RED = '#f2a092'


def parse_time(value):
    result = datetime.fromisoformat(value)
    return result.replace(tzinfo=CST) if result.tzinfo is None else result.astimezone(CST)


def report_window(now):
    """Most recent scheduled 10:00 cutoff, shared by every player."""
    end = now.astimezone(CST).replace(hour=10, minute=0, second=0, microsecond=0)
    if now < end:
        end -= timedelta(days=1)
    return end - timedelta(days=1), end


def summarize(history, start, end):
    """Use observations at/before endpoints; never leak later observations into a report."""
    points = []
    for point in history:
        try:
            ts = parse_time(point['timestamp'])
            rating, position = int(point['rating']), int(point['position'])
            if position <= 0:
                continue
        except (KeyError, TypeError, ValueError):
            continue
        points.append(dict(timestamp=ts, rating=rating, position=position))
    points.sort(key=lambda p: p['timestamp'])
    before = [p for p in points if p['timestamp'] <= start]
    inside = [p for p in points if start < p['timestamp'] <= end]
    available = [p for p in points if p['timestamp'] <= end]
    baseline = before[-1] if before else None
    latest = available[-1] if available else None
    state = ('no_records' if not available else 'ok' if baseline and inside
             else 'baseline_missing' if inside else 'no_new_records')
    return dict(
        state=state, baseline=baseline, latest=latest, points=inside,
        rating_delta=latest['rating'] - baseline['rating'] if state == 'ok' else None,
        rank_gain=baseline['position'] - latest['position'] if state == 'ok' else None,
    )


class Canvas:
    def __init__(self, height, preview=True):
        self.image = Image.new('RGB', (IMAGE_WIDTH, height), BG)
        self.draw = ImageDraw.Draw(self.image)
        self.fonts = {}
        self.preview = preview

    def font(self, size, bold=False):
        key = (size, bold)
        if key not in self.fonts:
            names = [
                os.getenv('BAZAAR_WATCH_FONT', ''),
                'C:/Windows/Fonts/msyhbd.ttc' if bold else 'C:/Windows/Fonts/msyh.ttc',
                '/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc' if bold else '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
                '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc',
            ]
            path = next((p for p in names if Path(p).is_file()), None)
            if path is None:
                raise OSError('未找到中文字体')
            self.fonts[key] = ImageFont.truetype(path, size)
        return self.fonts[key]

    def text(self, x, y, value, size=26, color=TEXT, bold=False, right=False):
        value = str(value)
        font = self.font(size, bold)
        if right:
            x -= self.draw.textlength(value, font=font)
        self.draw.text((x, y), value, font=font, fill=color)

    def panel(self, x, y, w, h, fill=PANEL):
        self.draw.rounded_rectangle((x, y, x+w, y+h), radius=22, fill=fill, outline=LINE)

    def fit(self, x, y, value, width, size=41, color=TEXT, bold=True):
        value = str(value)
        while size > 22 and self.draw.textlength(value, font=self.font(size, bold)) > width:
            size -= 1
        while value and self.draw.textlength(value, font=self.font(size, bold)) > width:
            value = value[:-2]+'…'
        self.text(x, y, value, size, color, bold)

    def line(self, y):
        self.draw.line((48, y, 1032, y), fill=LINE, width=2)

    def header(self, title, subtitle):
        self.draw.rectangle((48, 35, 53, 61), fill=GOLD)
        self.text(67, 30, '巴扎丘  /  玩家订阅', 24, GOLD, True)
        self.text(1032, 31, '验收预览' if self.preview else '玩家订阅', 22, MUTED, right=True)
        self.text(48, 95, title, 49, TEXT, True)
        self.text(50, 170, subtitle, 23, MUTED)

    def avatar(self, x, y, username='Koucha_'):
        self.draw.rounded_rectangle((x, y, x+72, y+72), radius=20, fill='#324653')
        self.text(x+17, y+5, username[:1].upper(), 42, GOLD, True)

    def save(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.image.save(path)
        return str(Path(path).resolve())


def subscription_card(username, current, generated, path, count=1, preview=True, season=19, added=True):
    c = Canvas(900, preview)
    c.header('订阅成功' if added else '已在本群订阅', '每日图片战报，关注玩家的分数与排名变化')
    c.panel(48, 233, 984, 241)
    c.avatar(76, 262, username)
    c.fit(172, 263, username, 820, 42)
    c.text(174, 323, f'当前状态 · S{season}', 23, MUTED)
    c.text(78, 381, '分数', 25, MUTED)
    c.text(155, 359, current.get('rating', '—'), 54, GOLD, True)
    c.text(540, 381, '排名', 25, MUTED)
    c.text(620, 359, f"#{current['position']}" if current.get('position') else '—', 54, TEXT, True)
    c.text(52, 507, '每日推送', 26, MUTED)
    c.text(330, 501, '10:00  北京时间', 32, TEXT, True)
    c.line(563)
    c.text(52, 590, '统计范围', 26, MUTED)
    c.text(330, 587, '前一日 10:00 — 当日 10:00', 27)
    c.text(52, 644, '预览名单' if preview else '本群订阅', 26, MUTED)
    c.text(330, 641, f'{count} 位玩家', 27)
    c.panel(48, 716, 984, 69, '#14242a')
    c.text(73, 733, f'取消订阅   #bz unwatch {username}', 26, GREEN)
    note = '订阅操作为界面模拟 · 未加入真实群订阅' if preview else '每日汇总近 24 小时分数与排名变化'
    c.text(50, 815, note, 21, MUTED)
    c.text(50, 850, f"数据记录 {current.get('timestamp') or '本赛季暂无记录'} · mrmao", 20, MUTED)
    return c.save(path)


def watchlist_card(players, generated, path, preview=True, page=1, pages=1, total=None):
    count = len(players)
    c = Canvas(790 + max(0, count-1)*274, preview)
    c.header('本群订阅', f"{'预览名单' if preview else '订阅名单'} · {total if total is not None else count} 位玩家 · 每天 10:00 推送")
    for index, player in enumerate(players):
        username, current = player['username'], player['current']
        y = 232+index*274
        c.panel(48, y, 984, 246)
        c.avatar(76, y+30, username)
        c.fit(172, y+29, username, 610)
        status = '暂时无法更新' if player.get('error') else '数据可用' if current.get('timestamp') else '本赛季无记录'
        c.text(1002, y+46, status, 24, GOLD if player.get('error') else GREEN, right=True)
        c.text(78, y+137, f"{current.get('rating', '—')} 分", 38, GOLD, True)
        c.text(438, y+137, f"排名 #{current.get('position', '—')}", 38, TEXT, True)
        c.text(80, y+201, f"记录时间  {current.get('timestamp') or '暂无可用记录'}", 22, MUTED)
    offset = max(0, count-1)*274
    c.text(51, 522+offset, '订阅管理', 26, GOLD, True)
    c.text(51, 574+offset, '添加玩家   #bz watch <用户名>', 26)
    c.text(51, 619+offset, '取消关注   #bz unwatch <用户名>', 26)
    c.line(690+offset)
    c.text(50, 716+offset, '名单为界面模拟 · 玩家数据为真实查询' if preview else '数据来源 / mrmao · 查询失败时保留订阅', 21, MUTED)
    c.text(1030, 750+offset, f'{page} / {pages}', 20, MUTED, right=True)
    return c.save(path)


def trend(c, points, baseline, start, end, box):
    x, y, w, h = box
    pts = ([baseline] if baseline else []) + points
    if not pts:
        return
    ratings = [p['rating'] for p in pts]
    lo, hi = min(ratings), max(ratings)
    span = max(hi-lo, 10)
    lo -= span * .16
    hi += span * .16
    for i in range(3):
        yy = y + h*i/2
        c.draw.line((x, yy, x+w, yy), fill=LINE)
    coords = []
    for point in pts:
        fraction = max(0, min(1, (point['timestamp']-start).total_seconds()/(end-start).total_seconds()))
        coords.append((x+w*fraction, y+h*(hi-point['rating'])/(hi-lo)))
    color = GREEN if ratings[-1] > ratings[0] else RED if ratings[-1] < ratings[0] else GOLD
    if len(coords) >= 2:
        c.draw.line(coords, fill=color, width=4)
    for xx, yy in (coords[0], coords[-1]):
        c.draw.ellipse((xx-6, yy-6, xx+6, yy+6), fill=color)
    c.text(x, y+h+15, start.strftime('%m/%d %H:%M'), 20, MUTED)
    c.text(x+w, y+h+15, end.strftime('%m/%d %H:%M'), 20, MUTED, right=True)


def daily_card(players, start, end, generated, path, preview=True, season=19, page=1, pages=1, totals=None, test=False):
    players = sorted(players, key=lambda p: (p['summary']['rating_delta'] is None, -(p['summary']['rating_delta'] or 0)))
    footer = 363+len(players)*764
    c = Canvas(footer+178, preview)
    c.header('每日战报' + (' · 测试预览' if test else ''), f"{start:%m月%d日 %H:%M} — {end:%m月%d日 %H:%M}  ·  北京时间")
    c.panel(48, 225, 984, 108)
    c.text(78, 244, '订阅玩家', 22, MUTED)
    totals = totals or report_totals(players)
    c.text(78, 278, f"{totals['count']} 位", 28, TEXT, True)
    c.text(406, 244, '分数上涨', 22, MUTED)
    rising = totals['rising']
    c.text(406, 278, f'{rising} 位', 28, GREEN, True)
    c.text(730, 244, '数据异常', 22, MUTED)
    missing = totals['missing']
    c.text(730, 278, f'{missing} 位', 28, TEXT, True)
    for index, player in enumerate(players):
        daily_player(c, player, start, end, 363+index*764, season)
    c.text(50, footer+10, '分数持平也可能发生排名变化 · 排名以截止记录为准', 22, MUTED)
    c.line(footer+57)
    c.text(50, footer+78, '订阅管理  #bz watch <用户名>  /  #bz watchlist', 23)
    c.text(50, footer+123, f"{'验收预览 · ' if preview else ''}数据 / mrmao · 生成于 {generated:%m/%d %H:%M}", 20, MUTED)
    c.text(1030, footer+123, f'{page} / {pages}', 20, MUTED, right=True)
    return c.save(path)


def daily_player(c, player, start, end, top, season=19):
    username, result = player['username'], player['summary']
    gain = result['rating_delta']
    c.panel(48, top, 984, 740)
    c.avatar(78, top+32, username)
    c.fit(174, top+32, username, 570, 42)
    c.text(1002, top+50, f'S{season} · 近 24 小时', 22, MUTED, right=True)
    c.text(80, top+144, '分数变化', 24, MUTED)
    c.text(559, top+144, '排名变化', 24, MUTED)
    base, latest = result['baseline'], result['latest']
    if result['state'] == 'ok':
        rank = result['rank_gain']
        c.text(76, top+182, f'{gain:+d}' if gain else '持平', 78, GREEN if gain > 0 else RED if gain < 0 else GOLD, True)
        c.text(555, top+188, ('↑ ' if rank > 0 else '↓ ' if rank < 0 else '→ ') + str(abs(rank)), 66, GREEN if rank > 0 else RED if rank < 0 else GOLD, True)
        c.text(80, top+297, f"{base['rating']} → {latest['rating']} 分", 28)
        c.text(560, top+297, f"#{base['position']} → #{latest['position']}", 28)
    else:
        message = {'no_new_records': '窗口内无新增记录', 'no_records': '本赛季暂无记录',
                   'query_failed': '暂时无法更新，稍后再试'}.get(result['state'], '起点数据不足，暂不计算变化')
        c.text(80, top+204, message, 34, GOLD, True)
        if latest:
            c.text(80, top+297, f"最近记录 {latest['rating']} 分 · #{latest['position']}", 28)
    c.text(80, top+366, '窗口内分数走势', 23, MUTED)
    trend(c, result['points'], base, start, end, (82, top+427, 916, 140))
    if base:
        c.text(80, top+635, f"起点记录 {base['timestamp']:%m/%d %H:%M:%S}", 22, MUTED)
    if latest:
        c.text(80, top+673, f"截止记录 {latest['timestamp']:%m/%d %H:%M:%S}", 22, MUTED)


def report_totals(players):
    return dict(count=len(players),
                rising=sum((p['summary']['rating_delta'] or 0) > 0 for p in players),
                missing=sum(p['summary']['state'] in ('baseline_missing', 'query_failed') for p in players))


def status_card(title, message, path, preview=False):
    c = Canvas(550, preview)
    c.header(title, '群订阅 · 每日 10:00 北京时间')
    c.panel(48, 230, 984, 184)
    # Wrap errors/help without allowing arbitrary length to overflow the image.
    lines, current = [], ''
    for char in str(message)[:240]:
        if char == '\n' or c.draw.textlength(current+char, font=c.font(26)) > 920:
            lines.append(current)
            current = '' if char == '\n' else char
        else:
            current += char
    lines.append(current)
    for index, line in enumerate(lines[:4]):
        c.text(78, 248+index*37, line, 26)
    c.text(50, 462, '添加  #bz watch <用户名>     名单  #bz watchlist', 23, MUTED)
    return c.save(path)


def render_pages(kind, players, generated, output, *, start=None, end=None, season=19, test=False, preview=False):
    """One player per daily page; equal-size canvases avoid narrow long-image previews."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    per_page = REPORT_PLAYERS_PER_PAGE if kind == 'daily' else 6
    if kind == 'daily':
        players = sorted(players, key=lambda p: (p['summary']['rating_delta'] is None, -(p['summary']['rating_delta'] or 0)))
    chunks = [players[i:i+per_page] for i in range(0, len(players), per_page)]
    if not chunks:
        return [status_card('本群暂无订阅', '用 #bz watch <用户名> 添加关注', output/(uuid.uuid4().hex+'.png'), preview=preview)]
    paths = []
    token = uuid.uuid4().hex
    for index, chunk in enumerate(chunks, 1):
        path = output/f'{token}-{index}.png'
        if kind == 'daily':
            paths.append(daily_card(chunk, start, end, generated, path, preview=preview, season=season,
                                    page=index, pages=len(chunks), totals=report_totals(players), test=test))
        else:
            paths.append(watchlist_card(chunk, generated, path, preview=preview,
                                        page=index, pages=len(chunks), total=len(players)))
    return paths
