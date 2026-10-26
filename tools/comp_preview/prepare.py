"""Export a read-only current-phase snapshot; never changes the remote service."""
import base64
import gzip
import json
import io
import re
import tarfile
from pathlib import Path
import subprocess

REMOTE = r'''
import ast,base64,datetime,gzip,json,pathlib,sqlite3
root=pathlib.Path('/opt/qiubot')
scope={}
for node in ast.parse((root/'plugins/bazaar_plugin/data_client.py').read_text()).body:
    if isinstance(node,ast.Assign):
        for target in node.targets:
            if isinstance(target,ast.Name) and target.id in ('RUNS_SEASON_ID','CURRENT_PHASE'):
                scope[target.id]=ast.literal_eval(node.value)
db=sqlite3.connect('file:/opt/qiubot/data/bazaar_runs.db?mode=ro',uri=True)
db.row_factory=sqlite3.Row
rows=[dict(row) for row in db.execute('SELECT id,hero,created_at,items_json,skills_json,stat_wins,stat_losses,combats_json,player_rank,screenshot_url,raw_json FROM runs WHERE season=? AND phase=?',(scope['RUNS_SEASON_ID'],scope['CURRENT_PHASE']))]
db.close()
# Keep public card-instance information, excluding account/profile information.
def parse(v,fallback):
    try:return json.loads(v) if isinstance(v,str) else v
    except (ValueError,TypeError):return fallback
def instances(row,kind):
    compact=parse(row.get(kind+'_json'),None)
    raw=parse(row.get('raw_json'),{}) or {}
    originals=raw.get(kind,[]) if isinstance(raw,dict) else []
    cards=compact if isinstance(compact,list) else originals
    result=[]
    for source in cards if isinstance(cards,list) else []:
        if isinstance(source,str) and kind=='skills':source={'cardId':source}
        if not isinstance(source,dict):continue
        cid=source.get('cardId') or source.get('skillId')
        if not isinstance(cid,str) or not cid:continue
        card={k:source[k] for k in ('tier','tierOverride','isLegendary','enchantment','slotPosition') if k in source}
        card['cardId']=cid
        matches=[c for c in originals if isinstance(c,dict) and (c.get('cardId') or c.get('skillId'))==cid]
        for field in ('slotPosition','enchantment'):
            if field in card:matches=[c for c in matches if c.get(field)==card[field]]
        for field in ('tier','isLegendary','enchantment','slotPosition'):
            if field not in card and matches and all(field in c for c in matches) and all(c[field]==matches[0][field] for c in matches):card[field]=matches[0][field]
        if 'tier' not in card and 'tierOverride' in card:card['tier']=card['tierOverride']
        result.append(card)
    return result
clean=[{'id':r['id'],'hero':r['hero'],'created_at':r['created_at'],'wins':r['stat_wins'],'losses':r['stat_losses'],'combats':[{'d':c['d']} for c in (parse(r['combats_json'],[]) or []) if isinstance(c,dict) and type(c.get('d')) is int],'rank':r['player_rank'],'screenshot':r['screenshot_url'],'items':instances(r,'items'),'skills':instances(r,'skills')} for r in rows]
cards=json.loads((root/'plugins/bazaar_plugin/cache/card_images.json').read_text()).get('cards',{})
payload={'season':scope['RUNS_SEASON_ID'],'phase':scope['CURRENT_PHASE'],'captured_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'runs':clean,'cards':cards,'source':'线上当前阶段只读快照；采集入库过滤 0 胜记录'}
print(base64.b64encode(gzip.compress(json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode())).decode())
'''

def prepare_art(destination):
    # Copy already-cached public art only; no upstream fetches or remote cache writes.
    source = """import io,pathlib,sys,tarfile
from PIL import Image
root=pathlib.Path('/opt/qiubot/web_runs/cache/share_card_art')
stream=io.BytesIO()
with tarfile.open(fileobj=stream,mode='w:gz') as archive:
    for path in sorted(root.glob('*')):
        if path.is_file() and not path.is_symlink() and path.suffix.lower() in ('.webp','.png','.jpg','.jpeg'):
            with Image.open(path) as image:
                fmt=image.format
                image.thumbnail((180,180))
                data=io.BytesIO()
                image.save(data,format=fmt,quality=82)
            body=data.getvalue()
            entry=tarfile.TarInfo(path.name)
            entry.size=len(body)
            archive.addfile(entry,io.BytesIO(body))
sys.stdout.buffer.write(stream.getvalue())
"""
    result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15',
                             'ubuntu@101.34.58.217', '/opt/qiubot/venv/bin/python -'], input=source.encode(),
                            capture_output=True, timeout=180)
    if result.returncode:
        raise SystemExit(result.stderr.decode(errors='replace'))
    art = destination / 'art'
    art.mkdir(exist_ok=True)
    count = 0
    with tarfile.open(fileobj=io.BytesIO(result.stdout), mode='r:gz') as archive:
        for entry in archive:
            if not entry.isfile() or not re.fullmatch(r'[a-f0-9]{64}\.(webp|png|jpg|jpeg)', entry.name) or entry.size > 10*1024*1024:
                raise ValueError('Unexpected cached art entry')
            (art / entry.name).write_bytes(archive.extractfile(entry).read())
            count += 1
    print(json.dumps({'cached_art_files': count, 'bytes': len(result.stdout)}))


if __name__ == '__main__':
    root = Path(__file__).resolve().parents[2]
    destination = root / '.tmp' / 'comp-preview'
    destination.mkdir(parents=True, exist_ok=True)
    if '--art-only' in __import__('sys').argv:
        prepare_art(destination)
        raise SystemExit(0)
    result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15',
                             'ubuntu@101.34.58.217', 'python3 -'], input=REMOTE,
                            text=True, encoding='utf-8', capture_output=True, timeout=180)
    if result.returncode:
        raise SystemExit(result.stderr)
    packed = base64.b64decode(result.stdout.strip(), validate=True)
    payload = json.loads(gzip.decompress(packed))
    target = destination / 'snapshot.json.gz'
    target.write_bytes(packed)
    print(json.dumps({'path': str(target), 'runs': len(payload['runs']), 'phase': payload['phase'],
                      'cards': len(payload['cards']), 'bytes': len(packed)}, ensure_ascii=False))
    prepare_art(destination)
