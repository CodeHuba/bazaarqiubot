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
    if data.get('view') == 'qq':
        return _render_families(data, output, preview=preview)
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
    return _save(im, output)


def _save(image, output):
    folder = Path(output) if output else OUTPUT
    folder.mkdir(parents=True, exist_ok=True)
    target = folder/('comp-'+uuid.uuid4().hex+'.png')
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text('qiubot_watermark', 'bazaarqiubot.com|single-footer-v1')
    image.save(target, pnginfo=metadata)
    return str(target.resolve())


def _render_families(data, output, *, preview):
    groups = selected_groups(data)
    # Keep every variant intact. Put unusually large families on additional rows.
    plans = []
    for group in groups:
        rows = [[]]
        cells = 0
        for variant in group['variants']:
            if rows[-1] and cells + len(variant) > 5:
                rows.append([])
                cells = 0
            rows[-1].append(variant)
            cells += len(variant)
        plans.append(rows)

    branch_heights = [37 + 99*len(group['branches']) if group.get('branches') else 0 for group in groups]
    group_heights = [402 + 145 * (len(rows)-1) + extra
                     for rows, extra in zip(plans, branch_heights)]
    bottom = 218 + sum(group_heights) + (75 if not groups else 0)
    canvas = Image.new('RGB', (W, bottom+53), BG)
    draw = ImageDraw.Draw(canvas)
    art_cache = {}

    def text(x, y, value, size, color=TEXT, bold=False, anchor='lt'):
        draw.text((round(x), round(y)), value, fill=color, font=font(size, bold), anchor=anchor)

    def label(x, y, value, width, size, *, bold=False, anchor='lt'):
        while size > 14 and draw.textlength(value, font=font(size, bold)) > width:
            size -= 1
        if draw.textlength(value, font=font(size, bold)) > width:
            short = value
            while short and draw.textlength(short + '…', font=font(size, bold)) > width:
                short = short[:-1]
            value = short + '…'
        text(x, y, value, size, bold=bold, anchor=anchor)

    def companion_name(x, y, name, width):
        for size in (18, 17, 16):
            if draw.textlength(name, font=font(size)) <= width:
                text(x, y, name, size, anchor='mt')
                return
        split = min(range(1, len(name)), default=1, key=lambda index: max(
            draw.textlength(name[:index], font=font(18)),
            draw.textlength(name[index:], font=font(18)),
        ))
        label(x, y, name[:split], width, 18, anchor='mt')
        label(x, y+19, name[split:], width, 18, anchor='mt')

    def art(card, x, y, width, height):
        key = card['cardId']
        if key not in art_cache:
            art_cache[key] = load_comp_art(card)
        source = art_cache[key]
        if source is None:
            return
        image = ImageOps.contain(source, (width, height), Image.Resampling.LANCZOS)
        canvas.paste(image, (round(x-image.width/2), round(y-image.height/2)), image)

    def metric(x, y, name, value, color):
        text(x, y, name, 26, MUTED, anchor='ls')
        offset = draw.textlength(name, font=font(26)) + 10
        text(x+offset, y, value, 44, color, True, 'ls')

    text(44, 34, '巴扎丘Bot · 阵容分析', 24, GOLD, True)
    if preview:
        text(1036, 34, '效果预览', 22, MUTED, anchor='rt')
    anchor_card = data['anchors'][0]
    art(anchor_card, 112, 132, 128, 124)
    label(196, 85, anchor_card['name'], 828, 57, bold=True)
    text(199, 155, scope_label(data), 24, MUTED)

    y = 215
    for group, rows, height, branch_height in zip(groups, plans, group_heights, branch_heights):
        draw.rounded_rectangle((44, y, 1036, y+height-19), radius=23, fill=PANEL, outline=LINE)
        label(76, y+27, group['hero_name'], 250, 29, bold=True)
        metric(340, y+56, '出场局数', f"{group['count']:,}", GOLD)
        metric(712, y+56, '10 胜率', f"{group['rate']:.1%}", GREEN)

        for row_index, variants in enumerate(rows):
            top = y + 145*row_index
            # A single combination may itself span most of the panel.
            cards = sum(map(len, variants))
            spacing = min(10, 900 // (2 * cards))
            cell = min(142, (920 - spacing*(len(variants)-1) - 14*len(variants)) / cards)
            widths = [len(variant)*cell+14 for variant in variants]
            x = (W-sum(widths)-spacing*(len(variants)-1))/2
            for variant, width in zip(variants, widths):
                draw.rounded_rectangle((x, top+72, x+width, top+214), radius=14, fill='#131e2c')
                for index, card in enumerate(variant):
                    center = x+7+(index+.5)*cell
                    art(card, center, top+131, max(1, min(120, int(cell)-24)), 105)
                    label(center, top+188, card['name'], max(15, cell-7), 23, bold=True, anchor='mt')
                    if index:
                        text(center-cell/2, top+132, '+', 22, GOLD, anchor='mm')
                x += width+spacing

        section = y+145*(len(rows)-1)
        if branch_height:
            draw.line((76, section+224, 1004, section+224), fill=LINE)
            text(76, section+234, '组内变种', 22, MUTED, True)
            for index, branch in enumerate(group['branches']):
                top = section+263+99*index
                draw.rounded_rectangle((76, top, 1004, top+90), radius=13, fill='#131e2c')
                for position, card in enumerate([anchor_card, *branch['cards']]):
                    center = 137+125*position
                    art(card, center, top+32, 96, 55)
                    label(center, top+64, card['name'], 116, 19, bold=True, anchor='mt')
                    if position:
                        text(center-62.5, top+32, '+', 20, GOLD, anchor='mm')
                text(645, top+35, f"{branch['count']:,} 局", 26, GOLD, True)
                text(795, top+38, f"10 胜率 {branch['rate']:.1%}", 23, GREEN)
            section += branch_height
        draw.line((76, section+224, 1004, section+224), fill=LINE)
        for x, name, key in ((76, '高频关联牌', 'related_items'), (554, '高频关联技能', 'related_skills')):
            cards = group[key]
            if not cards:
                continue
            text(x, section+234, name, 22, MUTED, True)
            for index, card in enumerate(cards):
                center = x+43+91*index
                art(card, center, section+295, 83, 69)
                companion_name(center, section+336, card['name'], 88)
        y += height

    if not groups:
        text(48, y+10, '当前筛选下暂无足够样本的核心组合' if data['baseline']['count'] else
             '当前版本及筛选条件下暂无数据', 29, MUTED)
    text(1036, bottom+13, 'bazaarqiubot.com', 23, MUTED, True, 'rt')
    return _save(canvas, output)
