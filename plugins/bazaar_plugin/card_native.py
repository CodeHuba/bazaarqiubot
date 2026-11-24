"""Native-style encyclopedia cards, with measured text and lossless pagination."""
from __future__ import annotations

from functools import lru_cache
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from PIL import Image, ImageDraw, ImageFont, ImageOps, PngImagePlugin

VERSION = 'native-card-2'
WIDTH, MAX_HEIGHT, SCALE = 1200, 2300, 2
CREAM, MUTED, GOLD = '#f6ead0', '#c7b48e', '#b99a56'
QUALITY_COLORS = {'Bronze': '#c79460', 'Silver': '#d0d7df', 'Gold': '#eac36e',
                  'Diamond': '#83e0ec', 'Legendary': '#efa46b'}
KEYWORDS = {'生命再生': '#8cd2a5', '灼烧': '#ff9b4e', '暴怒': '#ff9b4e', '冻结': '#63d9ef',
            '剧毒': '#a4cf73', '减速': '#c9a0e1', '伤害': '#f48165', '节奏': '#d4b0ed',
            '护盾': '#8ebbf3', '加速': '#edcf78'}
TOKEN = re.compile(r'最大生命值|生命再生|生命值|[-+]?\d+(?:\.\d+)?(?:秒|件|%|点)?|[^\d]')


@lru_cache(maxsize=32)
def load_font(path, size):
    return ImageFont.truetype(path, round(size*SCALE))


class Canvas:
    def __init__(self, height, font_path):
        self.font_path = font_path
        self.image = Image.new('RGB', (WIDTH*SCALE, height*SCALE), '#20180f')
        self.draw = ImageDraw.Draw(self.image)

    def font(self, size):
        return load_font(self.font_path, size)

    def measure(self, value, size):
        return self.draw.textlength(value, font=self.font(size))/SCALE

    def text(self, x, y, value, size=37, color=CREAM):
        self.draw.text((round(x*SCALE), round(y*SCALE)), value, font=self.font(size), fill=color)

    def polygon(self, points, color):
        self.draw.polygon([(round(x*SCALE), round(y*SCALE)) for x, y in points], fill=color)

    def line(self, points, color=GOLD, width=1):
        self.draw.line([(round(x*SCALE), round(y*SCALE)) for x, y in points], fill=color, width=round(width*SCALE))

    def frame(self, rect, color='#d5b269'):
        x, y, right, bottom = rect
        for inset, tint in [(0, color), (2, '#160f08'), (7, '#765525'), (9, '#20190f')]:
            self.polygon(octagon((x+inset, y+inset, right-inset, bottom-inset)), tint)
        for cx, cy in [(x+8, y+33), (right-8, bottom-33)]:
            self.draw.arc(tuple(round(v*SCALE) for v in (cx-7, cy-7, cx+7, cy+7)), 0, 310, fill=color, width=2*SCALE)

    def inline(self, x, y, chunks, size):
        for value, color in chunks:
            self.text(x, y, value, size, color)
            x += self.measure(value, size)

    def rows(self, rows, x, y, size, leading):
        for row in rows:
            self.inline(x, y, row, size)
            y += leading


def octagon(rect, bevel=15):
    x, y, right, bottom = rect
    return [(x+bevel, y), (right-bevel, y), (right, y+bevel), (right, bottom-bevel),
            (right-bevel, bottom), (x+bevel, bottom), (x, bottom-bevel), (x, y+bevel)]


def wrap(canvas, chunks, width, size):
    """Wrap Chinese text while keeping decimal numbers and quality sequences intact."""
    groups, index = [], 0
    while index < len(chunks):
        value, color = chunks[index]
        if re.fullmatch(r'[-+]?\d+(?:\.\d+)?%?', value) and index+1 < len(chunks) and chunks[index+1][0] == ' / ':
            group = [chunks[index]]
            index += 1
            while index+1 < len(chunks) and chunks[index][0] == ' / ':
                group.extend(chunks[index:index+2])
                index += 2
            groups.append(group)
            continue
        groups.extend([(token, color)] for token in TOKEN.findall(value))
        index += 1
    rows, current, used = [], [], 0
    for group in groups:
        if len(group) == 1 and group[0][0] == '\n':
            rows.append(current)
            current, used = [], 0
            continue
        length = sum(canvas.measure(value, size) for value, _ in group)
        if current and used+length > width:
            rows.append(current)
            current, used = [], 0
        current.extend(group)
        used += length
    if current or not rows:
        rows.append(current)
    return rows


def tint(chunks, sheet):
    result = []
    for value, color in chunks:
        value = value.replace('生命值最大生命值', '最大生命值')
        if color != sheet.TEXT:
            result.append((value, color))
            continue
        for part in re.split('('+'|'.join(KEYWORDS)+')', value):
            result.append((part, KEYWORDS.get(part, CREAM)))
    return result


