import json
import sqlite3

from test_runs_list_performance import RunsQuery,runs_query


def setup_query(tmp_path,monkeypatch):
    from plugins.bazaar_plugin import card_image_helper,runs_metadata
    images=tmp_path/'images.json';images.write_text('{"cards":{}}')
    monkeypatch.setattr(card_image_helper,'_CACHE_FILE',images)
    monkeypatch.setattr(card_image_helper,'_CACHE',None)
    monkeypatch.setattr(card_image_helper,'_CACHE_SIGNATURE',None)
    mapping=tmp_path/'mapping.json';mapping.write_text('{"q":{"name":"Item","type":"Item"}}')
    translations=tmp_path/'translations.json';translations.write_text('{"Item":"物品"}',encoding='utf-8')
    alias=tmp_path/'alias.json';alias.write_text('{}')
    monkeypatch.setattr(runs_query,'ALIAS_FILE',str(alias))
    native=tmp_path/'GameData.db'
    with sqlite3.connect(native) as conn:
        conn.execute('CREATE TABLE cards(Data TEXT)')
        conn.execute('INSERT INTO cards VALUES (?)',(json.dumps({'Id':'q','Size':'Large'}),))
    monkeypatch.setenv('GAMEDATA_DB',str(native))
    def query():return RunsQuery(db_path=str(tmp_path/'runs.db'),mapping_path=str(mapping),translations_path=str(translations))
    runs_metadata._cache.clear()
    return query,translations,native,images


def test_metadata_is_loaded_once_but_maps_and_connections_are_isolated(tmp_path,monkeypatch):
    factory,_,_,_=setup_query(tmp_path,monkeypatch)
    original=RunsQuery._load_metadata;loads=[]
    def load(self):loads.append(1);original(self)
    monkeypatch.setattr(RunsQuery,'_load_metadata',load)
    first=factory();first.load()
    second=factory();second.load()
    assert loads==[1]
    assert first.conn is not second.conn
    assert second.size_map['q']=='Large'
    first.card_mapping['q']['name']='mutated'
    first.name_to_ids['item'].append('wrong')
    first.conn.close()
    assert second.conn.execute('SELECT 1').fetchone()==(1,)
    third=factory();third.load()
    assert third.card_mapping['q']['name']=='Item'
    assert third.name_to_ids['item']==['q']
    second.conn.close();third.conn.close()


def test_translations_native_sizes_and_images_invalidate_by_source_version(tmp_path,monkeypatch):
    factory,translations,native,images=setup_query(tmp_path,monkeypatch)
    first=factory();first.load();first.conn.close()
    translations.write_text('{"Item":"新物品名称"}',encoding='utf-8')
    with sqlite3.connect(native) as conn:
        conn.execute('UPDATE cards SET Data=?',(json.dumps({'Id':'q','Size':'Small','Heroes':['Common','Hero8']}),))
    images.write_text(json.dumps({'cards':{'q':{'internalName':'Item','name':'官方新名称','type':'Item','artLarge':'/new.webp'}}}),encoding='utf-8')
    second=factory();second.load()
    assert second.en_to_zh['Item']=='新物品名称'
    assert second.size_map['q']=='Small'
    assert second.card_heroes['q']==['Common','Hero8']
    assert second.card_display_info_cached('q')['name']=='官方新名称'
    assert second.card_display_info_cached('q')['img']=='/new.webp'
    second.conn.close()
