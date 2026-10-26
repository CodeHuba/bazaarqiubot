"""Isolated, loopback-only library/builder acceptance preview. No production writes."""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys
import threading
import types
import urllib.request
import urllib.parse

ROOT = Path(__file__).resolve().parents[1]


def bootstrap():
    # Import shared read-only parsers without starting the QQ bot package.
    for name, directory in [('plugins', ROOT / 'plugins'), ('plugins.bazaar_plugin', ROOT / 'plugins/bazaar_plugin')]:
        if name not in sys.modules:
            package = types.ModuleType(name)
            package.__path__ = [str(directory)]
            sys.modules[name] = package
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / 'web_runs'))


def matches(rule, card):
    """Only evaluate known static stock constraints; unknown rules stay unknown."""
    if not rule:
        return True
    kind = rule.get('$type')
    if kind in ('ConstraintAnd', 'ConstraintOr'):
        values = [matches(r, card) for r in rule.get('Constraints', [])]
        if kind == 'ConstraintAnd':
            return False if False in values else None if None in values else True
        return True if True in values else None if None in values else False
    fields = {'ConstraintSize': ('Sizes', 'Size'), 'ConstraintCardType': ('Types', 'Type'),
              'ConstraintTag': ('Tags', 'Tags'), 'ConstraintHiddenTag': ('Tags', 'HiddenTags'),
              'ConstraintHero': ('Heroes', 'Heroes'), 'ConstraintTier': ('Tiers', 'StartingTier')}
    if kind not in fields:
        return None
    source, target = fields[kind]
    wanted = rule.get(source, [])
    actual = card.get(target, [])
    actual = actual if isinstance(actual, list) else [actual]
    found = all(x in actual for x in wanted) if rule.get('Operator') == 'All' else any(x in actual for x in wanted)
    return not found if rule.get('IsNot') else found


def appearance_days(schedule):
    """Read documented day buckets, retaining the distinction from card tier odds."""
    if schedule.get('kind') == 'observed_appearance' and schedule.get('source_patch') != '19.3':
        return []
    buckets = schedule.get('observed_day_buckets')
    if buckets:
        return sorted({10 if day == '10+' else day for day in buckets if day == '10+' or isinstance(day, int) and 1 <= day <= 10})
    if isinstance(schedule.get('min_day'), int):
        return list(range(max(1, schedule['min_day']), min(10, schedule.get('max_day') or 10) + 1))
    days = set()
    for match in re.finditer(r'Day\s*(\d+)(?:\s*[–-]\s*(\d+)|(\+))?', schedule.get('day_label', '')):
        first = int(match[1])
        last = int(match[2]) if match[2] else 10 if match[3] else first
        days.update(range(max(1, first), min(10, last) + 1))
    return sorted(days)


