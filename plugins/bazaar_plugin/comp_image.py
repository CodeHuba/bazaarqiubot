"""QQ composition analysis images using the accepted compact layout."""
from pathlib import Path
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse
import uuid

from PIL import Image, ImageDraw, ImageFont, ImageOps, PngImagePlugin
import qrcode
from .monster_image import _font_path, load_art
from .comp_command import selected_groups, website_link, scope_label

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT/'data/charts/comp'


def load_comp_art(card):
    url = card.get('img') or ''
    parsed = urlparse(url)
    if not parsed.scheme and parsed.path.startswith('/static/comp-art/'):
        folder = ROOT/'web_runs/static/comp-art'
        target = (folder/parsed.path.removeprefix('/static/comp-art/')).resolve()
        if target.is_relative_to(folder.resolve()) and target.is_file():
            try:
                with Image.open(target) as source:
                    if source.width*source.height <= 4_000_000:
                        return source.convert('RGBA')
            except (OSError, ValueError):
                pass
        return None
    return load_art(url, card.get('size'))
BG, PANEL, LINE = '#101621', '#192332', '#303e50'
TEXT, MUTED, GOLD, GREEN = '#f3f0e8', '#a8b5c7', '#e9c27b', '#8ce1c1'
W = 1080
fonts = {}


def font(size, bold=False):
    key = size, bold
    if key not in fonts:
        fonts[key] = ImageFont.truetype(_font_path(bold), size)
    return fonts[key]


