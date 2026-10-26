"""QQ winrate result images, using the accepted compact native-art layout."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import uuid

from PIL import Image, ImageDraw, ImageFont, ImageFilter

from .monster_image import _font_path, load_art

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / 'data/charts/winrate'
CACHE = ROOT / 'plugins/bazaar_plugin/cache/monster_art'
BG, PANEL, LINE = '#101621', '#192332', '#303e50'
TEXT, MUTED, GOLD, GREEN = '#f3f0e8', '#a8b5c7', '#e9c27b', '#8ce1c1'
CST = timezone(timedelta(hours=8))
WIDTH = 1080


def render_winrate(result, output=None, *, queried_at=None, preview=False):
    """Return one image for a card or joint combination; never invent missing data."""
    names = result.get('card_names') or []
    if not names or result.get('not_found'):
        raise ValueError('卡牌未完整识别')
    total = int(result.get('total') or 0)
    wins = int(result.get('ten_win') or 0)
    if not 0 <= wins <= total:
        raise ValueError('胜率统计计数不合法')
    rate = wins / total if total else None
    queried_at = queried_at or datetime.now(CST)
    if queried_at.tzinfo is None:
        queried_at = queried_at.replace(tzinfo=CST)

    fonts = {}

    def font(size, bold=False):
        key = size, bold
        if key not in fonts:
            fonts[key] = ImageFont.truetype(_font_path(bold), size)
        return fonts[key]

    def wrap(value, max_width, size):
        lines, line = [], ''
        for char in value:
            if line and font(size, True).getlength(line + char) > max_width:
                lines.append(line)
                line = ''
            line += char
        return lines + ([line] if line else [])

    title = ' + '.join(names)
    title_size = 55 if font(55, True).getlength(title) <= 984 else 40
    title_lines = wrap(title, 984, title_size)
    title_height = 66 if len(title_lines) == 1 else len(title_lines) * (title_size + 12)
    art_top = 126 + title_height
    details = result.get('card_details') or []
    # Keep 1:2, 1:1 and 3:2 art ratios. Longer combinations wrap into rows.
    art_height = 356 if len(names) <= 3 else 260
    rows, row, used = [], [], 0
    for index, name in enumerate(names):
        detail = details[index] if index < len(details) else {}
        size = detail.get('size') or 'Small'
        card_width = round(art_height * {'Small': .5, 'Medium': 1, 'Large': 1.5}.get(size, 1))
        if row and used + 42 + card_width > 888:
            rows.append(row)
            row, used = [], 0
        row.append((name, detail, size, card_width))
        used += card_width + (42 if len(row) > 1 else 0)
    if row:
        rows.append(row)
    art_panel_height = 56 + len(rows) * art_height + (len(rows) - 1) * 32
    after_art = art_top + art_panel_height
    height = after_art + 596
    if height > 6000:
        raise ValueError('卡牌组合过长，使用文字结果')
    im = Image.new('RGB', (WIDTH, height), BG)
    draw = ImageDraw.Draw(im)

    def text(x, y, value, size=26, fill=TEXT, bold=False, align='left'):
        value = str(value)
        width = draw.textlength(value, font=font(size, bold))
        if align == 'center':
            x -= width / 2
        elif align == 'right':
            x -= width
        draw.text((int(x), int(y)), value, font=font(size, bold), fill=fill)

    def panel(box, fill=PANEL, radius=24):
        draw.rounded_rectangle(box, radius=radius, fill=fill, outline=LINE, width=1)

    draw.rounded_rectangle((48, 40, 54, 69), radius=3, fill=GOLD)
    text(70, 36, '巴扎丘Bot  /  卡牌胜率', 25, GOLD, True)
    if preview:
        panel((866, 34, 1032, 82), fill='#26303b', radius=14)
        text(949, 42, '效果预览', 23, GOLD, align='center')
    for index, line in enumerate(title_lines):
        text(48, 106 + index * (title_size + 12), line, title_size, TEXT, True)

    panel((48, art_top, 1032, after_art))
    for row_index, row in enumerate(rows):
        x = (WIDTH - sum(card[3] for card in row) - 42 * (len(row) - 1)) // 2
        y = art_top + 24 + row_index * (art_height + 32)
        for column, (name, detail, size, card_width) in enumerate(row):
            if column:
                text(x - 21, y + art_height / 2 - 23, '+', 34, GOLD, True, 'center')
            art = load_art(detail.get('img', ''), size, cache=CACHE)
            if art is not None:
                art.thumbnail((card_width, art_height), Image.Resampling.LANCZOS)
                shadow = Image.new('RGBA', im.size)
                ImageDraw.Draw(shadow).rounded_rectangle(
                    (x - 12, y + 8, x + card_width + 12, y + art_height + 26),
                    radius=30, fill=(0, 0, 0, 110))
                shadow = shadow.filter(ImageFilter.GaussianBlur(20))
                im.paste(shadow, (0, 0), shadow)
                im.paste(art, (x + (card_width-art.width)//2, y+(art_height-art.height)//2), art)
            else:
                panel((x, y, x + card_width, y + art_height), fill='#233044')
                for n, line in enumerate(wrap(name, card_width-24, 25)):
                    text(x + card_width/2, y + 72 + n*36, line, 25, TEXT, True, 'center')
                text(x + card_width/2, y + art_height-62, '卡图暂缺', 22, MUTED, align='center')
            x += card_width + 42

    text(540, after_art+34, '组合10胜率' if len(names)>1 else '10 胜率', 29, MUTED, True, 'center')
    if rate is not None:
        pct = f'{rate*100:.1f}'
        num_width = font(124, True).getlength(pct)
        unit_width = font(58, True).getlength('%')
        start = (WIDTH-num_width-unit_width-12)/2
        text(start, after_art+70, pct, 124, GREEN, True)
        text(start+num_width+12, after_art+141, '%', 58, GREEN, True)
    else:
        text(540, after_art+70, '—', 124, MUTED, True, 'center')
        text(540, after_art+224, '暂无数据', 25, MUTED, align='center')
    draw.rounded_rectangle((108, after_art+257, 972, after_art+271), radius=7, fill='#293647')
    if rate:
        draw.rounded_rectangle((108, after_art+257, 108+max(1, round(864*rate)), after_art+271), radius=7, fill=GREEN)

    panel((48, after_art+310, 528, after_art+458))
    panel((552, after_art+310, 1032, after_art+458))
    text(80, after_art+328, '出场数', 25, MUTED)
    text(584, after_art+328, '胜场数 · 达到10胜', 25, MUTED)
    for x, value, color in ((80, total, TEXT), (584, wins, GOLD)):
        size = 54
        while size > 26 and font(size, True).getlength(f'{value:,}') > 360:
            size -= 2
        text(x, after_art+366, f'{value:,}', size, color, True)
    text(470, after_art+396, '局', 25, MUTED)
    text(974, after_art+396, '局', 25, MUTED)

    denominator = '同时含这些卡牌的对局数' if len(names)>1 else '含该卡牌的对局数'
    text(50, after_art+490, '10胜率 = 达到10胜的对局数 ÷ '+denominator, 23, MUTED)
    text(50, after_art+538, '已收录对局  ·  查询于 '+queried_at.astimezone(CST).strftime('%Y-%m-%d %H:%M'), 21, MUTED)
    text(1032, after_art+538, '#bz winrate' if len(names)>1 else '#bz winrate '+names[0], 21, GOLD, align='right')
    folder = Path(output) if output else OUTPUT
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / ('winrate-'+uuid.uuid4().hex+'.png')
    im.save(target)
    return str(target.resolve())
