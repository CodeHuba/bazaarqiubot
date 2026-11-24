"""Official card encyclopedia sheets: all qualities, native art, paginated enchants."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from . import gamedata_client as game
from .card_preview import CardPreviewRepository, QUALITY_LABELS, _apply_self_auras
from .monster_data import SOURCES
from .monster_image import Sheet, COLORS, TEXT, MUTED, LINE, GOLD, load_art, _font_path

OUTPUT = Path(__file__).resolve().parents[2]/'data/charts/cards'
VERSION = 'card-sheet-4'
ENCHANT_COLORS = {'Golden': GOLD, 'Heavy': '#b8aaa0', 'Icy': '#79d7ed', 'Turbo': '#9fdfb1',
                  'Shielded': '#8ab8f2', 'Restorative': '#89d4ba', 'Toxic': '#b6c97a', 'Fiery': '#f18a58',
                  'Shiny': '#f0d984', 'Deadly': '#e69ab4', 'Radiant': '#f5ddb0', 'Obsidian': '#b3a0df', 'Mossy': '#92c997'}
NUMBERS = r'(?<![0-9.])[-+]?\d+(?:\.\d+)?%?(?![0-9.])'
EXTRA_ATTRIBUTES = {'FreezeTargets': '冻结目标', 'FreezeAmount': '冻结时长', 'SlowTargets': '减速目标',
                    'SlowAmount': '减速时长', 'HasteTargets': '加速目标', 'HasteAmount': '加速时长',
                    'ChargeAmount': '充能时长'}


def resolve_option(value, choices, label):
    if not value:
        return ''
    matched = next((c['value'] for c in choices if str(value).casefold() in (c['value'].casefold(), c['label'].casefold())), None)
    if matched is None:
        raise ValueError(f"不支持的{label}『{value}』，可选："+'、'.join(c['label'] for c in choices))
    return matched


def build_document(raw, database, quality='', enchantment='', show_enchants=False, repository=None):
    repo = repository or CardPreviewRepository(database)
    default = repo.get(raw['Id'])
    quality = resolve_option(quality, default['availableQualities'], '品质')
    enchantment = resolve_option(enchantment, default['availableEnchantments'], '附魔')
    if show_enchants and enchantment:
        raise ValueError('请用 -e 查看全部附魔，或用 --附魔 指定一种附魔。')
    def read_variant(q, enchant):
        variant = deepcopy(repo.get(raw['Id'], q, enchant))
        attrs = game.build_tier_attrs(raw, q)
        attrs.update(((raw.get('Enchantments') or {}).get(enchant) or {}).get('Attributes') or {})
        active_ids = (raw.get('Tiers') or {}).get(q, {}).get('AuraIds')
        auras = {key: aura for key, aura in (raw.get('Auras') or {}).items() if active_ids is None or key in active_ids}
        auras.update(((raw.get('Enchantments') or {}).get(enchant) or {}).get('Auras') or {})
        attrs, _ = _apply_self_auras(attrs, auras)
        if attrs.get('Lifesteal') and not any(e['text'] == '吸血' for e in variant['effects']):
            variant['effects'].append({'text': '吸血', 'type': 'Passive', 'source': 'keyword'})
        return variant, attrs
    selected = [read_variant(q, enchantment) for q in ([quality] if quality else [q['value'] for q in default['availableQualities']])]
    variants = [v for v, _ in selected]
    extras = []
    for variant, attrs in selected:
        values = {a['key']: (a['label'], a['value']) for a in variant['attributes']}
        for key, label in EXTRA_ATTRIBUTES.items():
            if attrs.get(key):
                value = game.fmt_val(attrs[key], key)+('秒' if key in game.MS_ATTRS else '件')
                values[key] = (label, value)
        extras.append(values)
    keys = list(dict.fromkeys(key for attributes in extras for key in attributes))
    comparison = [{'label': next(v[key][0] for v in extras if key in v),
                   'values': [v.get(key, ('', '—'))[1] for v in extras]} for key in keys]
    # Existing verified art mappings also cover skills absent from the generic image index.
    overrides = json.loads(SOURCES.read_text(encoding='utf-8')).get('cards', {}) if SOURCES.is_file() else {}
    override = overrides.get(raw['Id']) or {}
    art = override.get('art') if override.get('art_key') == raw.get('ArtKey') else default.get('img')
    heroes = []
    for hero in raw.get('Heroes') or []:
        translated = repo._translate({'Text': hero}, 'zh-CN')
        heroes.append('通用' if hero == 'Common' else game.HERO_ZH.get(hero, hero) if translated == hero else translated)
    enchants = []
    if show_enchants:
        for option in default['availableEnchantments']:
            groups = []
            for base in variants:
                selected, _ = read_variant(base['quality'], option['value'])
                effects = [e for e in selected['effects'] if e['source'] == 'enchantment']
                old_base = [e for e in base['effects'] if e['source'] != 'enchantment']
                new_base = [e for e in selected['effects'] if e['source'] != 'enchantment']
                # Compare complete effect lists; quests/conditional effects stay attached.
                old_keys = {(e['text'], e.get('condition', ''), e.get('type')) for e in old_base}
                changes = [e for e in new_base if (e['text'], e.get('condition', ''), e.get('type')) not in old_keys]
                groups.append({'quality': base['quality'], 'effects': effects, 'changes': changes})
            enchants.append({**option, 'groups': groups,
                             'hiddenTags': [tag for tag in selected['hiddenTags'] if tag not in variants[0]['hiddenTags']]})
    selected_name = next((c['label'] for c in default['availableEnchantments'] if c['value'] == enchantment), '')
    return {'cardId': raw['Id'], 'name': default['name'], 'type': default['type'], 'size': default['size'],
            'heroes': heroes or ['资料暂缺'], 'allHeroes': 'Common' in (raw.get('Heroes') or []),
            'tags': variants[0]['tags'], 'hiddenTags': variants[0]['hiddenTags'], 'art': art or '',
            'startingQuality': raw.get('StartingTier') or default['quality'], 'selectedQuality': quality,
            'selectedEnchantment': selected_name, 'variants': variants, 'comparison': comparison,
            'enchants': enchants, 'showEnchants': show_enchants,
            'stamp': hashlib.sha256((default['version']+json.dumps([raw['Id'], quality, enchantment, show_enchants, art], ensure_ascii=False)).encode()).hexdigest()[:24]}


def effect_text(effect):
    return (effect.get('condition', '')+'：' if effect.get('condition') else '')+effect['text']


def hero_label(document):
    return '全部英雄（通用）' if document.get('allHeroes') else '、'.join(document['heroes'])


def format_document_text(document):
    lines = [document['name']]
    if document['type'] == 'Skill':
        lines.append('适用英雄：'+hero_label(document))
    lines.append('隐藏标签：'+('、'.join(document.get('hiddenTags') or []) or '无'))
    for variant in document['variants']:
        lines.append('［'+variant['qualityLabel']+'］')
        lines.extend(effect_text(e) for e in variant['effects'])
    for enchant in document['enchants']:
        lines.append('\n附魔：'+enchant['label'])
        if enchant.get('hiddenTags'):
            lines.append('新增隐藏标签：'+'、'.join(enchant['hiddenTags']))
        seen = set()
        for group in enchant['groups']:
            contents = [effect_text(e) for e in group['effects']]+['原有效果变更：'+effect_text(e) for e in group['changes']]
            for content in contents:
                key = (group['quality'], content)
                if key not in seen:
                    lines.append(QUALITY_LABELS.get(group['quality'], '')+'：'+content)
                    seen.add(key)
    return '\n'.join(lines)


def _segments(effects, qualities):
    """Merge only equivalent structures, retaining every quality's number and color."""
    if not effects or any(len(v) != len(effects[0]) for v in effects):
        return None
    result = []
    for corresponding in zip(*effects):
        first = corresponding[0]
        metadata = lambda e: (e.get('condition', ''), e.get('type'), e.get('source'))
        if any(metadata(e) != metadata(first) for e in corresponding):
            return None
        texts = [effect_text(e) for e in corresponding]
        templates = [re.sub(NUMBERS, '{n}', t) for t in texts]
        if len(set(templates)) != 1:
            return None
        if len(set(texts)) == 1:
            result.append((first.get('type'), [(texts[0], TEXT)]))
            continue
        numbers = [list(re.finditer(NUMBERS, text)) for text in texts]
        chunks, cursor = [], 0
        for index, match in enumerate(numbers[0]):
            chunks.append((texts[0][cursor:match.start()], TEXT))
            values = [row[index].group() for row in numbers]
            if len(set(values)) == 1:
                chunks.append((values[0], TEXT))
            else:
                for vi, value in enumerate(values):
                    if vi:
                        chunks.append((' / ', MUTED))
                    chunks.append((value, COLORS.get(qualities[vi], GOLD)))
            cursor = match.end()
        chunks.append((texts[0][cursor:], TEXT))
        result.append((first.get('type'), chunks))
    return result


