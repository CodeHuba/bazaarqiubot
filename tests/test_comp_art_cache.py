import importlib.util
import io
import json
from pathlib import Path
import sqlite3

from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('comp_art_cache',ROOT/'tools/comp_preview/cache_art.py')
art=importlib.util.module_from_spec(spec)
spec.loader.exec_module(art)


def fixture(tmp_path,catalog,native):
    state=tmp_path/'state';state.mkdir()
    cache=tmp_path/'catalog';cache.mkdir()
    (cache/'card_images.json').write_text(json.dumps({'cards':catalog}),encoding='utf-8')
    with sqlite3.connect(state/'runs.db') as db:
        db.execute('CREATE TABLE runs(items_json TEXT,skills_json TEXT)')
        db.execute('INSERT INTO runs VALUES (?,?)',(json.dumps([{'cardId':cid} for cid in catalog]),'[]'))
    database=tmp_path/'native.db'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE cards(Data TEXT)')
        db.executemany('INSERT INTO cards VALUES (?)',[(json.dumps(row),) for row in native])
    return state,cache,database


def picture():
    output=io.BytesIO()
    Image.new('RGB',(40,40),'red').save(output,format='WEBP')
    return output.getvalue()


def test_native_item_shader_alpha_is_discarded_before_resizing(tmp_path):
    source=Image.new('RGBA',(1024,1024),(240,50,30,0))
    content=io.BytesIO();source.save(content,format='PNG')
    path=tmp_path/'native.webp'
    art.write_thumbnail(path,content.getvalue(),opaque=True)
    with Image.open(path) as image:
        assert image.size==(240,240)
        assert image.mode=='RGB'
        red,green,blue=image.getpixel((120,120))
        assert red>220 and green<70 and blue<50


def test_transparent_skill_art_keeps_its_alpha(tmp_path):
    source=Image.new('RGBA',(1024,1024),(240,50,30,0))
    content=io.BytesIO();source.save(content,format='PNG')
    path=tmp_path/'skill.webp'
    art.write_thumbnail(path,content.getvalue())
    with Image.open(path) as image:
        assert image.mode=='RGBA'
        assert image.getchannel('A').getextrema()==(0,0)


def test_native_item_fallback_rebuilds_a_cached_transparent_thumbnail(tmp_path,monkeypatch):
    native=[{'Id':'native-item','Type':'Item','InternalName':'Native Item','ArtKey':'native.png'}]
    state,cache,database=fixture(tmp_path,{'native-item':None},native)
    textures=cache/'card_images';textures.mkdir()
    source=textures/'native.png'
    Image.new('RGBA',(1024,1024),(240,50,30,0)).save(source)
    directory=state/'art';directory.mkdir()
    old=art.asset_path(directory,str(source))
    art.write_thumbnail(old,source.read_bytes())
    monkeypatch.setattr(art,'fetch_image',lambda url:(_ for _ in ()).throw(AssertionError('native fallback has no URL')))
    result=art.build_cache(state,cache,database)
    entry=result['art']['native-item']
    assert entry['kind']=='native_texture' and entry['file']!=old.name
    with Image.open(directory/entry['file']) as image:
        assert image.mode=='RGB' and image.getpixel((120,120))[0]>220


def test_all_recorded_items_and_skills_are_cached_and_reused(tmp_path,monkeypatch):
    urls={cid:'https://s.bazaardb.gg/v1/z19.0/'+cid+'@400.webp' for cid in ('item','skill')}
    catalog={cid:{'type':'Item' if cid=='item' else 'Skill','name':cid,'artLarge':url} for cid,url in urls.items()}
    state,cache,native=fixture(tmp_path,catalog,[])
    requests=[]
    monkeypatch.setattr(art,'fetch_image',lambda url:(requests.append(url),picture())[1])
    result=art.build_cache(state,cache,native)
    assert result['summary']['recorded_ready']==2
    assert all(art.valid_image(state/'art'/entry['file']) for entry in result['art'].values())
    assert len(requests)==2
    art.build_cache(state,cache,native)
    assert len(requests)==2


def test_shared_native_art_key_uses_verified_asset_and_parallel_writes_are_safe(tmp_path,monkeypatch):
    catalog={'original':{'artLarge':'https://s.bazaardb.gg/v1/z19.0/shared@400.webp'},'variant':None}
    native=[{'Id':cid,'Type':'Item','ArtKey':'same-native-asset','InternalName':cid} for cid in catalog]
    state,cache,database=fixture(tmp_path,catalog,native)
    monkeypatch.setattr(art,'fetch_image',lambda url:picture())
    result=art.build_cache(state,cache,database,workers=2)
    assert result['summary']['recorded_ready']==2
    assert result['art']['original']['file']==result['art']['variant']['file']


def test_native_texture_fallback_and_missing_source_are_reported(tmp_path,monkeypatch):
    catalog={'local':None,'missing':None}
    native=[{'Id':cid,'Type':'Skill','InternalName':cid,'ArtKey':'Icon_'+cid+'.png'} for cid in catalog]
    state,cache,database=fixture(tmp_path,catalog,native)
    images=cache/'card_images';images.mkdir()
    (images/'Icon_local.png').write_bytes(picture())
    monkeypatch.setattr(art,'fetch_image',lambda url:(_ for _ in ()).throw(AssertionError('No URL must be invented')))
    result=art.build_cache(state,cache,database)
    assert result['art']['local']['kind']=='native_texture'
    assert 'missing' not in result['art']
    assert result['missing'][0]['error']=='no_verified_source'
    assert result['cards']['missing']['type']=='Skill'


def test_corrupt_upstream_image_is_not_published(tmp_path,monkeypatch):
    state,cache,native=fixture(tmp_path,{'a':{'art':'https://s.bazaardb.gg/v1/z19.0/bad.webp'}},[])
    monkeypatch.setattr(art,'fetch_image',lambda url:b'not an image')
    result=art.build_cache(state,cache,native)
    assert result['art']=={}
    assert result['missing'][0]['cardId']=='a'
