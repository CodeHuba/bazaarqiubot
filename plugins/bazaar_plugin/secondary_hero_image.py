"""Portrait reports for the temporary secondary-hero event."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import uuid

from PIL import Image, ImageDraw, ImageFont

from .monster_image import _font_path

ASSETS = Path(__file__).parent/'assets/hero_portraits'
PORTRAITS = {
    'Vanessa': ('vanessa', '#89bfff'), 'Dooley': ('dooley', '#8ce1c1'),
    'Mak': ('mak', '#cfadf3'), 'Pygmalien': ('pygmalien', '#dba5cf'),
    'Stelle': ('stelle', '#aeb8fa'), 'Jules': ('jules', '#f29d9d'),
    'Karnok': ('karnok', '#dfb08d'), 'The Dragons': ('the-dragons', '#e9c27b'),
}
CST = timezone(timedelta(hours=8))
OUTPUT = Path(__file__).resolve().parents[2]/'data/charts/secondary-heroes'
BG, PANEL, TEXT, MUTED = '#101621', '#192332', '#f3f0e8', '#a8b5c7'
GOLD, GREEN, LINE = '#e9c27b', '#8ce1c1', '#303e50'


def china_time(now=None):
    now = now or datetime.now(CST)
    return now.replace(tzinfo=CST) if now.tzinfo is None else now.astimezone(CST)


class Canvas:
    def __init__(self, height):
        self.scale = 2
        self.image = Image.new('RGB', (2160, height*2), BG)
        self.draw = ImageDraw.Draw(self.image)
        self.fonts = {}

    def font(self, size, bold):
        key = size,bold
        if key not in self.fonts:
            self.fonts[key] = ImageFont.truetype(_font_path(bold), size*2)
        return self.fonts[key]

    def text(self, x, y, value, size=24, color=TEXT, bold=False, anchor='lt', width=None):
        value = str(value)
        while width and size > 14 and self.font(size,bold).getlength(value) > width*2:
            size -= 1
        self.draw.text((x*2,y*2),value,font=self.font(size,bold),fill=color,anchor=anchor)

    def rect(self, box, fill, radius=16, outline=None):
        self.draw.rounded_rectangle(tuple(round(v*2) for v in box),radius*2,fill,outline,width=2)

    def portrait(self, hero, x, y, width, height):
        slug,color = PORTRAITS[hero]
        try:
            with Image.open(ASSETS/(slug+'.webp')) as source:
                art = source.convert('RGBA')
            bounds = art.getbbox()
            if not bounds:
                raise ValueError('empty portrait')
            art = art.crop(bounds)
            art.thumbnail((width*2,height*2),Image.Resampling.LANCZOS)
            self.image.paste(art,(round((x+width/2)*2-art.width/2),round((y+height)*2-art.height)),art)
        except (OSError,ValueError):
            self.rect((x,y,x+width,y+height),'#263548',12)
            self.text(x+width/2,y+height/2,hero[0],30,color,True,'mm')


def pct(value):
    return f'{value:.1%}' if value is not None else '—'


def heading(c, doc, title):
    c.text(50,42,'巴扎丘Bot  /  赛季末活动',24,GOLD,True)
    if doc['preview']:
        c.rect((825,30,1032,78),'#352e27',12)
        c.text(929,54,'测试预览',22,GOLD,True,'mm')
    c.text(50,116,title,48,TEXT,True,width=790 if doc['hero'] else 980)
    c.text(52,190,'活动已结束 · 历史数据' if doc['ended'] else doc['event']['name'],24,GOLD)
    scope = ' / '.join(doc['event']['phases'])
    c.text(52,232,f'活动版本 {scope} · 全部段位 · 按'+('10胜率' if doc['order']=='win' else '选取率')+'排序',23,MUTED,width=974)
    stamp = china_time(datetime.fromisoformat(doc['queried_at'])).strftime('%Y-%m-%d %H:%M')
    c.text(52,271,'查询时间 '+stamp+' 北京时间',22,MUTED)


def summary(c, total, valid, wins):
    c.rect((48,322,1032,446),PANEL)
    for x,label,value,color in (
        (76,'有效识别场次',f'{valid:,}',TEXT),
        (414,'识别覆盖率',pct(valid/total if total else None),TEXT),
        (748,'整体10胜率',pct(wins/valid if valid else None),GREEN),
    ):
        c.text(x,343,label,22,MUTED)
        c.text(x,386,value,34,color,True,width=260)


def footer(c, doc, records, y):
    total = sum(r['total'] for r in records)
    missing = sum(r['missing'] for r in records)
    conflict = sum(r['conflict'] for r in records)
    floor = '1胜及以上' if doc['event']['min_wins'] else '含0胜'
    c.text(52,y,'选取率 = 该副角色场次 ÷ 该主角色有效识别场次',22,MUTED)
    c.text(52,y+38,'10胜率 = 该搭配10胜场次 ÷ 该搭配场次',22,MUTED)
    c.text(52,y+78,f'已收录{floor}样本 {total:,}场；不足30场标记小样本。',21,MUTED,width=975)
    c.text(52,y+116,f'未识别 {missing:,}场 · 冲突 {conflict:,}场，均不计入排行。',21,MUTED,width=975)
    c.text(52,y+154,'基于已收录样本，非全服统计；选取率不是三选一候选的被选概率。',20,MUTED,width=975)
    if doc['source_note']:
        c.text(52,y+192,doc['source_note'],19,GOLD,width=975)
    else:
        start = china_time(datetime.fromisoformat(doc['event']['start_at'])).strftime('%Y-%m-%d %H:%M')
        end = doc['event'].get('end_at')
        end_label = china_time(datetime.fromisoformat(end)).strftime('%m-%d %H:%M') if end else '结束待公告'
        c.text(52,y+192,f'活动范围：{start} 至 {end_label}（北京时间）',19,MUTED,width=975)
    c.draw.line((104,(y+236)*2,2056,(y+236)*2),fill=LINE,width=2)
    hero = next((r['name'] for r in records if r['hero']==doc['hero']),'')
    c.text(52,y+256,'#bz 副角色'+(' '+hero if hero else '')+(' 胜率' if doc['order']=='win' else ''),23,GOLD,True)
    c.text(1028,y+258,'活动独立查询',20,MUTED,anchor='rt')


def render_detail(doc):
    record = next(r for r in doc['heroes'] if r['hero']==doc['hero'])
    c = Canvas(1660)
    heading(c,doc,record['name']+' · 副角色排行')
    c.portrait(record['hero'],850,105,170,180)
    summary(c,record['total'],record['valid'],record['wins'])
    c.text(70,478,'副角色',23,MUTED)
    for x,label in ((660,'选取率'),(782,'场次'),(896,'10胜场'),(1010,'10胜率')):
        c.text(x,478,label,23,MUTED,anchor='rt')
    maximum = max((row['total'] for row in record['rows']),default=0)
    for i,row in enumerate(record['rows']):
        y = 526+i*112
        active = row['total'] > 0
        c.rect((48,y,1032,y+100),PANEL,14,GOLD if i==0 and active else None)
        c.text(72,y+25,f'{i+1:02}' if active else '—',26,GOLD if i<3 and active else MUTED,True)
        c.portrait(row['hero'],120,y+4,92,92)
        c.text(232,y+17,row['name'],27,TEXT,True,width=294)
        if 0 < row['total'] < 30 or not active:
            c.text(232,y+62,'小样本' if active else '暂无数据',18,GOLD if active else MUTED)
        else:
            c.rect((232,y+65,522,y+73),LINE,4)
            c.rect((232,y+65,232+290*row['total']/maximum,y+73),GOLD if i==0 else '#527e78',4)
        c.text(660,y+29,pct(row['pick_rate']),28,GOLD if i==0 and active else TEXT,True,'rt',width=128)
        c.text(782,y+31,f"{row['total']:,}",25,TEXT,anchor='rt',width=112)
        c.text(896,y+31,f"{row['wins']:,}",25,TEXT,anchor='rt',width=104)
        c.text(1010,y+31,pct(row['win_rate']),27,GREEN if active else MUTED,True,'rt',width=104)
    footer(c,doc,[record],1340)
    return c


def render_overview(doc):
    c = Canvas(2080)
    heading(c,doc,'副角色 · 全职业概览')
    summary(c,sum(r['total'] for r in doc['heroes']),sum(r['valid'] for r in doc['heroes']),sum(r['wins'] for r in doc['heroes']))
    for i,record in enumerate(doc['heroes']):
        x,y = 48+(i%2)*504,484+(i//2)*310
        c.rect((x,y,x+480,y+290),PANEL,18)
        c.portrait(record['hero'],x+14,y+10,70,78)
        c.text(x+102,y+22,record['name'],27,TEXT,True,width=340)
        c.text(x+102,y+64,f"有效 {record['valid']:,}/{record['total']:,}场",20,MUTED,width=340)
        for xx,label in ((x+28,'热门副角色'),(x+328,'选取率'),(x+454,'10胜率')):
            c.text(xx,y+109,label,19,MUTED,anchor='rt' if xx>x+100 else 'lt')
        for j,row in enumerate(record['rows'][:3]):
            yy = y+151+j*43
            c.text(x+28,yy,row['name'],22,TEXT,width=192)
            if 0<row['total']<30:
                c.text(x+184,yy+3,'小样本',14,GOLD)
            c.text(x+328,yy,pct(row['pick_rate']),22,GOLD if j==0 and row['total'] else TEXT,True,'rt',width=105)
            c.text(x+454,yy,pct(row['win_rate']),22,GREEN if row['total'] else MUTED,True,'rt',width=115)
    c.text(52,1740,'查看完整榜单：#bz 副角色 杜利 / #bz 副角色 杜利 胜率',22,GOLD,width=975)
    footer(c,doc,doc['heroes'],1780)
    return c


def render_report(doc, output=None):
    c = render_detail(doc) if doc['hero'] else render_overview(doc)
    folder = Path(output) if output else OUTPUT
    folder.mkdir(parents=True,exist_ok=True)
    path = folder/('secondary-'+uuid.uuid4().hex+'.png')
    c.image.resize((1080,c.image.height//2),Image.Resampling.LANCZOS).save(path)
    return str(path.resolve())