def _wrap_chunks(sheet, chunks, width=1130, size=29):
    lines, current, used = [], [], 0
    for text, color in chunks:
        for char in text:
            char_width = sheet.draw.textlength(char, font=sheet.font(size))
            if char == '\n' or used+char_width > width:
                lines.append(current)
                current, used = [], 0
                if char == '\n':
                    continue
            if current and current[-1][1] == color:
                current[-1] = (current[-1][0]+char, color)
            else:
                current.append((char, color))
            used += char_width
    lines.append(current)
    return lines


def _blocks(document):
    measure = Sheet(10)
    blocks = []
    tag_rows, row, used = [], [], 0
    for tag in document.get('hiddenTags') or ['无']:
        width = measure.draw.textlength(tag, font=measure.font(24, True))+32
        if row and used+width > 1248:
            tag_rows.append(row)
            row, used = [], 0
        row.append(tag)
        used += width+14
    if row:
        tag_rows.append(row)
    for start in range(0, len(tag_rows), 24):
        rows = tag_rows[start:start+24]
        blocks.append({'kind': 'tags', 'rows': rows, 'height': 38+len(rows)*60, 'title': '隐藏标签'})
    variants = document['variants']
    if document['comparison'] and (len(variants) > 1 or document['type'] == 'Item'):
        for start in range(0, len(document['comparison']), 8):
            rows = document['comparison'][start:start+8]
            blocks.append({'kind': 'table', 'rows': rows, 'height': 84+80*len(rows), 'title': '品质对比' if not start else '品质对比（续）'})
    combined = _segments([v['effects'] for v in variants], [v['quality'] for v in variants])
    effect_rows = []
    if combined is not None:
        effect_rows = [(kind, '', chunks) for kind, chunks in combined]
    else:
        for variant in variants:
            for effect in variant['effects']:
                effect_rows.append((effect.get('type'), variant['qualityLabel'], [(effect_text(effect), TEXT)]))
    for kind, quality_label, chunks in effect_rows:
        lines = _wrap_chunks(measure, chunks, 1120, 29)
        # Even a single unusually long tooltip may span pages, without truncation.
        for start in range(0, len(lines), 32):
            part = lines[start:start+32]
            blocks.append({'kind': 'effect', 'effect_type': kind, 'quality_label': quality_label,
                           'lines': part, 'height': 42+len(part)*40,
                           'title': '技能效果' if document['type'] == 'Skill' else '卡牌效果'})
    if not effect_rows:
        blocks.append({'kind': 'effect', 'effect_type': 'Passive', 'quality_label': '',
                       'lines': [[('暂无效果资料', MUTED)]], 'height': 92, 'title': '卡牌效果'})
    for enchant in document['enchants']:
        groups = enchant['groups']
        signature = lambda g: json.dumps([g['effects'], g['changes']], ensure_ascii=False, sort_keys=True)
        same = len({signature(g) for g in groups}) == 1
        lines = []
        for group in (groups[:1] if same else groups):
            prefix = '' if same else QUALITY_LABELS.get(group['quality'], '')+'：'
            for effect in group['effects']:
                lines.extend(_wrap_chunks(measure, [(prefix+effect_text(effect), TEXT)], 990, 28))
            for effect in group['changes']:
                lines.extend(_wrap_chunks(measure, [('原有效果变更：'+prefix+effect_text(effect), '#9fdfb1')], 990, 27))
        if not lines:
            lines = [[('暂无附魔效果资料', MUTED)]]
        if enchant.get('hiddenTags'):
            lines.extend(_wrap_chunks(measure, [('新增隐藏标签：'+' · '.join(enchant['hiddenTags']), '#8ab8f2')], 990, 28))
        for start in range(0, len(lines), 32):
            part = lines[start:start+32]
            blocks.append({'kind': 'enchant', 'label': enchant['label'], 'value': enchant['value'], 'lines': part,
                           'height': max(90, 36+len(part)*40), 'title': '附魔总览'})
    if document['showEnchants'] and not document['enchants']:
        blocks.append({'kind': 'effect', 'effect_type': '', 'quality_label': '',
                       'lines': [[('该卡牌没有可用附魔', MUTED)]], 'height': 92, 'title': '附魔总览'})
    return blocks


