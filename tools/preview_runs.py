"""Run the real web app on loopback using a local run DB and card-image index.

Example:
    python tools/preview_runs.py --db path/to/runs.db --card-images path/to/card_images.json

This boots the web server without loading the QQ bot plugin. Preview state stays
in .tmp/runs-preview (or --state-dir), independently of production services.
"""

import argparse
import os
from pathlib import Path
import sys
import types


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, required=True)
    parser.add_argument('--card-images', type=Path, required=True)
    parser.add_argument('--state-dir', type=Path, default=root / '.tmp' / 'runs-preview')
    parser.add_argument('--port', type=int, default=1027)
    args = parser.parse_args()
    for label, path in [('run database', args.db), ('card-image index', args.card_images)]:
        if not path.is_file():
            parser.error(f'Missing {label}: {path}')
    state = args.state_dir.resolve()
    state.mkdir(parents=True, exist_ok=True)
    os.environ.update({
        'RUNS_DB_PATH': str(args.db.resolve()),
        'STATS_DB_PATH': str(state / 'stats.db'),
        'DAY_SNAPSHOT_DB_PATH': str(state / 'day-snapshots.db'),
        'DAY_STATS_DB_PATH': str(state / 'day-stats.db'),
        'FEEDBACK_DB_PATH': str(state / 'feedback.db'),
        'ZAZA_SPACE_DB_PATH': str(state / 'zaza-spaces.db'),
        'ENABLE_DAY_SNAPSHOT_QUEUE': '0',
    })
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(root / 'web_runs'))
    # The package __init__ normally boots bot-specific imports. All web/query
    # modules below are the original modules; only local file defaults change.
    for name, directory in [('plugins', root / 'plugins'),
                            ('plugins.bazaar_plugin', root / 'plugins' / 'bazaar_plugin')]:
        package = types.ModuleType(name)
        package.__path__ = [str(directory)]
        sys.modules[name] = package
    from plugins.bazaar_plugin import card_image_helper, runs_query
    card_image_helper._CACHE_FILE = args.card_images.resolve()
    card_image_helper._CACHE = None
    base_query = runs_query.RunsQuery

    class PreviewRunsQuery(base_query):
        def __init__(self, db_path=None, **kwargs):
            super().__init__(db_path=db_path or os.environ['RUNS_DB_PATH'], **kwargs)

        def load(self):
            super().load()
            # The exported image index also carries official bilingual names;
            # local previews need not have the production translations.json.
            for info in card_image_helper._load().get('cards', {}).values():
                if not isinstance(info, dict):
                    continue
                en, zh = info.get('internalName'), info.get('name')
                if en and zh:
                    self.en_to_zh.setdefault(en, zh)
                    self.zh_to_en.setdefault(zh, en)

    runs_query.RunsQuery = PreviewRunsQuery
    # OCR is unrelated to read-only browsing and needs server-only dependencies.
    # Disable its job hooks; writes/ingestion are blocked below as well.
    ocr = types.ModuleType('ocr_worker')
    ocr.start_worker = lambda: None
    ocr.enqueue_run = lambda *args, **kwargs: None
    sys.modules['ocr_worker'] = ocr
    import app as web_app
    web_app.QUERY_STATS_DB = str(state / 'query-stats.db')
    web_app._init_stats_db()

    @web_app.app.before_request
    def preview_read_only():
        from flask import request, jsonify
        if request.method not in ('GET', 'HEAD', 'OPTIONS') and request.path != '/api/track/pv':
            return jsonify({'error': 'Local preview is read-only'}), 405

    print(f'Local run preview: http://127.0.0.1:{args.port}/runs', flush=True)
    web_app.app.run(host='127.0.0.1', port=args.port, threaded=True, debug=False)


if __name__ == '__main__':
    main()
