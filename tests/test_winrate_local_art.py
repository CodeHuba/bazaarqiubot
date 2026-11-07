import importlib

import pytest
from PIL import Image

from test_bz_db_command import load_plugin_module


@pytest.fixture
def art(monkeypatch, tmp_path):
    load_plugin_module(monkeypatch)
    module = importlib.import_module('plugins.bazaar_plugin.winrate_image')
    monkeypatch.setattr(module, 'LOCAL_ART', tmp_path)
    return module, tmp_path


@pytest.mark.parametrize('size,dimensions', [('Small',(100,200)),('Medium',(200,200)),('Large',(300,200))])
def test_local_art_loads_native_shape_without_network(art,monkeypatch,size,dimensions):
    module,folder=art
    name='a'*64+'.webp'
    Image.new('RGB', dimensions, 'orange').save(folder/name)
    monkeypatch.setattr(module,'_load_remote_art',lambda *a,**k:pytest.fail('local art must not use network'))
    image=module.load_art('/static/comp-art/'+name,size)
    assert image.size==(dimensions[0]*2,400) and image.mode=='RGBA'


def test_missing_local_art_is_retried_when_file_arrives_and_refreshes_on_replace(art):
    module,folder=art
    name='b'*64+'.webp';url='/static/comp-art/'+name
    assert module.load_art(url,'Medium') is None
    Image.new('RGB',(200,200),'red').save(folder/name,lossless=True)
    assert module.load_art(url,'Medium').getpixel((0,0))[:3]==(255,0,0)
    Image.new('RGB',(200,200),'blue').save(folder/name,lossless=True)
    assert module.load_art(url,'Medium').getpixel((0,0))[:3]==(0,0,255)


def test_corrupt_and_wrong_ratio_local_files_remain_missing(art):
    module,folder=art
    name='c'*64+'.webp';url='/static/comp-art/'+name
    (folder/name).write_bytes(b'not an image')
    assert module.load_art(url,'Large') is None
    Image.new('RGB',(200,200)).save(folder/name)
    assert module.load_art(url,'Large') is None


@pytest.mark.parametrize('url',['/static/comp-art/../../secret.webp','/etc/passwd',
    '/static/comp-art/'+'a'*64+'.webp?path=../secret','file:///etc/passwd'])
def test_unapproved_local_paths_never_open_files(art,monkeypatch,url):
    module,_=art
    calls=[]
    monkeypatch.setattr(module,'_load_remote_art',lambda *a,**k:None)
    monkeypatch.setattr(module.Image,'open',lambda *a,**k:calls.append(a) or pytest.fail('unsafe file access'))
    assert module.load_art(url,'Medium') is None
    assert not calls


def test_external_cdn_loading_is_preserved(art,monkeypatch,tmp_path):
    module,_=art
    calls=[];sentinel=object()
    monkeypatch.setattr(module,'_load_remote_art',lambda *a,**k:calls.append((a,k)) or sentinel)
    url='https://s.bazaardb.gg/v1/z20.0/example@400L.webp'
    assert module.load_art(url,'Large',cache=tmp_path) is sentinel
    assert calls==[((url,'Large'),dict(cache=tmp_path))]


def test_query_image_uses_local_art_instead_of_missing_placeholder(art,monkeypatch,tmp_path):
    module,folder=art
    name='d'*64+'.webp'
    Image.new('RGB',(200,200),'orange').save(folder/name,lossless=True)
    result=dict(card_names=['原子钟'],card_details=[dict(size='Medium',img='/static/comp-art/'+name)],total=10,ten_win=6)
    with Image.open(module.render_winrate(result,tmp_path/'out')) as image:
        assert image.getpixel((540,400))==(255,165,0)
