"""Paginated merchant/event sheets, using native artwork and explicit selection rules."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import tempfile

from .monster_image import Sheet, load_art, COLORS, TEXT, MUTED, LINE, GOLD, _font_path
from .card_preview import QUALITY_LABELS

OUTPUT=Path(__file__).resolve().parents[2]/'data/charts/encounters'
VERSION='encounter-sheet-2'
TEAL='#81d5c3'

def _load(url,fallback='',size=None,cache=None):
    kwargs={'cache':cache} if cache is not None else {}
    art=load_art(url,size,**kwargs)
    if art is not None and art.getchannel('A').getbbox() is None:art=None
    if art is None and fallback and fallback!=url:art=load_art(fallback,size,**kwargs)
    return art

def _prepare(document):
    measure=Sheet(10);blocks=[]
    for original in document['blocks']:
        block=deepcopy(original)
        if block['kind']=='option':
            option=block['option'];lines=[]
            for line in option['lines']:lines.extend(measure.wrap(line,960,27))
            for start in range(0,max(1,len(lines)),36):
                part=deepcopy(block);part['lines']=lines[start:start+36]
                if start:part['option']['name']+='（续）'
                minimum=276 if option['size']=='Small' and option['type']=='Item' else 218 if option['type'] in ('Item','Skill') else 180
                part['height']=max(minimum,114+len(part['lines'])*39)
                blocks.append(part)
        elif block['kind']=='table':
            block['height']=110+len(block['rows'])*70+80
            blocks.append(block)
        else:
            lines=[]
            for line in block['lines']:lines.extend(measure.wrap(line,1232,28))
            for start in range(0,max(1,len(lines)),40):
                part=deepcopy(block);part['lines']=lines[start:start+40]
                part['height']=max(100,42+len(part['lines'])*42)
                blocks.append(part)
    description=measure.wrap(document['description'],1000,28)
    header=max(356,232+len(description)*38)
    fact_rows=[];y=header+38
    for index in range(0,len(document['facts']),2):
        pair=document['facts'][index:index+2]
        cells=[(label,measure.wrap(value,598,26)) for label,value in pair]
        height=52+max(len(lines) for _,lines in cells)*36
        fact_rows.append({'y':y,'height':height,'cells':cells});y+=height+16
    pages=[];page={'full':True,'header':header,'facts':fact_rows,'blocks':[],'end':y+18}
    previous=''
    for block in blocks:
        section=previous!=block['title'];needed=block['height']+18+(65 if section else 0)
        if page['blocks'] and page['end']+needed+120>2400:
            pages.append(page);page={'full':False,'blocks':[],'end':300};section=True
        block['section']=section;block['y']=page['end']+(65 if section else 0)
        page['blocks'].append(block);page['end']=block['y']+block['height']+18;previous=block['title']
    pages.append(page)
    return pages,description

def render_document(document,output=OUTPUT,cache=None):
    pages,description=_prepare(document)
    options={b['option']['id']:b['option'] for b in document['blocks'] if b['kind']=='option'}
    with ThreadPoolExecutor(max_workers=4) as pool:
        main=pool.submit(_load,document['art'],document['art_fallback'],None,cache)
        jobs={key:pool.submit(_load,o['art'],o['art_fallback'],o['size'] if o['type']=='Item' else None,cache) for key,o in options.items()}
        portrait=main.result();art={key:job.result() for key,job in jobs.items()}
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    fingerprint=hashlib.sha256((document['stamp']+VERSION+_font_path()).encode()).hexdigest()[:24]
    targets=[output/(fingerprint+'-'+str(i+1)+'.png') for i in range(len(pages))]
    if portrait is not None and all(a is not None for a in art.values()) and all(p.is_file() for p in targets):
        return [str(p.resolve()) for p in targets]
    kind='商店' if document['kind']=='merchant' else '遭遇事件'
    for index,(page,target) in enumerate(zip(pages,targets)):
        s=Sheet(page['end']+118)
        s.draw.rectangle((0,0,1440,7),fill=TEAL)
        s.text(64,32,'巴扎丘丘Bot / 巴扎百科',25,MUTED)
        s.text(1260,32,f'{index+1} / {len(pages)}',24,TEAL)
        if page['full']:
            s.art(portrait,(64,106,242,242),LINE)
            s.text(346,106,kind,25,TEAL,True)
            size=58
            while s.draw.textlength(document['name'],font=s.font(size,True))>1010 and size>28:size-=2
            s.text(346,155,document['name'],size,TEXT,True)
            for i,line in enumerate(description):s.text(346,238+i*38,line,28,MUTED)
            for row in page['facts']:
                for ci,(label,lines) in enumerate(row['cells']):
                    x=64+ci*668;y=row['y'];s.panel((x,y,x+644,y+row['height']),radius=16)
                    s.text(x+22,y+10,label,22,MUTED)
                    for li,line in enumerate(lines):s.text(x+22,y+44+li*36,line,26,TEXT)
        else:
            s.art(portrait,(64,106,148,148),LINE)
            s.text(250,115,document['name']+'（续）',42,TEXT,True)
            s.text(250,192,kind+' · '+page['blocks'][0]['title'],26,MUTED)
        for block in page['blocks']:
            y=block['y'];height=block['height']
            if block['section']:
                s.draw.rectangle((64,y-53,70,y-19),fill=TEAL)
                for li,line in enumerate(s.wrap(block['title'],1260,32)):
                    if li==0:s.text(88,y-59,line,32,TEXT,True)
            s.panel((64,y,1376,y+height),radius=18)
            if block['kind']=='text':
                for li,line in enumerate(block['lines']):s.text(88,y+20+li*42,line,28,TEXT)
            elif block['kind']=='table':
                count=len(block['columns']);width=916/count
                for ci,column in enumerate(block['columns']):s.text(460+width*ci+20,y+20,column,25,MUTED,True)
                for ri,row in enumerate(block['rows']):
                    yy=y+85+ri*70;s.draw.line((86,yy-10,1352,yy-10),fill=LINE)
                    label_size=25
                    while s.draw.textlength(row['label'],font=s.font(label_size))>350 and label_size>18:label_size-=1
                    s.text(88,yy,row['label'],label_size,TEXT)
                    for ci,value in enumerate(row['values']):s.text(460+width*ci+20,yy,value,27,GOLD,True)
                for li,line in enumerate(s.wrap(block['note'],1240,23)):s.text(88,y+height-66+li*31,line,23,MUTED)
            else:
                option=block['option'];quality=COLORS.get(option['quality'],GOLD)
                w,h={'Small':(112,224),'Medium':(170,170),'Large':(234,156)}.get(option['size'],(170,170)) if option['type']=='Item' else (164,164) if option['type']=='Skill' else (128,128)
                s.art(art[option['id']],(84+(244-w)//2,y+26,w,h),quality,option['type']=='Skill')
                title_size=34
                while s.draw.textlength(option['name'],font=s.font(title_size,True))>755 and title_size>22:title_size-=1
                s.text(358,y+18,option['name'],title_size,TEXT,True)
                cost_size=24
                cost_width=s.draw.textlength(option['cost'],font=s.font(cost_size))
                s.text(1348-cost_width,y+24,option['cost'],cost_size,GOLD)
                badge=QUALITY_LABELS.get(option['quality'],option['quality'])
                if option['type'] in ('Item','Skill'):badge+=' · '+('技能' if option['type']=='Skill' else {'Small':'小型','Medium':'中型','Large':'大型'}.get(option['size'],''))
                s.text(358,y+66,badge,22,quality)
                for li,line in enumerate(block['lines']):s.text(358,y+106+li*39,line,27,TEXT)
        y=page['end']+10
        s.draw.line((64,y,1376,y),fill=LINE)
        s.text(64,y+20,'官方中文与游戏规则 · #bz db '+('商店 ' if document['kind']=='merchant' else '事件 ')+document['name'],22,MUTED)
        if document.get('appearance_source'):s.text(64,y+56,'日期来源：'+document['appearance_source'],21,MUTED)
        fd,name=tempfile.mkstemp(dir=output,suffix='.png');os.close(fd)
        try:s.image.save(name);Path(name).replace(target)
        finally:Path(name).unlink(missing_ok=True)
    return [str(p.resolve()) for p in targets]