def paginate(document, maximum=2400):
    pages, current = [], {'full': True, 'blocks': [], 'end': 620}
    previous = ''
    for block in _blocks(document):
        section = block['title'] != previous
        needed = block['height']+14+(64 if section else 0)
        # Put enchant summaries on their own pages, as in the approved -e sample.
        new_enchants = block['kind'] == 'enchant' and previous != block['title']
        if current['blocks'] and (current['end']+needed+120 > maximum or new_enchants):
            pages.append(current)
            current = {'full': False, 'blocks': [], 'end': 342}
            section = True
        placed = {**block, 'section': section, 'y': current['end']+(64 if section else 0)}
        current['blocks'].append(placed)
        current['end'] = placed['y']+block['height']+14
        previous = block['title']
    pages.append(current)
    return pages


def _badge(sheet, x, y, label, color=GOLD, size=24):
    width = sheet.draw.textlength(label, font=sheet.font(size, True))+32
    sheet.panel((x, y, x+width, y+46), '#263143', 11, color)
    sheet.text(x+16, y+6, label, size, color, True)
    return width


def _draw_chunks(sheet, x, y, line, size):
    for value, color in line:
        sheet.text(x, y, value, size, color)
        x += sheet.draw.textlength(value, font=sheet.font(size))


def render_document(document, output=OUTPUT, cache=None):
    from .card_native import render_native
    return render_native(document, output, cache)
