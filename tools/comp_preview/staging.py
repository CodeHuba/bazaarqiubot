"""Isolated /runs staging: actual website assets and actual public query handlers."""
import ast
import gzip
import hashlib
import html
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import types

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/'web_runs'))

from flask import Flask, Response, g, jsonify, request, send_file

PREFIX = os.environ.get('COMP_STAGING_PREFIX', '/comp-preview').rstrip('/')
URLS = re.compile(r'''(?<=["'`])(/static|/api|/runs)(?=[/?#"'`])''')


def initialize_db(path, payload):
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute('''CREATE TABLE runs (
          id TEXT PRIMARY KEY,hero TEXT,username TEXT,created_at TEXT,items_json TEXT,skills_json TEXT,
          combats_json TEXT,stat_wins INTEGER,stat_losses INTEGER,screenshot_url TEXT,player_rank TEXT,
          season INTEGER,phase TEXT,card_ids_text TEXT,raw_json TEXT)''')
        db.executemany('INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', [(
            r['id'], r['hero'], None, r['created_at'], json.dumps(r['items']), json.dumps(r['skills']),
            json.dumps(r.get('combats',[])), r['wins'], r.get('losses'), r['screenshot'], r['rank'],
            payload['season'], payload['phase'], ' '.join(c['cardId'] for c in r['items']),
            json.dumps({'items':r['items'],'skills':r['skills']})) for r in payload['runs']])
        db.execute('CREATE INDEX current_scope ON runs(season,phase,hero,player_rank)')


