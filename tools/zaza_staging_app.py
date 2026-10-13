#!/usr/bin/env python3
"""Standalone Zaza preview harness and isolated worker entrypoint."""
from __future__ import annotations

import argparse
import hmac
import hashlib
import json
import logging
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
STATIC = ROOT / 'web_runs' / 'static'
PREFIX = '/zaza-preview'
# Rewrite only complete root-relative Zaza URLs, including JS string literals.
URL_PATTERN = re.compile(r"(?<=[\"'`])(/api/admin/zaza|/api/zaza|/api/card_search|/static|/admin/zaza|/zaza)(?=[/?#\"'`])")


def required_path(name):
    path = Path(os.environ[name]).resolve()
    if not path.is_file():
        raise RuntimeError(f'{name} is not an existing file: {path}')
    return path


class CardResolver:
    """Read native art, names and sizes from copied caches; no plugin imports."""
    def __init__(self):
        cache = Path(os.environ.get('ZAZA_CARD_CACHE', ROOT / 'plugins/bazaar_plugin/cache'))
        translations_file = cache / 'translations.json'
        translations = json.loads(translations_file.read_text()) if translations_file.exists() else {}
        self.cards = {}
        game = cache / 'GameData.db'
        if game.exists():
            with sqlite3.connect(f'file:{game}?mode=ro', uri=True) as conn:
                for (raw,) in conn.execute('SELECT Data FROM cards'):
                    data = json.loads(raw)
                    cid = data.get('Id')
                    if cid:
                        en = data.get('InternalName') or data.get('Name') or cid
                        self.cards[cid] = {'cardId': cid, 'name_en': en,
                            'name': translations.get(en, en), 'size': data.get('Size', 'Small'),
                            'img': data.get('ArtLarge') or data.get('Art') or ''}
        image_file = cache / 'card_images.json'
        if image_file.exists():
            for cid, data in json.loads(image_file.read_text()).get('cards', {}).items():
                if not isinstance(data, dict):
                    continue
                existing = self.cards.get(cid, {})
                en = data.get('internalName') or existing.get('name_en') or cid
                self.cards[cid] = {'cardId': cid, 'name_en': en,
                    'name': data.get('name') or translations.get(en, en),
                    'size': data.get('size') or existing.get('size', 'Small'),
                    'img': existing.get('img') or data.get('artLarge') or data.get('art') or ''}
        if not self.cards:
            raise RuntimeError('Staging requires a populated real card cache')

    def __call__(self, card_id):
        return self.cards.get(card_id, {'cardId': card_id, 'name': card_id, 'name_en': card_id, 'img': ''})

    card_display_info_cached = __call__
    skill_display_info_cached = __call__

    def find_card_ids(self, query):
        query = ''.join(query.casefold().split())
        if not query:
            return []
        matches = []
        for cid, info in self.cards.items():
            names = [''.join(str(info.get(k, '')).casefold().split()) for k in ('name', 'name_en')]
            if query == cid.casefold() or any(query in n for n in names):
                matches.append((0 if query in names or query == cid.casefold() else 1, cid))
        return [cid for _, cid in sorted(matches)]


def config_paths():
    runs = required_path('ZAZA_RUNS_DB')
    snapshots = required_path('ZAZA_SNAPSHOTS_DB')
    spaces = Path(os.environ['ZAZA_SPACE_DB']).resolve()
    if len({runs, snapshots, spaces}) != 3:
        raise RuntimeError('Runs, snapshots and spaces must use distinct staging paths')
    return runs, snapshots, spaces, int(os.environ['ZAZA_SEASON']), os.environ['ZAZA_PHASE']


class PreviewPrefix:
    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        path = environ.get('PATH_INFO', '')
        if path == PREFIX or path.startswith(PREFIX + '/'):
            environ['PATH_INFO'] = path[len(PREFIX):] or '/zaza'
            environ['SCRIPT_NAME'] = environ.get('SCRIPT_NAME', '') + PREFIX
        # Caddy handle_path has already stripped the prefix; assets always use it.
        return self.app(environ, start_response)