def values_chunks(values, variants):
    if len(set(values)) == 1:
        return [(values[0], CREAM)]
    chunks = []
    for index, (value, variant) in enumerate(zip(values, variants)):
        if index:
            chunks.append((' / ', MUTED))
        chunks.append((value, QUALITY_COLORS.get(variant['quality'], GOLD)))
    return chunks


def content_blocks(document, canvas, sheet):
    variants = document['variants']
    qualities = [v['quality'] for v in variants]
    effects = [v['effects'] for v in variants]
    if document['cardId'] == '719c5db8-85ff-41ca-8494-25741c8c5f1d':
        # Its third ability pays twice TempoCost; the official placeholder
        # supplies the number while omitting the Tempo unit.
        effects = deepcopy(effects)
        for group in effects:
            if len(group) > 2:
                group[2]['text'] = re.sub(r'^(\d+)：', r'\1节奏：', group[2]['text'])
    merged = sheet._segments(effects, qualities)
    cooldown = next((r for r in document['comparison'] if r['label'] == '冷却'), None)
    rows = merged if merged is not None else [
        (effect.get('type'), [(variant['qualityLabel']+'：'+sheet.effect_text(effect), sheet.TEXT)])
        for variant in variants for effect in variant['effects']]
    grouped_rows = []
    for index, (kind, chunks) in enumerate(rows):
        condition = effects[0][index].get('condition', '') if merged is not None else ''
        match = re.match(r'任务\s*(\d+)', condition)
        quest = match[1] if match else None
        if quest and grouped_rows and grouped_rows[-1][2] == quest:
            grouped_rows[-1][1].extend([('\n', sheet.TEXT), *chunks])
        else:
            grouped_rows.append((kind, list(chunks), quest))
    blocks = []
    for index, (kind, chunks, _) in enumerate(grouped_rows):
        # A cooldown badge belongs to the first active tooltip, never a passive effect.
        clock = cooldown if index == 0 and kind == 'Active' else None
        x = 497 if clock else 400
        lines = wrap(canvas, tint(chunks, sheet), 1122-x, 37)
        for start in range(0, len(lines), 22):
            part = lines[start:start+22]
            blocks.append(dict(area='body', rows=part, x=x, size=37, leading=58,
                               height=max(194 if clock and not start else 134, len(part)*58+62),
                               clock=clock if not start else None))
    if not blocks:
        blocks.append(dict(area='body', rows=[[('暂无效果资料', MUTED)]], x=400, size=37, leading=58, height=150))

    # Preserve attributes that are not already represented by an effect's values.
    effects = [v['effects'] for v in variants]
    covered = []
    if len({len(group) for group in effects}) == 1:
        for corresponding in zip(*effects):
            numbers = [re.findall(sheet.NUMBERS, sheet.effect_text(e)) for e in corresponding]
            if len({len(group) for group in numbers}) == 1:
                covered.extend([list(values) for values in zip(*numbers)])
    full_text = ' '.join(sheet.effect_text(e) for e in variants[0]['effects'])
    extras = []
    for attr in document['comparison']:
        if attr['label'] == '冷却' and any(b.get('clock') for b in blocks):
            continue
        numbers = [re.sub(r'[^\d.+-]', '', value) for value in attr['values']]
        semantic = re.sub(r'(目标|时长)$', '', attr['label'])
        if attr['label'] != '冷却' and (numbers in covered or len(set(numbers)) == 1 and semantic in full_text):
            continue
        extras.append((attr['label'], values_chunks(attr['values'], variants)))

    quality = document['selectedQuality'] or document['startingQuality']
    meta = [('所选品质' if document['selectedQuality'] else '起始品质',
             [(sheet.QUALITY_LABELS.get(quality, quality), QUALITY_COLORS.get(quality, GOLD))])]
    if document['selectedEnchantment']:
        meta.append(('附魔', [(document['selectedEnchantment'], GOLD)]))
    if document['type'] == 'Skill':
        affiliation = sheet.skill_affiliation(document) if hasattr(sheet, 'skill_affiliation') else sheet.hero_label(document)
        meta.append(('归属标签', [(affiliation, CREAM)]))
    elif canvas.measure('、'.join(document['heroes']), 25) > 210:
        meta.append(('适用英雄', [('、'.join(document['heroes']), CREAM)]))
    meta.append(('隐藏标签', [(' · '.join(document.get('hiddenTags') or []) or '无', CREAM)]))
    meta.extend(extras)
    for label, chunks in meta:
        lines = wrap(canvas, chunks, 830, 32)
        for start in range(0, len(lines), 22):
            part = lines[start:start+22]
            blocks.append(dict(area='meta', label=label, rows=part, x=276, size=32, leading=52, height=len(part)*52+26))

    for enchant in document['enchants']:
        if hasattr(sheet, '_enchant_rows'):
            descriptions = sheet._enchant_rows(enchant)
        else:
            descriptions = [[(sheet.QUALITY_LABELS.get(g['quality'], '')+'：'+sheet.effect_text(e), sheet.TEXT)]
                            for g in enchant['groups'] for key in ('effects', 'changes') for e in g[key]]
        if not descriptions:
            descriptions = [[('暂无附魔效果资料', MUTED)]]
        if enchant.get('hiddenTags'):
            descriptions.append([('新增隐藏标签：'+' · '.join(enchant['hiddenTags']), sheet.TEXT)])
        lines = []
        for chunks in descriptions:
            lines.extend(wrap(canvas, tint(chunks, sheet), 710, 34))
        for start in range(0, len(lines), 20):
            part = lines[start:start+20]
            blocks.append(dict(area='enchant', label=enchant['label']+'附魔', rows=part, x=400, size=34,
                               leading=54, height=len(part)*54+100))
    if document['showEnchants'] and not document['enchants']:
        blocks.append(dict(area='enchant', label='附魔', rows=[[('该卡牌没有可用附魔', MUTED)]],
                           x=400, size=34, leading=54, height=154))
    return blocks


