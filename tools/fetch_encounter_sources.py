"""Index public encounter art by game entity ID; retain verifiable appearance sources."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time

import requests

UUID = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'

def hidden(raw):
    return any(s in raw.get('InternalName','').lower() for s in ('tutorial','[debug]','start run','level up'))

def decode_rsc(html):
    result=[]
    for value in re.findall(r'self\.__next_f\.push\((.*?)\)</script>', html, re.S):
        try:
            chunk=json.loads(value)
            if len(chunk)>1 and isinstance(chunk[1],str):result.append(chunk[1])
        except (ValueError,TypeError):pass
    return ''.join(result)

def walk(value):
    yield value
    if isinstance(value,dict):
        for child in value.values():yield from walk(child)
    elif isinstance(value,list):
        for child in value:yield from walk(child)

def parse_art(html, raw_by_id):
    text=decode_rsc(html);decoder=json.JSONDecoder();out={}
    for match in re.finditer(r'\["\$","\$1","card:('+UUID+r'):',text):
        entity_id=match[1]
        if entity_id not in raw_by_id:continue
        try:tree,_=decoder.raw_decode(text[match.start():])
        except ValueError:continue
        urls=[v['src'] for v in walk(tree) if isinstance(v,dict) and isinstance(v.get('src'),str)
              and v['src'].startswith('https://s.bazaardb.gg/v1/')]
        hrefs=[v['href'] for v in walk(tree) if isinstance(v,dict) and isinstance(v.get('href'),str) and v['href'].startswith('/card/')]
        if urls:
            url=re.sub(r'@\d+[SL]?\.webp','@400.webp',urls[0])
            out[entity_id]={'art':url,'art_fallback':url.replace('_p@','@'),
                            'art_key':raw_by_id[entity_id].get('ArtKey',''),
                            'url':'https://bazaardb.gg'+hrefs[0] if hrefs else ''}
    for match in re.finditer(r'\{"baseId":',text):
        try:d,_=decoder.raw_decode(text[match.start():])
        except ValueError:continue
        entity_id=d.get('baseId');url=d.get('art','')
        if entity_id in raw_by_id and url.startswith('https://s.bazaardb.gg/v1/'):
            out[entity_id]={'art':url,'art_key':raw_by_id[entity_id].get('ArtKey',''),
                            'url':'https://bazaardb.gg'+d.get('url','')}
    return out

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--database',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--cache',required=True)
    parser.add_argument('--news')
    parser.add_argument('--appearance')
    args=parser.parse_args()
    cache=Path(args.cache);cache.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(Path(args.database).resolve().as_uri()+'?mode=ro',uri=True) as db:
        raw={d['Id']:d for (s,) in db.execute('SELECT Data FROM cards') for d in [json.loads(s)]}
    entries=[d for d in raw.values() if d.get('Type') in ('EventEncounter','PedestalEncounter') and not hidden(d)]
    names=sorted({((d.get('Localization') or {}).get('Title') or {}).get('Text') or d['InternalName'] for d in entries})
    failure=[]
    def fetch(name):
        path=cache/(hashlib.sha256(name.encode()).hexdigest()+'.html')
        if path.is_file():return name,path.read_text(encoding='utf-8')
        try:
            response=requests.get('https://bazaardb.gg/search',params={'q':name,'c':'all'},timeout=20)
            response.raise_for_status()
            path.write_text(response.text,encoding='utf-8')
            time.sleep(.5)
            return name,response.text
        except requests.RequestException as error:
            failure.append({'name':name,'error':str(error)[:180]})
            return name,''
    artwork={}
    with ThreadPoolExecutor(max_workers=3) as executor:
        for index,(name,html) in enumerate(executor.map(fetch,names),1):
            artwork.update(parse_art(html,raw))
            if index%25==0:print(json.dumps({'queried':index,'total':len(names),'art':len(artwork),'failures':len(failure)}),flush=True)
    days={}
    if args.news:
        news=json.loads(Path(args.news).read_text(encoding='utf-8'))['appnews']['newsitems']
        by_name={}
        for d in entries:
            for name in (d.get('InternalName'),((d.get('Localization') or {}).get('Title') or {}).get('Text')):
                if name:
                    for alias in {name.lower(),re.sub(r'^(?:a|the)\s+','',name.lower())}:
                        by_name.setdefault(alias,[]).append(d['Id'])
        for article in sorted(news,key=lambda n:n['date']):
            # Only explicit standalone schedule statements under a named encounter.
            blocks=re.split(r'\[p\]\[b\]([^\[]+)\[/b\]\[/p\]',article.get('contents',''))
            for index in range(1,len(blocks)-1,2):
                name=re.sub(r'\s*\((?:Encounter|Merchant|Event)\)\s*$','',blocks[index],flags=re.I).strip()
                ids=by_name.get(name.lower(),[])
                if not ids:continue
                sentences=re.findall(r'\[p\](.*?)\[/p\]',blocks[index+1],re.S)
                for sentence in sentences:
                    sentence=re.sub(r'\[/?[^\]]+\]','',sentence)
                    sentence=re.sub(r'\s*\(from[^)]*\)\s*$','',sentence.strip(),flags=re.I)
                    m=re.fullmatch(r'(?:Spawns? on )?Days?\s+(\d+)(?:\s*(?:to|[-–])\s*(\d+)|(\+))?(?: only)?',sentence,re.I)
                    all_days=re.fullmatch(r'All Days',sentence,re.I)
                    if not m and not all_days:continue
                    label='Day 1+' if all_days else 'Day '+m[1]+(('–'+m[2]) if m[2] else ('+' if m[3] else ''))
                    for entity_id in ids:
                        days[entity_id]={'day_label':label,'kind':'official_schedule','source_title':article['title'],
                                         'source_url':article['url'],'source_date':datetime.fromtimestamp(article['date'],timezone.utc).isoformat()}
    if args.appearance:
        days.update(json.loads(Path(args.appearance).read_text(encoding='utf-8')).get('entries',{}))
    # Art is shared only when the current game ArtKey is exactly identical.
    by_art_key={d['art_key']:d for d in artwork.values() if d.get('art_key')}
    for d in raw.values():
        if d['Id'] not in artwork and d.get('ArtKey') in by_art_key:
            artwork[d['Id']]={**by_art_key[d['ArtKey']], 'shared_art':True}
    payload={'retrieved_at':datetime.now(timezone.utc).isoformat(),'source':'https://bazaardb.gg/search',
             'entries':artwork,'appearance':days,'failures':failure}
    target=Path(args.output);target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'public_names':len(names),'art_entries':len(artwork),'appearance_entries':len(days),'failures':len(failure)}),flush=True)

if __name__=='__main__':main()