def create_app():
    from flask import Flask, jsonify, request, send_from_directory
    from web_runs.zaza_space import create_zaza_blueprint
    runs, snapshots, spaces, season, phase = config_paths()
    password = os.environ.get('STATS_PASSWORD')
    if not password:
        raise RuntimeError('STATS_PASSWORD is required')
    app = Flask('zaza_staging', static_folder=None)
    app.config['MAX_CONTENT_LENGTH'] = 1024 * 1024
    app.wsgi_app = PreviewPrefix(app.wsgi_app)

    def admin_cookie():
        return hashlib.sha256((password + '|zaza-admin-v1').encode()).hexdigest()

    def authorized():
        auth = request.authorization
        basic = bool(auth and hmac.compare_digest(auth.password or '', password))
        cookie = hmac.compare_digest(request.cookies.get('zaza_admin', ''), admin_cookie())
        return basic or cookie

    @app.get('/healthz')
    def health():
        return jsonify(ok=True, service='zaza-staging', season=season, phase=phase)

    def page(filename):
        text = (STATIC / filename).read_text()
        return URL_PATTERN.sub(lambda m: PREFIX + m.group(1), text)

    @app.get('/zaza')
    @app.get('/')
    def public_page():
        return page('zaza.html')

    @app.get('/zaza/replay')
    def replay_page():
        return page('zaza-replay.html')

    @app.get('/admin/zaza')
    def admin_page():
        if not authorized():
            return 'Authentication required', 401, {'WWW-Authenticate': 'Basic realm="Zaza staging"'}
        response = app.make_response(page('zaza-admin.html'))
        response.set_cookie('zaza_admin', admin_cookie(), max_age=8*3600, httponly=True,
                            secure=True, samesite='Lax', path='/zaza-preview')
        return response

    @app.get('/static/<path:filename>')
    def static_files(filename):
        if filename not in {'zaza.js', 'zaza-admin.js', 'zaza.css', 'zaza-cards.js', 'zaza-replay.js'}:
            return 'Not found', 404
        if filename.endswith('.js'):
            return page(filename), 200, {'Content-Type': 'application/javascript; charset=utf-8'}
        return send_from_directory(STATIC, filename)

    resolver = CardResolver()

    @app.get('/api/card_search')
    def search_cards():
        return jsonify([resolver(cid) | {'zh': resolver(cid)['name'], 'en': resolver(cid)['name_en']}
                        for cid in resolver.find_card_ids(request.args.get('q', '').strip())[:20]])

    app.register_blueprint(create_zaza_blueprint(runs_path=runs, space_path=spaces,
        snapshots_path=snapshots, season=season, phase=phase,
        card_resolver=resolver, admin_authorized=authorized,
        created_after=os.environ.get('ZAZA_PHASE_START'),created_before=os.environ.get('ZAZA_PHASE_END')))
    if os.environ.get('ZAZA_COLLECTOR_TOKEN'):
        from tools.zaza_collector_bridge import register_collector_routes
        from web_runs.zaza_space import ZazaSpaceStore
        bridge_store = ZazaSpaceStore(spaces)
        bridge_store.initialize()
        app.config['MAX_CONTENT_LENGTH'] = 32 * 1024 * 1024
        register_collector_routes(app, bridge_store, runs, season, phase,
            os.environ['ZAZA_PHASE_START'], os.environ['ZAZA_COLLECTOR_TOKEN'])
    return app


def worker(once=False, interval=60):
    # Existing worker's CLI imports plugin data_client. Call its side-effect-free
    # process_once directly, with explicit production-discovered scope instead.
    from tools.zaza_space_worker import process_once
    from web_runs.zaza_space import ZazaSpaceStore
    runs, snapshots, spaces, season, phase = config_paths()
    store = ZazaSpaceStore(spaces)
    store.initialize()
    def enabled():
        pause_env = os.environ.get('ZAZA_PAUSE_ENV')
        if pause_env:
            # Re-read the production pause switch only; this path is read-only.
            try:
                matches = re.findall(r'^\s*(?:export\s+)?ENABLE_DAY_SNAPSHOT_QUEUE\s*=\s*[\"\']?([01])',
                                     Path(pause_env).read_text(), re.MULTILINE)
                return bool(matches and matches[-1] == '1')
            except OSError:
                return False
        return os.environ.get('ENABLE_DAY_SNAPSHOT_QUEUE', '0') == '1'
    discovery_ready = None
    if os.environ.get('ZAZA_COLLECTOR_TOKEN'):
        from tools.zaza_collector_bridge import enqueue_profile, has_completion_result
        def discovery_ready(space, job):
            enqueue_profile(store, space['profile_id'], season, phase)
            return has_completion_result(store, space['profile_id'], season, phase)
    logging.basicConfig(level=logging.INFO)
    while True:
        try:
            count = process_once(store, runs, season=season, phase=phase,
                                 snapshots_path=snapshots, enabled=enabled, discovery_ready=discovery_ready,
                                 created_after=os.environ.get('ZAZA_PHASE_START'),created_before=os.environ.get('ZAZA_PHASE_END'))
            logging.info('staging discovery jobs processed=%d enabled=%s', count, enabled())
        except Exception:
            logging.exception('staging worker cycle failed')
            if once:
                raise
        if once:
            return
        time.sleep(max(10, interval))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', action='store_true')
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--interval', type=int, default=60)
    args = parser.parse_args()
    if args.worker:
        worker(args.once, args.interval)
    else:
        create_app().run(host=os.environ.get('WEB_HOST', '127.0.0.1'),
                         port=int(os.environ.get('WEB_PORT', '1030')), threaded=True)