def plan(document, canvas, sheet):
    labels = [('技能' if document['type'] == 'Skill' else {'Small': '小型', 'Medium': '中型', 'Large': '大型'}.get(document['size'], document['size']))]
    labels.extend(document['tags'])
    tabs, x, y = [], 385, 94
    for label in labels:
        # Long labels remain complete through wrapping within the tab.
        lines = wrap(canvas, [(label, CREAM)], 680, 29)
        for row in lines:
            title = ''.join(t for t, _ in row)
            width = canvas.measure(title, 29)+46
            if x+width > 1153:
                x, y = 385, y+54
            tabs.append((x, y, width, title))
            x += width+10
    header = y+49
    pages, current, bottom, previous = [], [], header+96, ''
    for block in content_blocks(document, canvas, sheet):
        if block['area'] == 'meta' and previous != 'meta' and not pages:
            bottom = max(bottom, 610)
        gap = 44 if block['area'] == 'meta' and previous != 'meta' else 0
        new_page = block['area'] == 'enchant' and previous != 'enchant'
        if current and (new_page or bottom+gap+block['height']+80 > MAX_HEIGHT):
            pages.append(current)
            current, bottom, previous = [], header+96, ''
            gap = 44 if block['area'] == 'meta' else 0
        block = {**block, 'y': bottom+gap}
        current.append(block)
        bottom = block['y']+block['height']
        previous = block['area']
    if current:
        pages.append(current)
    return tabs, header, pages


def draw_art(canvas, art, document, color):
    size = document['size'] if document['type'] == 'Item' else 'Skill'
    width, height = {'Small': (212, 424), 'Medium': (270, 270), 'Large': (285, 190), 'Skill': (258, 258)}.get(size, (270, 270))
    x, y = 39+(285-width)/2, 173 if size == 'Small' else 236
    canvas.polygon(octagon((x-12, y-12, x+width+12, y+height+12)), '#090908')
    canvas.polygon(octagon((x-8, y-8, x+width+8, y+height+8)), color)
    canvas.polygon(octagon((x-3, y-3, x+width+3, y+height+3)), '#413929')
    if art is None:
        canvas.text(x+20, y+height/2-20, '图片暂缺', 28, MUTED)
        return
    layer = ImageOps.contain(art.convert('RGBA'), (round(width*SCALE), round(height*SCALE)), Image.Resampling.LANCZOS)
    canvas.image.paste(layer, (round((x+width/2)*SCALE-layer.width/2), round((y+height/2)*SCALE-layer.height/2)), layer)
    canvas.line([(x-5, y-4), (x+width+5, y-4)], '#efe0b8', 2)


def draw_clock(canvas, clock, y, variants):
    cx, cy = 363, y+94
    canvas.polygon([(cx-57, cy-51), (cx+48, cy-52), (cx+85, cy), (cx+45, cy+57), (cx-62, cy+55)], '#7c5a28')
    for radius, color in [(68, '#d5b269'), (64, '#59401b'), (57, '#bb9650'), (53, '#17150f'), (47, '#292820')]:
        canvas.draw.ellipse(((cx-radius)*SCALE, (cy-radius)*SCALE, (cx+radius)*SCALE, (cy+radius)*SCALE), fill=color)
    values = [v.removesuffix('秒') for v in clock['values']]
    number = values[0] if len(set(values)) == 1 else '/'.join(values)
    size = 48
    while size > 16 and canvas.measure(number, size)+28 > 110:
        size -= 1
    width = canvas.measure(number, size)
    x, baseline = cx-(width+28)/2, (cy+18)*SCALE
    canvas.draw.text((x*SCALE, baseline), number, font=canvas.font(size), fill=CREAM, anchor='ls')
    canvas.draw.text(((x+width+5)*SCALE, baseline), '秒', font=canvas.font(23), fill=MUTED, anchor='ls')


