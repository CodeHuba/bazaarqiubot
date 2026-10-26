"""Complete staging card art from recorded URLs and verified native mappings only."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter, defaultdict
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import threading
import time
import uuid
from urllib.error import HTTPError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

from PIL import Image

_image_files_lock=threading.RLock()


def valid_image(path):
    try:
        with _image_files_lock:
            with Image.open(path) as image:
                image.verify()
        return True
    except (OSError, ValueError, SyntaxError):
        return False


def asset_path(directory, source):
    return directory / (hashlib.sha256(source.encode()).hexdigest()+'.webp')


def write_thumbnail(path, content):
    with Image.open(io.BytesIO(content)) as image:
        image.load()
        image.thumbnail((240,240))
        if image.mode not in ('RGB','RGBA'):
            image=image.convert('RGBA')
        buffer=io.BytesIO()
        image.save(buffer,format='WEBP',quality=88)
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    with _image_files_lock:
        temporary.write_bytes(buffer.getvalue())
        temporary.replace(path)


def fetch_image(url):
    parsed=urlparse(url)
    if parsed.scheme!='https' or parsed.hostname!='s.bazaardb.gg' or not parsed.path.startswith('/v1/'):
        raise ValueError('Unrecognized official card art URL')
    request=Request(url,headers={'User-Agent':'BazaarQiuBot/1.0'})
    with urlopen(request,timeout=15) as response:
        if not response.headers.get('Content-Type','').startswith('image/'):
            raise ValueError('Upstream did not return an image')
        content=response.read(10*1024*1024+1)
        if len(content)>10*1024*1024:raise ValueError('Image exceeds size limit')
        return content


def native_catalog(path):
    cards={}
    with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True) as db:
        for (raw,) in db.execute('SELECT Data FROM cards'):
            value=json.loads(raw)
            if value.get('Id') and value.get('Type') in ('Item','Skill'):
                title=(value.get('Localization') or {}).get('Title') or {}
                cards[value['Id']]={'internalName':value.get('InternalName') or title.get('Text') or value['Id'],
                    'title':title.get('Text') or '', 'titleKey':title.get('Key') or '',
                    'type':value['Type'],'size':value.get('Size') or 'Small','artKey':value.get('ArtKey') or ''}
    return cards


def build_cache(state, card_cache, native_path, workers=4):
    state=Path(state).resolve()
    directory=state/'art'
    directory.mkdir(parents=True,exist_ok=True)
    catalog=json.loads((card_cache/'card_images.json').read_text(encoding='utf-8'))['cards']
    native=native_catalog(native_path)
    for override_file in (Path(__file__).with_name('card_art_sources.json'),state/'card-art-overrides.json'):
        if override_file.is_file():
            for cid,fields in json.loads(override_file.read_text(encoding='utf-8')).items():
                identity=native.get(cid) or {}
                if not identity or (fields.get('verified_art_key') and fields['verified_art_key']!=identity['artKey']):
                    continue
                if fields.get('verified_title') and fields['verified_title']!=identity['title']:
                    continue
                catalog[cid]=dict(catalog.get(cid) or {},**fields)
    translations_file=card_cache/'zh-CN.json'
    translations=json.loads(translations_file.read_text(encoding='utf-8')) if translations_file.exists() else {}
    texture_file=card_cache/'item_tex_map.json'
    textures=json.loads(texture_file.read_text(encoding='utf-8')) if texture_file.exists() else {}
    texture_dir=card_cache/'card_images'
    appearances=Counter()
    with sqlite3.connect((state/'runs.db').resolve().as_uri()+'?mode=ro',uri=True) as db:
        for items,skills in db.execute('SELECT items_json,skills_json FROM runs'):
            appearances.update({c['cardId'] for c in json.loads(items)+json.loads(skills)})
    by_key=defaultdict(list)
    for cid,info in native.items():
        if info['artKey']:
            by_key[info['artKey']].append(cid)
    completed={}
    failures=[]
    limited=threading.Event()
    # Preserve the exact native identity, including skills missing from the URL index.
    for cid in set(catalog)|set(appearances):
        current=dict(catalog.get(cid) or {})
        info=native.get(cid) or {}
        if not current.get('name') and info:
            current['name']=translations.get(info['titleKey']) or translations.get(hashlib.md5(info['title'].encode()).hexdigest()) or info['title'] or info['internalName']
        for field in ('internalName','type','size','artKey'):
            if not current.get(field) and info.get(field):current[field]=info[field]
        catalog[cid]=current

    def obtain(cid):
        info=catalog[cid]
        urls=[]
        for variant in [cid]+by_key.get((native.get(cid) or {}).get('artKey'),[]):
            for field in ('artLarge','art'):
                url=(catalog.get(variant) or {}).get(field)
                if url and url not in urls:urls.append(url)
        errors=[]
        # Reuse a real cached source image, including files copied before this fix.
        for url in urls:
            path=asset_path(directory,url)
            if path.is_file() and valid_image(path):
                return {'file':path.name,'source':url,'kind':'official_url'}
        if not limited.is_set():
            for url in urls:
                try:
                    content=fetch_image(url)
                    path=asset_path(directory,url)
                    write_thumbnail(path,content)
                    return {'file':path.name,'source':url,'kind':'official_url'}
                except HTTPError as error:
                    errors.append(str(error.code))
                    if error.code==429:
                        limited.set()
                        break
                except Exception as error:
                    errors.append(type(error).__name__)
        # A native filename or the existing per-card texture map is a verified fallback.
        native_info=native.get(cid) or {}
        candidates=[]
        key=native_info.get('artKey') or ''
        if key and Path(key).name==key:
            candidates.extend([texture_dir/key,texture_dir/(key+'.png')])
        for name in (native_info.get('internalName'),native_info.get('title'),info.get('internalName')):
            mapped=textures.get(name)
            if mapped and Path(mapped).name==mapped:
                candidates.append(texture_dir/(mapped if mapped.endswith('.png') else mapped+'.png'))
        for source in candidates:
            if source.is_file():
                try:
                    path=asset_path(directory,str(source))
                    write_thumbnail(path,source.read_bytes())
                    return {'file':path.name,'source':str(source),'kind':'native_texture'}
                except Exception as error:
                    errors.append(type(error).__name__)
        return {'error':','.join(errors) or 'no_verified_source'}

    targets=sorted(catalog,key=lambda cid:(-appearances[cid],cid))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending={pool.submit(obtain,cid):cid for cid in targets}
        for index,future in enumerate(as_completed(pending),1):
            cid=pending[future]
            result=future.result()
            if result.get('file'):completed[cid]=result
            else:failures.append({'cardId':cid,'name':catalog[cid].get('name',cid),
                                  'type':catalog[cid].get('type','unknown'),'appearances':appearances[cid],**result})
            if index%100==0:print(json.dumps({'checked':index,'total':len(targets),'ready':len(completed),'missing':len(failures)}),flush=True)
    result={'cards':catalog,'art':completed,'missing':sorted(failures,key=lambda r:-r['appearances']),
            'summary':{'recorded_unique_cards':len(appearances),'recorded_ready':sum(cid in completed for cid in appearances),
                       'catalog_ready':len(completed),'catalog_total':len(catalog),'limited':limited.is_set()},
            'updated_at':time.time()}
    target=state/'card-assets.json'
    temporary=target.with_suffix('.tmp')
    temporary.write_text(json.dumps(result,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
    temporary.replace(target)
    print(json.dumps(result['summary'],ensure_ascii=False),flush=True)
    print(json.dumps({'remaining_recorded_missing':[r for r in result['missing'] if r['appearances']]},ensure_ascii=False),flush=True)
    return result


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--state',type=Path,required=True)
    parser.add_argument('--card-cache',type=Path,required=True)
    parser.add_argument('--native',type=Path,required=True)
    parser.add_argument('--workers',type=int,default=4,choices=range(1,9))
    args=parser.parse_args()
    build_cache(args.state,args.card_cache,args.native,args.workers)