def create_app(database, translations, images, state, prefix='', data_label='19.3 · 本地数据快照', production=False):
    bootstrap()
    from flask import Flask, jsonify, send_from_directory, abort, Response
    from plugins.bazaar_plugin.card_preview import CardPreviewRepository
    from plugins.bazaar_plugin.encounter_data import hidden
    from card_preview_api import create_card_preview_blueprint

    repository = CardPreviewRepository(database, translations, images)
    repository._refresh()
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        raw = [json.loads(row[0]) for row in db.execute('SELECT Data FROM cards')]
    cards, errors = [], []
    for record in raw:
        if (record.get('Type') not in ('Item', 'Skill') or hidden(record) or not record.get('Tiers')
                or record.get('SpawningEligibility') == 'Never'):
            continue
        try:
            preview = repository.get(record['Id'])
            cards.append({**{k: preview[k] for k in ('cardId', 'name', 'name_en', 'type', 'size', 'quality', 'tags', 'hiddenTags', 'attributes', 'availableQualities', 'availableEnchantments')},
                          'heroes': record.get('Heroes', []), 'startingTier': record.get('StartingTier'),
                          'img': prefix + '/api/library-art/' + record['Id'] + '?v=native-1',
                          'summary': ' '.join(e['text'] for e in preview['effects']), 'merchants': [], 'days': [], 'merchantDays': {}})
        except (ValueError, LookupError) as error:
            errors.append([record['Id'], str(error)])
    by_id = {c['cardId']: c for c in cards}
    encounter_sources = json.loads((ROOT / 'plugins/bazaar_plugin/encounter_sources.json').read_text(encoding='utf-8'))
    schedules = encounter_sources.get('appearance', {})
    merchant_art = {}
    merchants = []
    for record in raw:
        if 'Merchant' not in record.get('Tags', []) or hidden(record):
            continue
        groups = ((record.get('SelectionContext') or {}).get('SpawnContext') or {}).get('Groups', [])
        rules = [f.get('Constraints') for g in groups for f in g.get('Filters', []) if f.get('$type') == 'TSpawnFilterQuery']
        if not rules:
            continue
        name = repository._translate((record.get('Localization') or {}).get('Title') or {}, 'zh-CN')
        found = []
        for source in raw:
            card = by_id.get(source['Id'])
            if card is None or not any(matches(rule, source) is True for rule in rules):
                continue
            card['merchants'].append(record['Id'])
            found.append(source['Id'])
            days = appearance_days(schedules.get(record['Id'], {}))
            card['days'].extend(days)
            card['merchantDays'][record['Id']] = days
        if found:
            image = encounter_sources.get('entries', {}).get(record['Id'], {})
            if image.get('art_key') == record.get('ArtKey') and image.get('art'):
                merchant_art[record['Id']] = image
            merchants.append({'id': record['Id'], 'name': name, 'count': len(found),
                              'name_en': ((record.get('Localization') or {}).get('Title') or {}).get('Text', ''),
                              'img': prefix + '/api/library-merchant-art/' + record['Id'] + '?v=1' if record['Id'] in merchant_art else '',
                              'dayLabel': schedules.get(record['Id'], {}).get('day_label', '日期待核实')})
    for card in cards:
        card['days'] = sorted(set(card['days']))
    cards.sort(key=lambda c: (not ('Jules' in c['heroes']), c['size'] != 'Small', c['name_en']))
    label = data_label
    payload = {'cards': cards, 'merchants': sorted(merchants, key=lambda m: m['name']), 'version': label,
               'fingerprint': hashlib.sha256(database.read_bytes()).hexdigest()[:12], 'skipped': len(errors),
               'sourceNote': '商人按已支持的静态货池条件匹配；日期仅表示已知商人出现日期，未计入当日品质概率及全部获取途径。'}
    app = Flask(__name__, static_folder=str(ROOT / 'web_runs/static'))
    app.register_blueprint(create_card_preview_blueprint(repository, label))
    # The @256 thumbnails are square crops, even for Small and Large items.
    # Keep native art in a separate cache so existing square files cannot leak in.
    image_cache = state / 'images-native-1'
    image_cache.mkdir(parents=True, exist_ok=True)
    merchant_cache = state / 'merchant-images-v1'
    merchant_cache.mkdir(parents=True, exist_ok=True)
    locks = {}
    locks_guard = threading.Lock()
    downloads = threading.BoundedSemaphore(4)

    @app.after_request
    def staging_headers(response):
        if not production:
            response.headers['X-Robots-Tag'] = 'noindex, nofollow'
        return response

    @app.get('/healthz')
    def health():
        return jsonify(ok=True, service='library-preview', cards=len(cards), merchants=len(merchants),
                       version=label, fingerprint=payload['fingerprint'])

    @app.get('/')
    @app.get('/library')
    def page():
        html = (Path(app.static_folder) / 'library.html').read_text(encoding='utf-8')
        if prefix:
            html = html.replace('href="/static/', 'href="' + prefix + '/static/')
            html = html.replace('src="/static/', 'src="' + prefix + '/static/')
            html = html.replace('href="/library"', 'href="' + prefix + ('/"' if production else '/library"'))
            html = html.replace('<head>', '<head>\n<meta name="library-base" content="' + prefix + '">', 1)
        if production:
            html = html.replace('<head>', '<head>\n<meta name="library-analytics" content="production">\n<script src="/static/acquisition.js?v=1"></script>', 1)
            html = html.replace('content="noindex,nofollow"', 'content="index,follow"')
            html = html.replace('<span class="preview-badge">预览版</span>', '')
            html = html.replace(' target="_blank" rel="noopener"', '')
            html = html.replace('数据来自本地 GameData 与官方中文。', '数据来自游戏数据与官方中文。')
        return Response(html, content_type='text/html; charset=utf-8', headers={'Cache-Control': 'no-store'})

    @app.get('/api/library')
    def library():
        return jsonify(payload)

    @app.get('/api/library-merchant-art/<merchant_id>')
    def merchant_image(merchant_id):
        info = merchant_art.get(merchant_id)
        if not info:
            abort(404)
        with locks_guard:
            lock = locks.setdefault('merchant:' + merchant_id, threading.Lock())
        path = merchant_cache / (merchant_id + '.webp')
        with lock:
            if not path.is_file():
                for url in dict.fromkeys([info.get('art'), info.get('art_fallback')]):
                    if not url or urllib.parse.urlparse(url).scheme != 'https' or urllib.parse.urlparse(url).hostname != 's.bazaardb.gg':
                        continue
                    try:
                        request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (BazaarQiuBot staging preview)'})
                        with downloads, urllib.request.urlopen(request, timeout=15) as response:
                            content = response.read(4 * 1024 * 1024)
                        temporary = path.with_suffix('.pending')
                        temporary.write_bytes(content)
                        temporary.replace(path)
                        break
                    except Exception as error:
                        app.logger.warning('Merchant image unavailable for %s: %s', merchant_id, error)
                if not path.is_file():
                    abort(502)
        return send_from_directory(merchant_cache, path.name, max_age=86400)

    @app.get('/api/library-art/<card_id>')
    def art(card_id):
        if card_id not in by_id:
            abort(404)
        with locks_guard:
            lock = locks.setdefault(card_id, threading.Lock())
        path = image_cache / (card_id + '.webp')
        with lock:
            if not path.is_file():
                info = repository._images.get(card_id) or {}
                url = info.get('artLarge') or info.get('art')
                if not url or urllib.parse.urlparse(url).hostname != 's.bazaardb.gg':
                    abort(404)
                try:
                    request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (BazaarQiuBot local preview)'})
                    with downloads, urllib.request.urlopen(request, timeout=15) as response:
                        content = response.read(4 * 1024 * 1024)
                    temporary = path.with_suffix('.pending')
                    temporary.write_bytes(content)
                    temporary.replace(path)
                except Exception as error:
                    app.logger.warning('Card image unavailable for %s: %s', card_id, error)
                    abort(502)
        return send_from_directory(image_cache, path.name, max_age=86400)

    print(f'Library: {len(cards)} cards, {len(merchants)} merchants, {len(errors)} skipped', flush=True)
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    assets = ROOT / '.tmp/season-19.3-20261024/assets'
    parser.add_argument('--database', type=Path, default=assets / 'GameData.db')
    parser.add_argument('--translations', type=Path, default=assets / 'zh-CN.bytes')
    parser.add_argument('--images', type=Path, default=assets / 'card_images.json')
    parser.add_argument('--port', type=int, default=1031)
    parser.add_argument('--state-dir', type=Path, default=ROOT / '.tmp/library-preview')
    parser.add_argument('--prefix', default='')
    parser.add_argument('--data-label', default='19.3 · 本地数据快照')
    parser.add_argument('--production', action='store_true')
    args = parser.parse_args()
    if args.prefix and not re.fullmatch(r'/[a-z0-9-]+', args.prefix):
        parser.error('Prefix must be one lowercase URL path segment')
    app = create_app(args.database, args.translations, args.images, args.state_dir, args.prefix, args.data_label, args.production)
    print(f'Local: http://127.0.0.1:{args.port}{args.prefix}/library', flush=True)
    app.run(host='127.0.0.1', port=args.port, threaded=True, debug=False)


if __name__ == '__main__':
    main()
