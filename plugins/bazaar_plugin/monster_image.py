"""Pillow monster sheets with native-aspect card art and actual board slots."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import os
from pathlib import Path
import re
import tempfile
import threading
import time
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw, ImageFont, ImageOps
from .card_preview import QUALITY_LABELS
from .monster_data import day_label

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / 'plugins/bazaar_plugin/cache/monster_art'
OUTPUT = ROOT / 'data/charts/monsters'
VERSION = 'monster-sheet-2'
POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix='monster-art')
FAILED = {}
LOCK = threading.RLock()
SIZES = {'Small': 1, 'Medium': 2, 'Large': 3}
COLORS = {'Bronze': '#bd895e', 'Silver': '#c2cad7', 'Gold': '#dfb65f', 'Diamond': '#79d7ed', 'Legendary': '#f18a58'}
BG, PANEL, TEXT, MUTED, LINE = '#101621', '#192230', '#f0eee8', '#a4afbd', '#303b4c'
GOLD = COLORS['Gold']


def _font_path(bold=False):
    configured = os.getenv('MONSTER_FONT', '')
    candidates = [configured, 'C:/Windows/Fonts/msyhbd.ttc' if bold else 'C:/Windows/Fonts/msyh.ttc',
                  '/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc' if bold else '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
                  '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc']
    for path in candidates:
        if path and Path(path).is_file():
            return path
    raise OSError('未找到中文字体')


def native_art_url(url, size=None):
    """The 256px thumbnails are square; the 400S/400/400L assets preserve the native shape."""
    parsed = urlparse(url or '')
    if parsed.scheme != 'https' or parsed.hostname != 's.bazaardb.gg' or not parsed.path.startswith('/v1/'):
        return ''
    if size in SIZES:
        suffix = {'Small': 'S', 'Medium': '', 'Large': 'L'}[size]
        return re.sub(r'@\d+[SL]?\.webp', '@400'+suffix+'.webp', url)
    return url


def _valid_ratio(image, size):
    return size not in SIZES or abs(image.width/image.height - SIZES[size]/2) < 0.015


def load_art(url, size=None, cache=CACHE):
    source = native_art_url(url, size)
    if not source:
        return None
    cache = Path(cache)
    key = hashlib.sha256(source.encode()).hexdigest()
    target = cache / (key+'.png')
    if target.is_file():
        try:
            with Image.open(target) as image:
                image.load()
                if _valid_ratio(image, size):
                    return image.convert('RGBA')
        except (OSError, ValueError):
            pass
    with LOCK:
        if time.monotonic()-FAILED.get(key, -1000) < 180:
            return None
    try:
        request = Request(source, headers={'User-Agent': 'BazaarQiuBot/1.0'})
        with urlopen(request, timeout=6) as response:
            content = response.read(4*1024*1024+1)
        if len(content) > 4*1024*1024:
            raise ValueError('图片过大')
        with Image.open(io.BytesIO(content)) as native:
            if native.width*native.height > 4_000_000 or not _valid_ratio(native, size):
                raise ValueError('图片比例与卡牌尺寸不符')
            native.load()
            image = native.convert('RGBA')
        cache.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=cache, suffix='.png')
        os.close(fd)
        try:
            image.save(name)
            Path(name).replace(target)
        finally:
            Path(name).unlink(missing_ok=True)
        return image
    except (OSError, ValueError):
        with LOCK:
            FAILED[key] = time.monotonic()
        return None


class Sheet:
    def __init__(self, height):
        self.image = Image.new('RGB', (1440, height), BG)
        self.draw = ImageDraw.Draw(self.image)
        self.fonts = {}

    def font(self, size, bold=False):
        key = (size, bold)
        if key not in self.fonts:
            self.fonts[key] = ImageFont.truetype(_font_path(bold), size)
        return self.fonts[key]

    def text(self, x, y, value, size=28, color=TEXT, bold=False, center=False):
        value = str(value)
        font = self.font(size, bold)
        if center:
            x -= self.draw.textlength(value, font=font)/2
        self.draw.text((int(x), int(y)), value, font=font, fill=color)

    def wrap(self, value, width, size=27):
        lines = []
        for paragraph in str(value).split('\n'):
            current = ''
            for char in paragraph:
                if current and self.draw.textlength(current+char, font=self.font(size)) > width:
                    lines.append(current)
                    current = ''
                current += char
            lines.append(current)
        return lines

    def panel(self, box, color=PANEL, radius=20, outline=None, width=1):
        self.draw.rounded_rectangle(tuple(map(int, box)), radius, fill=color, outline=outline, width=width)

    def art(self, image, box, color, circle=False):
        x, y, w, h = map(int, box)
        self.panel((x, y, x+w, y+h), '#101721', 14)
        if image is not None:
            # Uniform scaling only. The frame includes 4px of border on every side.
            layer = ImageOps.contain(image, (w-8, h-8))
            if circle:
                mask = Image.new('L', layer.size)
                ImageDraw.Draw(mask).ellipse((0, 0, layer.width-1, layer.height-1), fill=255)
                from PIL import ImageChops
                layer.putalpha(ImageChops.multiply(layer.getchannel('A'), mask))
            self.image.paste(layer, (x+(w-layer.width)//2, y+(h-layer.height)//2), layer)
        else:
            self.text(x+w/2, y+h/2-16, '图片暂缺', 20, MUTED, center=True)
        if circle:
            # The official skill icons have transparent/round artwork; keep all of it visible.
            self.draw.ellipse((x, y, x+w, y+h), outline=color, width=5)
        else:
            self.draw.rounded_rectangle((x, y, x+w, y+h), 12, outline=color, width=5)


def _cooldown(card):
    return next((a['value'] for a in card.get('attributes', []) if a['key'] == 'CooldownMax'), '被动')


def render_monster(detail, output=OUTPUT, cache=CACHE):
    items, skills = detail.get('items', []), detail.get('skills', [])
    jobs = [POOL.submit(load_art, detail.get('art'), None, cache)]
    jobs += [POOL.submit(load_art, c.get('img'), c.get('size') if c in items else None, cache) for c in items+skills]
    assets = [job.result() for job in jobs]
    out = Path(output)
    out.mkdir(parents=True, exist_ok=True)
    fingerprint = hashlib.sha256((detail['stamp']+VERSION+_font_path()).encode()).hexdigest()[:24]
    target = out/(fingerprint+'.png')
    if target.is_file() and all(a is not None for a in assets):
        return str(target.resolve())

    # Preserve genuine socket positions, including empty slots. Wrap additional boards.
    rows, row = [], []
    occupied = 0
    for card in items:
        try:
            start = int(card.get('slot', '').rsplit('_', 1)[-1])
        except ValueError:
            start = occupied
        span = SIZES.get(card.get('size'), 1)
        if start < occupied or start+span > 10:
            rows.append(row)
            row, occupied, start = [], 0, 0
        row.append((start, span, card))
        occupied = start+span
    rows.append(row)
    measure = Sheet(10)
    prepared = []
    for card in items:
        lines = []
        for effect in card.get('effects', []):
            content = effect.get('text', '')
            if effect.get('condition'):
                content = effect['condition']+'：'+content
            lines.extend(measure.wrap(content, 1208, 27))
        if card.get('enchantmentLabel'):
            lines.insert(0, '附魔：'+card['enchantmentLabel'])
        prepared.append((card, lines or ['暂无效果资料']))
    skill_cards = []
    for card in skills:
        content = '\n'.join(e['text'] for e in card.get('effects', [])) or '暂无效果资料'
        lines = measure.wrap(content, 444, 26)
        skill_cards.append((card, lines))
    skill_heights = [max(201, 93+max((len(lines) for _, lines in skill_cards[i:i+2]), default=1)*38)
                     for i in range(0, len(skill_cards), 2)]
    board_height = 380*len(rows)
    skills_y = 550+board_height+40
    details_y = skills_y+62+sum(h+20 for h in skill_heights)+32
    if not skills:
        details_y += 48
    detail_height = sum(max(126, 69+len(lines)*37)+14 for _, lines in prepared)
    height = details_y+65+detail_height+210+(150 if detail.get('mechanics') else 0)
    sheet = Sheet(height)
    sheet.text(64, 31, '巴扎丘百科', 25, GOLD, True)
    sheet.text(1060, 31, '#bz db 野怪查询', 24, MUTED)
    sheet.draw.line((64, 82, 1376, 82), fill=LINE)
    sheet.panel((64, 110, 1376, 436), '#1b2432', outline=LINE)
    tier_color = COLORS.get(detail.get('tier'), GOLD)
    sheet.art(assets[0], (82, 127, 292, 292), tier_color)
    title_size = 64
    while sheet.draw.textlength(detail['name'], font=sheet.font(title_size, True)) > 900 and title_size > 30:
        title_size -= 2
    sheet.text(414, 135, detail['name'], title_size, TEXT, True)
    sheet.text(416, 237, '出现：'+day_label(detail.get('day')), 31, tier_color, True)
    sheet.text(972, 242, QUALITY_LABELS.get(detail.get('tier'), '')+'野怪', 27, tier_color)
    sheet.text(416, 305, f"等级  {detail.get('level', '?')}", 31, TEXT, True)
    sheet.text(688, 305, f"生命  {detail.get('health', '?')}", 31, TEXT, True)
    sheet.text(416, 369, f"战斗奖励  {detail.get('gold', '?')} 金币 · {detail.get('xp', '?')} 经验", 27, GOLD)
    sheet.text(1060, 371, '沙尘暴'+('开启' if detail.get('sandstorm') else '关闭'), 25, MUTED)
    sheet.text(64, 480, '01  物品阵容', 33, TEXT, True)
    sheet.text(1006, 487, '按实际站位 · 从左到右', 23, MUTED)
    art_by_identity = {id(card): asset for card, asset in zip(items+skills, assets[1:])}
    for ri, row in enumerate(rows):
        y = 540+ri*380
        sheet.panel((64, y, 1376, y+364))
        unit = (1208-8*len(row)-12*max(0, len(row)-1))/10
        for ci, (start, span, card) in enumerate(row):
            x, w = 116+start*unit+ci*20, span*unit+8
            # Inside the border, width:height is exactly span:2.
            h = unit*2+8
            color = COLORS.get(card.get('quality'), GOLD)
            sheet.art(art_by_identity[id(card)], (x, y+23, w, h), color)
            name_lines = sheet.wrap(card['name'], w+5, 25)
            for li, line in enumerate(name_lines[:2]):
                sheet.text(x+w/2, y+277+li*29, line, 25, TEXT, True, True)
            sheet.text(x+w/2, y+337, QUALITY_LABELS.get(card.get('quality'), '')+' · '+_cooldown(card), 20, color, center=True)
    sheet.text(64, skills_y, '02  携带技能', 33, TEXT, True)
    yy = skills_y+62
    for index, (card, lines) in enumerate(skill_cards):
        x = 64+(index%2)*668
        if index and index%2 == 0:
            yy += skill_heights[index//2-1]+20
        h = skill_heights[index//2]
        color = COLORS.get(card.get('quality'), GOLD)
        sheet.panel((x, yy, x+644, yy+h), outline=LINE)
        sheet.art(art_by_identity[id(card)], (x+22, yy+34, 116, 116), color, True)
        title_size = 29
        while sheet.draw.textlength(card['name'], font=sheet.font(title_size, True)) > 365 and title_size > 18:
            title_size -= 1
        sheet.text(x+163, yy+18, card['name'], title_size, TEXT, True)
        sheet.text(x+550, yy+22, QUALITY_LABELS.get(card.get('quality'), ''), 22, color)
        for j, line in enumerate(lines):
            sheet.text(x+163, yy+78+j*38, line, 26, MUTED)
    if not skills:
        sheet.text(90, yy, '无携带技能', 27, MUTED)
    sheet.text(64, details_y, '03  物品详细效果', 33, TEXT, True)
    sheet.text(1017, details_y+9, '按携带品质展示基础效果', 23, MUTED)
    yy = details_y+65
    for index, (card, lines) in enumerate(prepared, 1):
        h = max(126, 69+len(lines)*37)
        color = COLORS.get(card.get('quality'), GOLD)
        sheet.panel((64, yy, 1376, yy+h), radius=16)
        sheet.draw.rounded_rectangle((64, yy+18, 69, yy+h-18), 2, fill=color)
        sheet.text(88, yy+12, f'{index:02d}', 23, color, True)
        sheet.text(139, yy+10, card['name'], 29, TEXT, True)
        sheet.text(1050, yy+17, QUALITY_LABELS.get(card.get('quality'), '')+' · '+_cooldown(card), 21, color)
        for j, line in enumerate(lines):
            sheet.text(139, yy+58+j*37, line, 27, MUTED)
        yy += h+14
    if detail.get('mechanics'):
        sheet.panel((64, yy+10, 1376, yy+142), '#232b37', outline='#66563c')
        sheet.text(88, yy+29, '机制说明', 27, GOLD, True)
        for j, line in enumerate(detail['mechanics']):
            sheet.text(262, yy+29+j*44, line, 26)
        yy += 150
    footer = detail.get('notice') or '数值为基础效果，战斗中的相邻加成与动态变化另行计算。'
    sheet.text(64, yy+23, footer, 23, GOLD if detail.get('notice') else MUTED)
    sheet.text(64, yy+75, f"中文：游戏官方中文 · 卡图及天数：Bazaar DB · 天数资料更新：{detail.get('source_date') or '暂无'}", 21, MUTED)
    sheet.text(64, yy+113, '物品保留原始比例 · 边框表示实际品质 · 部分动态数值需结合战斗状态', 21, MUTED)
    fd, name = tempfile.mkstemp(dir=out, suffix='.png')
    os.close(fd)
    try:
        sheet.image.save(name)
        Path(name).replace(target)
    finally:
        Path(name).unlink(missing_ok=True)
    return str(target.resolve())
