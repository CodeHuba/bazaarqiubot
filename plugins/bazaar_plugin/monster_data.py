"""Versioned monster/encounter joins using official game text and instance tiers."""
from __future__ import annotations

from contextlib import closing
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import threading

from . import gamedata_client as game
from .card_preview import CardPreviewRepository, build_preview, _plain

SOURCES = Path(__file__).with_name('monster_sources.json')


def _signature(path):
    try:
        stat = path.stat()
        return f'{path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}'
    except OSError:
        return str(path) + ':missing'


class MonsterRepository:
    def __init__(self, database, translations=None, images=None, sources=SOURCES):
        self.database = Path(database)
        self.preview = CardPreviewRepository(self.database, translations, images)
        self.sources = Path(sources)
        self._lock = threading.RLock()
        self._stamp = ''
        self._entries = []
        self._cards = {}
        self._art = {}

    def _refresh(self):
        self.preview._refresh()
        stamp = '|'.join((_signature(self.database), self.preview._stamp, _signature(self.sources)))
        if stamp == self._stamp:
            return
        with closing(sqlite3.connect(self.database.resolve().as_uri()+'?mode=ro', uri=True)) as db:
            cards = [json.loads(row[0]) for row in db.execute('SELECT Data FROM cards')]
            monsters = {m['Id']: m for row in db.execute('SELECT Data FROM monsters') for m in [json.loads(row[0])]}
        sources = json.loads(self.sources.read_text(encoding='utf-8')) if self.sources.is_file() else {}
        self._cards = {c['Id']: c for c in cards if c.get('Type') in ('Item', 'Skill')}
        entries = []
        for encounter in cards:
            combatant = encounter.get('CombatantType') or {}
            monster = monsters.get(combatant.get('MonsterTemplateId'))
            if encounter.get('Type') != 'CombatEncounter' or not monster:
                continue
            player = monster.get('Player') or {}
            attrs = player.get('Attributes') or {}
            level = combatant.get('Level') or attrs.get('Level')
            loc = (encounter.get('Localization') or {}).get('Title') or {}
            name_en = loc.get('Text') or monster.get('InternalName', '')
            name = _plain(self.preview._translate(loc or {'Text': name_en}, 'zh-CN'))
            aliases = {game.normalize_card_name(n) for n in (name, name_en, encounter.get('InternalName'), monster.get('InternalName')) if n}
            matches = [s for s in sources.get('encounters', [])
                       if game.normalize_card_name(s['name']) in aliases
                       and s.get('level') == level and s.get('tier') == encounter.get('StartingTier')]
            source = matches[0] if len(matches) == 1 else {}
            entries.append({'id': encounter['Id'], 'name': name, 'name_en': name_en, 'aliases': sorted(aliases),
                            'level': level, 'health': attrs.get('HealthMax'), 'tier': encounter.get('StartingTier'),
                            'day': source.get('day') or '', 'art': source.get('art') or '',
                            'source': source.get('url') or '', 'source_date': sources.get('retrieved_at', ''),
                            'gold': encounter.get('RewardCombatGold'), 'xp': encounter.get('RewardCombatXp'),
                            'sandstorm': encounter.get('SandstormEnabled'), 'player': player,
                            'board_override': encounter.get('BoardOverride'), 'version': encounter.get('Version', '')})
        self._entries, self._art = entries, sources.get('cards', {})
        self._stamp = stamp

    def search(self, name, exact=True):
        with self._lock:
            self._refresh()
            query = game.normalize_card_name(name)
            if not query:
                return []
            found = [e for e in self._entries if any(query == alias if exact else query in alias for alias in e['aliases'])]
            return [deepcopy(e) for e in sorted(found, key=lambda e: (e['name'], str(e['level']), e['id']))]

    def detail(self, entry):
        with self._lock:
            self._refresh()
            # Use the fresh indexed encounter if the source database changed after matching.
            entry = next((e for e in self._entries if e['id'] == entry['id']), None)
            if entry is None:
                raise LookupError('野怪资料已更新，请重新查询')
            result = deepcopy(entry)
            player = entry['player']
            def convert(instance):
                raw = self._cards.get(instance.get('TemplateId'))
                if not raw:
                    return {'name': '卡牌资料暂缺', 'quality': instance.get('Tier', ''), 'effects': [],
                            'attributes': [], 'size': 'Small', 'slot': instance.get('SocketId', ''), 'img': ''}
                raw = deepcopy(raw)
                quality = instance.get('Tier') or raw.get('StartingTier')
                # Instance overrides must win over accumulated base-tier attributes.
                if quality in (raw.get('Tiers') or {}):
                    raw['Tiers'][quality]['Attributes'] = {**raw['Tiers'][quality].get('Attributes', {}), **(instance.get('Attributes') or {})}
                override = self._art.get(raw['Id']) or {}
                image = self.preview._images.get(raw['Id']) or {}
                if override.get('art_key') == raw.get('ArtKey'):
                    image = {'artLarge': override['art']}
                if quality not in (raw.get('Tiers') or {}):
                    loc = (raw.get('Localization') or {}).get('Title') or {'Text': raw.get('InternalName', '')}
                    return {'cardId': raw['Id'], 'name': self.preview._translate(loc, 'zh-CN'),
                            'quality': quality, 'size': raw.get('Size') or 'Small', 'slot': instance.get('SocketId', ''),
                            'img': image.get('artLarge') or image.get('art') or '', 'attributes': [],
                            'effects': [{'text': '该品质资料暂缺，无法可靠展示效果。', 'type': 'Passive'}]}
                preview = build_preview(raw, quality, instance.get('EnchantmentType') or '', 'zh-CN',
                                        self.preview._translate, image, self._stamp)
                attrs = game.build_tier_attrs(raw, quality)
                if attrs.get('Lifesteal', 0):
                    preview['effects'].append({'text': '吸血', 'type': 'Passive', 'source': 'base'})
                preview['slot'] = instance.get('SocketId', '')
                preview['enchantmentLabel'] = self.preview._translate({'Text': instance.get('EnchantmentType') or ''}, 'zh-CN')
                return preview
            def slot(instance):
                try:
                    return int(instance.get('SocketId', '').rsplit('_', 1)[-1])
                except ValueError:
                    return 100
            result['items'] = [convert(i) for i in sorted((player.get('Hand') or {}).get('Items') or [], key=slot)]
            result['skills'] = [convert(i) for i in player.get('Skills') or []]
            carried = {c.get('cardId') for c in result['items']+result['skills']}
            void_combo = {'6f70e0cf-8349-4f14-a121-6c9065dc514c', '0c710f33-d0fd-40c8-aa2d-34fc20f23140',
                          '86f9a35d-73d6-456c-b56d-ddb6adc64175', '09f668a1-82f6-42d5-94fa-88af3568edd8',
                          'b607f75b-e245-4e4a-809e-899fcdd1e6e5'}
            if entry['name_en'] == 'Void Knight' and void_combo <= carried and not entry.get('board_override'):
                result['mechanics'] = ['暗影斗篷加速镰刀；凡躯之缚让镰刀获得吸血。',
                                       '吸血武器触发催眠吸取，限速器影响双方充能。']
            result['stamp'] = hashlib.sha256((self._stamp+entry['id']).encode()).hexdigest()[:24]
            result.pop('player', None)
            if entry.get('board_override'):
                result['notice'] = '此遭遇包含特殊阵容覆盖，以下展示基础野怪阵容。'
            return result


def day_label(value):
    return 'Day ' + str(value) if value else '暂无可靠天数资料'


def format_monster_text(detail):
    from .card_preview import QUALITY_LABELS
    lines = [detail['name'], f"出现：{day_label(detail.get('day'))}｜等级：{detail.get('level', '?')}｜生命：{detail.get('health', '?')}",
             f"战斗奖励：{detail.get('gold', '?')} 金币、{detail.get('xp', '?')} 经验"]
    if detail.get('notice'):
        lines.append(detail['notice'])
    for kind, label in (('items', '物品（从左到右）'), ('skills', '技能')):
        lines.append('\n'+label)
        for card in detail.get(kind, []):
            tier = QUALITY_LABELS.get(card.get('quality'), card.get('quality', ''))
            lines.append(f"{card['name']}［{tier}］")
            for effect in card.get('effects', []):
                lines.append('  '+effect['text'])
    return '\n'.join(lines)