def render_native(document, output, cache=None):
    from . import card_sheet as sheet
    font_path = sheet._font_path(True)
    measure = Canvas(1, font_path)
    tabs, header, pages = plan(document, measure, sheet)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    fingerprint = hashlib.sha256((VERSION+font_path+json.dumps(document, ensure_ascii=False, sort_keys=True)).encode()).hexdigest()[:24]
    targets = [output/f'{fingerprint}-native-{i+1}.png' for i in range(len(pages))]
    art = sheet.load_art(document['art'], document['size'] if document['type'] == 'Item' else None,
                         **({'cache': cache} if cache is not None else {}))
    if art is not None and all(p.is_file() for p in targets):
        return [str(p.resolve()) for p in targets]
    quality = document['selectedQuality'] or document['startingQuality']
    for page_index, (blocks, target) in enumerate(zip(pages, targets)):
        height = max(blocks[-1]['y']+blocks[-1]['height']+80, 720 if page_index == 0 else 0)
        canvas = Canvas(height, font_path)
        for y in range(height*SCALE):
            t = y/(height*SCALE)
            canvas.draw.line((0, y, WIDTH*SCALE, y), fill=(int(49-26*t), int(37-19*t), int(25-12*t)))
        caption = '#bz db '+document['name']+(' -e' if document['showEnchants'] else '')
        caption_rows = wrap(canvas, [(caption, MUTED)], 950, 24)
        canvas.rows(caption_rows[:1], 48, 28, 24, 32)
        if len(pages) > 1:
            canvas.text(1045, 28, f'{page_index+1}/{len(pages)}', 24, MUTED)
        for x, y, width, label in tabs:
            canvas.polygon(octagon((x, y, x+width, y+60), 9), '#a78849')
            canvas.polygon(octagon((x+3, y+3, x+width-3, y+61), 7), '#21190e')
            canvas.text(x+23, y+9, label, 29, '#e5cf9c')
        body = [b for b in blocks if b['area'] != 'meta']
        bottom = max([b['y']+b['height'] for b in body]+[610 if page_index == 0 else header+96])
        canvas.frame((362, header, 1153, bottom))
        canvas.line([(371, header+96), (1143, header+96)], '#b29556', 2)
        heroes = '、'.join(document['heroes'])
        hero_width = canvas.measure(heroes, 25) if document['type'] == 'Item' else 0
        title_width = 690-hero_width if hero_width <= 210 else 710
        title_size = 48
        while title_size > 16 and canvas.measure(document['name'], title_size) > title_width:
            title_size -= 1
        canvas.text(399, header+24, document['name'], title_size)
        if 0 < hero_width <= 210:
            canvas.text(1114-hero_width, header+42, heroes, 25, '#c4a466')
        if page_index == 0:
            draw_art(canvas, art, document, QUALITY_COLORS.get(quality, GOLD))
        for index, block in enumerate(body):
            y = block['y']
            if index:
                canvas.line([(371, y), (1143, y)], '#b29556', 2)
            if block.get('clock'):
                draw_clock(canvas, block['clock'], y, document['variants'])
            if block['area'] == 'enchant':
                canvas.text(400, y+20, block['label'], 29, GOLD)
            canvas.rows(block['rows'], block['x'], y+(72 if block['area'] == 'enchant' else 36), block['size'], block['leading'])
        metadata = [b for b in blocks if b['area'] == 'meta']
        if metadata:
            canvas.frame((47, metadata[0]['y'], 1153, metadata[-1]['y']+metadata[-1]['height']))
            for index, block in enumerate(metadata):
                if index:
                    canvas.line([(64, block['y']), (1135, block['y'])], '#65502b')
                canvas.text(77, block['y']+23, block['label'], 29, MUTED)
                canvas.rows(block['rows'], 276, block['y']+18, block['size'], block['leading'])
        canvas.text(48, height-50, 'bazaarqiubot.com', 24, '#a18e68')
        fd, filename = tempfile.mkstemp(dir=output, suffix='.png')
        os.close(fd)
        try:
            # Render large internally, then downsample once for crisp outlines.
            info = PngImagePlugin.PngInfo()
            info.add_text('qiubot_watermark', 'bazaarqiubot.com|single-footer-v1')
            canvas.image.resize((WIDTH, height), Image.Resampling.LANCZOS).save(filename, pnginfo=info)
            Path(filename).replace(target)
        finally:
            Path(filename).unlink(missing_ok=True)
    return [str(p.resolve()) for p in targets]