def create_app(snapshot, state=None, *, maintain_cache=False):
    snapshot = Path(snapshot).resolve()
    state = Path(state or snapshot.parent)
    db_path = state/'runs.db'
    metadata_path = state/'snapshot-metadata.json'
    snapshot_hash = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    cached = json.loads(metadata_path.read_text(encoding='utf-8')) if metadata_path.is_file() else None
    if cached and db_path.is_file():
        if cached.get('snapshot_sha256') != snapshot_hash:
            raise RuntimeError('Staging snapshot changed; use a separate state directory')
        payload, source_count = cached['payload'], cached['source_count']
    else:
        payload = json.loads(gzip.decompress(snapshot.read_bytes()))
        initialize_db(db_path, payload)
        source_count = len(payload.pop('runs'))
        # The immutable match database already owns the large run array. Future
        # starts need only card metadata, not a second decoded copy of all runs.
        state.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=state,delete=False) as temporary:
            json.dump(dict(snapshot_sha256=snapshot_hash,payload=payload,source_count=source_count),temporary,ensure_ascii=False,separators=(',',':'))
            temporary_path=Path(temporary.name)
        temporary_path.replace(metadata_path)
    # Skip the bot package initializer. All query handlers/classes below are unchanged source.
    for name,directory in [('plugins',ROOT/'plugins'),('plugins.bazaar_plugin',ROOT/'plugins/bazaar_plugin')]:
        package=types.ModuleType(name)
        package.__path__=[str(directory)]
        sys.modules[name]=package
    constants=types.ModuleType('plugins.bazaar_plugin.data_client')
    constants.CURRENT_SEASON_ID=constants.RUNS_SEASON_ID=payload['season']
    constants.CURRENT_PHASE=payload['phase']
    sys.modules[constants.__name__]=constants
    from plugins.bazaar_plugin import runs_query
    from plugins.bazaar_plugin.run_card_filters import normalize_run_card_filters
    base_query=getattr(runs_query,'_composition_original_query',runs_query.RunsQuery)
    runs_query._composition_original_query=base_query
    catalog=payload['cards']
    # Asset metadata is independent of the fixed match snapshot. Missing art can
    # be repaired without changing statistics or replacing the match database.
    assets_file=state/'card-assets.json'
    assets=json.loads(assets_file.read_text(encoding='utf-8')) if assets_file.is_file() else {}
    for cid,info in assets.get('cards',{}).items():
        if isinstance(info,dict):catalog[cid]=dict(catalog.get(cid) or {},**info)
    native_path=os.environ.get('GAMEDATA_DB','')
    if native_path and Path(native_path).is_file():
        with sqlite3.connect(f'file:{native_path}?mode=ro',uri=True) as native:
            for (raw,) in native.execute('SELECT Data FROM cards'):
                card=json.loads(raw)
                if isinstance(catalog.get(card.get('Id')),dict) and card.get('Size'):
                    catalog[card['Id']]['size']=card['Size']
                    catalog[card['Id']]['heroes']=card.get('Heroes')
    art_dir=state/'art'
    available={}
    for cid,record in assets.get('art',{}).items():
        filename=record.get('file','')
        if re.fullmatch(r'[a-f0-9]{64}\.webp',filename) and (art_dir/filename).is_file():
            available[cid]=art_dir/filename
    from urllib.parse import urlparse
    for cid,info in catalog.items():
        if cid in available:continue
        for field in ('artLarge','art'):
            url=(info or {}).get(field) or ''
            path=art_dir/(hashlib.sha256(url.encode()).hexdigest()+Path(urlparse(url).path).suffix)
            if path.is_file():
                available[cid]=path
                break

    class SnapshotQuery(base_query):
        analysis_snapshot_time=payload['captured_at']

        def __init__(self, db_path=str(db_path), **kwargs):
            super().__init__(db_path=db_path, **kwargs)

        def load(self):
            self.conn=sqlite3.connect(f'file:{self.db_path}?mode=ro',uri=True,check_same_thread=False)
            for cid,card in catalog.items():
                if not isinstance(card,dict):continue
                en=card.get('internalName') or cid
                name=card.get('name') or en
                self.card_mapping[cid]={'name':en,'type':card.get('type','')}
                self.name_to_ids.setdefault(en.lower().replace(' ',''),[]).append(cid)
                self.en_to_zh[en]=name
                self.zh_to_en[name]=en
                if card.get('size'):self.size_map[cid]=card['size']
                if isinstance(card.get('heroes'),list):self.card_heroes[cid]=card['heroes']

        def _card_image_info(self,cid):
            info=dict(catalog.get(cid) or {})
            image=PREFIX+'/art/'+cid if cid in available else ''
            info.update(art=image,artLarge=image)
            return info

    runs_query.RunsQuery=SnapshotQuery
    from plugins.bazaar_plugin.comp_analysis_store import prewarm, start_background_refresh, cache_status
    factory=lambda:SnapshotQuery(db_path=str(db_path))
    prewarm(factory, season=payload['season'], phase=payload['phase'], wait=True)
    if maintain_cache:
        start_background_refresh(factory, season=payload['season'], phase=payload['phase'])
    app=Flask(__name__,static_folder=None)
    # Compile precisely the real API handlers, excluding production initialization/hooks.
    tree=ast.parse((ROOT/'web_runs/app.py').read_text(encoding='utf-8'))
    route_names={'api_comp_card','api_runs','api_run_detail','api_card_search','_find_card_search_matches'}
    selected=[]
    for node in tree.body:
        if isinstance(node,ast.FunctionDef) and node.name in route_names:
            node.decorator_list=[]
            selected.append(node)
    assert len(selected)==len(route_names)
    index=[]
    for cid,info in catalog.items():
        if not isinstance(info,dict):continue
        index.append({'cardId':cid,'zh':info.get('name') or info.get('internalName') or cid,
                      'en':info.get('internalName') or cid,'type':info.get('type',''),
                      'img':PREFIX+'/art/'+cid if cid in available else '', 'size':info.get('size','Small')})
    namespace={'app':app,'request':request,'jsonify':jsonify,'g':g,'json':json,'re':re,
               'RunsQuery':SnapshotQuery,'RUNS_DB_PATH':str(db_path),
               'RUNS_SEASON_ID':payload['season'],'CURRENT_PHASE':payload['phase'],
               '_card_search_index':index,'_mask_ip':lambda value:'staging',
               '_log_query':lambda *args,**kwargs:None,'normalize_run_card_filters':normalize_run_card_filters}
    exec(compile(ast.fix_missing_locations(ast.Module(body=selected,type_ignores=[])),str(ROOT/'web_runs/app.py'),'exec'),namespace)
    for name,path in [('api_comp_card','/api/comp/card'),('api_runs','/api/runs'),
                      ('api_run_detail','/api/runs/<run_id>'),('api_card_search','/api/card_search')]:
        app.add_url_rule(path,name,namespace[name])
    from card_preview_api import create_card_preview_blueprint
    preview_repository=None
    if native_path:
        from plugins.bazaar_plugin.card_preview import CardPreviewRepository
        class StagingCardRepository(CardPreviewRepository):
            def get(self,card_id,*args,**kwargs):
                result=dict(super().get(card_id,*args,**kwargs))
                if card_id in available:result['img']=PREFIX+'/art/'+card_id
                return result
        preview_repository=StagingCardRepository(images=Path(native_path).parent/'card_images.json')
    app.register_blueprint(create_card_preview_blueprint(preview_repository,data_label='staging 游戏数据'))

    @app.get('/')
    @app.get('/runs')
    def page():
        source=(ROOT/'web_runs/static/runs.html').read_text(encoding='utf-8')
        source=source.replace('content="index, follow"','content="noindex, nofollow"')
        banner='<div style="padding:9px 20px;text-align:center;background:var(--theme-surface,#171047);color:var(--theme-muted,#b5b3d7);font-size:12px;border-bottom:1px solid var(--theme-border,#444)">阵容分析验收环境 · '+html.escape(str(payload['phase']))+' · 固定快照 '+html.escape(payload['captured_at'][:16].replace('T',' '))+' UTC · 不写入正式数据</div>'
        source=source.replace('<body>','<body>'+banner,1)
        return Response(URLS.sub(lambda m:PREFIX+m[1],source),content_type='text/html; charset=utf-8')

    @app.get('/static/<path:filename>')
    def static(filename):
        base=(ROOT/'web_runs/static').resolve()
        path=(base/filename).resolve()
        if not path.is_relative_to(base) or not path.is_file():return '',404
        if path.suffix in ('.js','.css'):
            source=path.read_text(encoding='utf-8')
            mime='application/javascript' if path.suffix=='.js' else 'text/css'
            return Response(URLS.sub(lambda m:PREFIX+m[1],source),content_type=mime)
        return send_file(path)

    @app.get('/art/<cid>')
    def art(cid):
        path=available.get(cid)
        return send_file(path, max_age=3600) if path else ('',404)

    @app.get('/healthz')
    def health():
        return jsonify(ok=True,phase=payload['phase'],season=payload['season'],snapshot_at=payload['captured_at'],runs=source_count,analysis_cache=cache_status())

    @app.get('/api/suggestions')
    def suggestions():return jsonify(heroes=[],cards=[])

    @app.get('/api/announcement')
    @app.get('/api/announcements')
    @app.get('/api/announcement/latest')
    def announcements():return jsonify(announcements=[])

    @app.post('/api/track/pv')
    @app.post('/api/track/visit')
    @app.post('/api/track/search')
    @app.post('/api/track/acquisition')
    @app.post('/api/track/action')
    @app.post('/api/track/comp')
    def telemetry():return '',204

    @app.after_request
    def headers(response):
        response.headers['X-Robots-Tag']='noindex, nofollow, noarchive'
        if response.mimetype not in ('image/webp','image/png','image/jpeg'):
            response.headers['Cache-Control']='no-store'
        return response

    original=app.wsgi_app
    def prefix(environ,start_response):
        path=environ.get('PATH_INFO','')
        if path==PREFIX or path.startswith(PREFIX+'/'):
            environ['PATH_INFO']=path[len(PREFIX):] or '/'
            environ['SCRIPT_NAME']=PREFIX
        return original(environ,start_response)
    app.wsgi_app=prefix
    return app


def app_from_env():
    return create_app(os.environ['COMP_STAGING_SNAPSHOT'], os.environ['COMP_STAGING_STATE'], maintain_cache=True)


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--snapshot',type=Path,default=ROOT/'.tmp/comp-preview/snapshot.json.gz')
    parser.add_argument('--state',type=Path)
    parser.add_argument('--port',type=int,default=1048)
    args=parser.parse_args()
    from socketserver import ThreadingMixIn
    from wsgiref.simple_server import make_server, WSGIServer
    class ThreadedServer(ThreadingMixIn,WSGIServer):
        daemon_threads=True
        # HTTP/2 can dispatch many card images together through Caddy. The
        # default backlog of five drops valid image requests during expansion.
        request_queue_size=256
    print(f'Integrated staging: http://127.0.0.1:{args.port}{PREFIX}/runs',flush=True)
    make_server('127.0.0.1',args.port,create_app(args.snapshot,args.state,maintain_cache=True),server_class=ThreadedServer).serve_forever()