def render_comp(data, output=None, *, preview=False):
    groups = selected_groups(data)
    height = 440 + 580 * len(groups) + 262 + (60 if not groups else 0)
    im = Image.new('RGB', (W, height), BG)
    draw = ImageDraw.Draw(im)

    def text(x, y, value, size=25, color=TEXT, bold=False, anchor='lt'):
        draw.text((round(x), round(y)), str(value), font=font(size, bold), fill=color, anchor=anchor)

    def panel(box, fill=PANEL, outline=LINE, radius=22):
        draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=1)

    def fit(value, max_width, size=24, lines=2):
        result, current = [], ''
        for c in str(value):
            if current and font(size).getlength(current+c) > max_width:
                result.append(current)
                current = ''
            current += c
        if current:
            result.append(current)
        if len(result) > lines:
            result = result[:lines]
            while font(size).getlength(result[-1]+'…') > max_width:
                result[-1] = result[-1][:-1]
            result[-1] += '…'
        return result

    def art(card, box):
        x, y, width, height = box
        source = load_comp_art(card)
        if source is not None:
            layer = ImageOps.contain(source, (int(width), int(height)), Image.Resampling.LANCZOS)
            im.paste(layer, (int(x+(width-layer.width)/2), int(y+(height-layer.height)/2)), layer)
        else:
            panel((x, y, x+width, y+height), '#233044', radius=10)
            text(x+width/2, y+height/2, '暂无卡图', 18, MUTED, anchor='mm')

    anchor = data['anchors'][0]
    base, scope = data['baseline'], data['scope']
    url = website_link(data)
    draw.rounded_rectangle((48, 38, 54, 67), radius=3, fill=GOLD)
    text(70, 39, '巴扎丘Bot  /  阵容分析', 25, GOLD, True)
    if preview:
        panel((845, 31, 1032, 80), '#2c2c2e', '#5c4e38', 13)
        text(938, 55, '效果预览', 22, GOLD, anchor='mm')
    art(anchor, (56, 101, 190, 190))
    title_size = 56
    while title_size > 26 and font(title_size, True).getlength(anchor['name']) > 750:
        title_size -= 2
    for i, row in enumerate(fit(anchor['name'], 750, title_size, 1)):
        text(278, 110+i*(title_size+8), row, title_size, TEXT, True)
    text(280, 191, '围绕这件物品，可以怎么搭？', 27, MUTED)
    for i, row in enumerate(fit(scope_label(data), 750, 21, 2)):
        text(280, 244+i*28, row, 21, MUTED)
    panel((48, 313, 1032, 429))
    metrics = [('命中对局', f"{base['count']:,}", TEXT), ('整体 10 胜率', f"{base['rate']:.1%}" if base['count'] else '—', GREEN), ('平均胜场', f"{base['average_wins']:.1f}" if base['count'] else '—', TEXT)]
    for i, (label, value, color) in enumerate(metrics):
        x = 80 + i*328
        text(x, 330, label, 22, MUTED)
        text(x, 365, value, 42, color, True)
        if i:
            draw.line((x-25, 338, x-25, 405), fill=LINE, width=1)

    for i, group in enumerate(groups):
        y = 452 + i*580
        panel((48, y, 1032, y+558))
        text(78, y+26, f"0{i+1}", 31, GOLD, True)
        text(137, y+30, group['hero_name']+' · 核心组合', 29, TEXT, True)
        label = '按出场局数排序' if group['count'] >= 30 else '小样本探索'
        text(1002, y+32, label, 22, MUTED, anchor='rt')
        count_value = f"{group['count']:,} 局"
        rate_value = f"{group['rate']:.1%}"
        count_size = 44
        while count_size > 26 and font(count_size, True).getlength(count_value) > 350:
            count_size -= 2
        text(78, y+89, count_value, count_size, GOLD, True)
        text(78+font(count_size, True).getlength(count_value)+16, y+109, '出场局数', 24, MUTED)
        text(596, y+89, rate_value, 44, GREEN, True)
        text(596+font(44, True).getlength(rate_value)+16, y+109, '10 胜率', 24, MUTED)
        core = group['core']
        cell = min(250, 880/len(core))
        x0 = (W-cell*len(core))/2
        for j, card in enumerate(core):
            cx = x0 + cell*(j+.5)
            if j:
                text(x0+cell*j, y+215, '+', 28, GOLD, anchor='mm')
            art(card, (cx-cell/2+19, y+160, cell-38, 128))
            for k, row in enumerate(fit(card['name'], cell-22, 24)):
                text(cx, y+302+k*29, row, 24, GOLD if card['cardId']==anchor['cardId'] else TEXT, anchor='mt')
        draw.line((78, y+365, 1002, y+365), fill=LINE, width=1)
        for section, cards, offset in [('补充物品',group['winning_items'][:3],384), ('常见技能',group['winning_skills'][:3],466)]:
            text(78, y+offset+23, section, 21, MUTED)
            for j, card in enumerate(cards):
                x = 194+j*270
                art(card, (x, y+offset, 64, 67))
                for k, row in enumerate(fit(card['name'], 174, 21, 1)):
                    text(x+76, y+offset+8+k*25, row, 21)
                freq = card.get('ten_win_frequency')
                text(x+76, y+offset+40, f"{freq:.0%} 搭配" if freq is not None else '—', 20, GOLD)

    y = 452+len(groups)*580
    if not groups:
        text(48, y+20, '当前筛选下暂无足够样本的核心组合' if base['count'] else '当前版本及筛选条件下暂无数据', 29, MUTED)
        y += 60
    text(48, y+2, '搭配频率：该组合的 10 胜对局中，出现此物品或技能的比例。', 21, MUTED)
    text(48, y+37, '主推荐 ≥30 局，按出场局数降序；各组合样本可能重叠。', 21, MUTED)
    text(48, y+72, '原始采集不含 0 胜局，结果不代表拿到物品后的获胜概率。', 21, MUTED)
    # QR includes all query conditions so the companion link opens the same analysis.
    qr = qrcode.QRCode(box_size=3, border=4, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(url); qr.make(fit=True)
    qr_image = qr.make_image(fill_color='black', back_color='white').convert('RGB')
    qr_image = qr_image.resize((128,128),Image.Resampling.NEAREST)
    im.paste(qr_image,(904,y+112))
    text(48, y+127, 'bazaarqiubot.com', 26, GOLD, True)
    text(48, y+168, fit('#bz 阵容 '+anchor['name'], 690, 22, 1)[0], 22, TEXT)
    captured = datetime.fromisoformat(scope['captured_at'].replace('Z','+00:00')).astimezone(timezone(timedelta(hours=8)))
    text(48, y+207, '数据快照 '+captured.strftime('%Y-%m-%d %H:%M')+' · 北京时间', 20, MUTED)
    text(872, y+165, '扫码查看', 22, TEXT, anchor='rt')
    text(872, y+197, '完整分析 →', 22, GOLD, anchor='rt')
    folder = Path(output) if output else OUTPUT
    folder.mkdir(parents=True, exist_ok=True)
    target = folder/('comp-'+uuid.uuid4().hex+'.png')
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text('qiubot_watermark', 'bazaarqiubot.com|single-footer-v1')
    im.save(target, pnginfo=metadata)
    return str(target.resolve())
