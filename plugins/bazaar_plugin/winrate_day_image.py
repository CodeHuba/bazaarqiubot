"""Terminal-day report using the accepted native-art winrate design."""
from pathlib import Path
import uuid

from PIL import Image, ImageDraw, ImageFont

from .winrate_stats import MIN_TERMINAL_DAY, highlights


def render(result, output, *, queried_at, preview=False):
    from . import winrate_image as base
    rows = [dict(r) for r in result['day_distribution']]
    total, wins = int(result['total']), int(result['ten_win'])
    unknown = result['unknown_day']
    valid_total = sum(r['total'] for r in rows)
    valid_wins = sum(r['ten_win'] for r in rows)
    earlier_total = total-valid_total-unknown['total']
    earlier_wins = wins-valid_wins-unknown['ten_win']
    if (not 0 <= earlier_wins <= earlier_total or
            any(type(r['day']) is not int or r['day'] < MIN_TERMINAL_DAY or r['total'] <= 0
                or not 0 <= r['ten_win'] <= r['total'] for r in rows) or
            not 0 <= unknown['ten_win'] <= unknown['total'] or not 0 <= wins <= total):
        raise ValueError('终局天数统计计数不合法')
    if len({r['day'] for r in rows}) != len(rows):
        raise ValueError('终局天数重复')
    rows.sort(key=lambda r: r['day'])
    peaks, bests = highlights(rows)
    last_day = rows[-1]['day'] if rows else None
    if last_day is not None and last_day-MIN_TERMINAL_DAY <= 40:
        by_day = {r['day']: r for r in rows}
        rows = [by_day.get(day, dict(day=day, total=0, ten_win=0))
                for day in range(MIN_TERMINAL_DAY, last_day+1)]
    names = result['card_names']
    fonts = {}
    def font(n, bold=False):
        key = n, bold
        if key not in fonts:
            fonts[key] = ImageFont.truetype(base._font_path(bold), n)
        return fonts[key]
    def wrap(s, width, n):
        parts, line = [], ''
        for char in s:
            if line and font(n, True).getlength(line+char) > width:
                parts.append(line); line = ''
            line += char
        return parts+[line]
    title_lines = wrap(' + '.join(names), 984, 48)
    scope_text, time_text = base.scope_label(result.get('scope')), base.time_range_label(result.get('scope'))
    hero_top = 127+len(title_lines)*60+(44 if scope_text else 0)+(40 if time_text else 0)
    # Wrap wider combinations while retaining each card's native aspect ratio.
    details = result.get('card_details') or []
    art_rows, current, used = [], [], 0
    art_height = 250 if len(names) == 1 else 210
    for i, name in enumerate(names):
        detail = details[i] if i < len(details) else {}
        size = detail.get('size') or 'Small'
        width = round(art_height*{'Small': .5, 'Medium': 1, 'Large': 1.5}.get(size, 1))
        if current and used+width+40 > 888:
            art_rows.append(current); current, used = [], 0
        current.append((name, detail, size, width)); used += width+40
    if current:
        art_rows.append(current)
    # Single card uses the compact side-by-side summary from the preview.
    single = len(names) == 1
    art_bottom = hero_top+(319 if single else 48+len(art_rows)*(art_height+30))
    summary_bottom = art_bottom if single else art_bottom+204
    cards_top = summary_bottom+24
    chart_top = cards_top+192
    chart_bottom = chart_top+222+max(1, len(rows))*50
    height = chart_bottom+182
    if height > 6000:
        raise ValueError('终局天数过多，使用文字结果')
    im = Image.new('RGB', (1080, height), base.BG)
    draw = ImageDraw.Draw(im)
    def text(x, y, s, n=26, c=base.TEXT, bold=False, align='left'):
        s = str(s)
        width = draw.textlength(s, font=font(n, bold))
        if align == 'right': x -= width
        if align == 'center': x -= width/2
        draw.text((round(x), round(y)), s, font=font(n, bold), fill=c)
    def panel(box, fill=base.PANEL, outline=base.LINE, radius=22):
        draw.rounded_rectangle(box, fill=fill, outline=outline, radius=radius, width=1)
    def days_label(selected):
        days = [str(r['day']) for r in selected]
        if not days: return '暂无已知天数'
        return '第 '+'、'.join(days[:3])+(' 等'+str(len(days))+'天' if len(days)>3 else ' 天')

    draw.rounded_rectangle((48, 43, 54, 68), fill=base.GOLD, radius=3)
    text(70, 37, '巴扎丘Bot  /  卡牌胜率', 25, base.GOLD, True)
    if preview:
        panel((866, 32, 1032, 79), '#26303b', radius=13)
        text(949, 40, '效果预览', 23, base.GOLD, align='center')
    for i, line in enumerate(title_lines): text(48, 95+i*60, line, 48, bold=True)
    y = 110+len(title_lines)*60
    if scope_text: text(50, y, scope_text, 25, base.GOLD, True); y += 44
    if time_text: text(50, y, time_text, 21, base.MUTED)
    panel((48, hero_top, 1032, art_bottom))
    for ri, row in enumerate(art_rows):
        x = 78 if single else (1080-sum(it[3] for it in row)-40*(len(row)-1))/2
        for ci, (name, detail, size, width) in enumerate(row):
            y = hero_top+24+ri*(art_height+30)
            if ci: text(x-20, y+art_height/2-20, '+', 30, base.GOLD, True, 'center')
            art = base.load_art(detail.get('img', ''), size, cache=base.CACHE)
            if art is not None:
                art = art.convert('RGBA')
                art.thumbnail((min(width, 290) if single else width, art_height), Image.Resampling.LANCZOS)
                slot_width = 290 if single else width
                im.paste(art, (round(x+(slot_width-art.width)/2), round(y+(art_height-art.height)/2)), art)
            else:
                placeholder_width = min(width, 290) if single else width
                left = x+(290-placeholder_width)/2 if single else x
                panel((left, y, left+placeholder_width, y+art_height), '#233044')
                text(left+placeholder_width/2, y+art_height/2, '卡图暂缺', 20, base.MUTED, align='center')
            x += width+40
    sy = hero_top+25 if single else art_bottom+18
    sx = 404 if single else 76
    text(sx, sy, '整体 10 胜率' if single else '组合整体 10 胜率', 27, base.MUTED, True)
    text(sx, sy+36, f'{wins/total:.1%}' if total else '—', 88, base.GREEN, True)
    cx = 407 if single else 585
    cy = sy+175 if single else sy+18
    text(cx, cy, '出场数', 23, base.MUTED)
    text(cx, cy+33, f'{total:,} 局', 38, bold=True)
    text(cx+293 if single else cx+235, cy, '胜场数 · 达到10胜', 23, base.MUTED)
    text(cx+293 if single else cx+235, cy+33, f'{wins:,} 局', 38, base.GOLD, True)

    panel((48, cards_top, 528, cards_top+165), '#302b23', '#6a5940')
    text(76, cards_top+20, '最集中的结束天数', 24, base.GOLD, True)
    label = days_label(peaks)
    text(76, cards_top+64, label, 36 if font(36, True).getlength(label)<425 else 26, base.GOLD, True)
    prefix = '各 ' if len(peaks)>1 else ''
    text(76, cards_top+119, f"{prefix}{peaks[0]['total']} 局 · 占有效样本 {peaks[0]['total']/valid_total:.1%}" if peaks else '有效天数从第 10 天起', 23)
    panel((552, cards_top, 1032, cards_top+165), '#1b302f', '#3c665e')
    text(580, cards_top+20, '最高 10 胜率'+(' · 并列' if len(bests)>1 else ''), 24, base.GREEN, True)
    label = days_label(bests)
    text(580, cards_top+65, label, 34 if font(34, True).getlength(label)<280 else 23, base.GREEN, True)
    if bests:
        text(1004, cards_top+66, f"{bests[0]['ten_win']/bests[0]['total']:.1%}", 34, base.GREEN, True, 'right')
        samples = ' / '.join(str(r['total']) for r in bests[:3])+(' 等' if len(bests)>3 else '')
        text(580, cards_top+119, '样本 '+samples+' 局'+(' *' if any(r['total']<30 for r in bests) else ''), 23)

    panel((48, chart_top, 1032, chart_bottom))
    text(76, chart_top+24, '终局结束天数分布', 31, bold=True)
    span = f'第 {MIN_TERMINAL_DAY} 天起'
    if last_day is not None and last_day > MIN_TERMINAL_DAY:
        span = f'第 {MIN_TERMINAL_DAY}–{last_day} 天'
    text(76, chart_top+74, f'{span} · 有效样本 {valid_total:,} 局', 23, base.MUTED)
    for x, color, label in [(730, base.GREEN, '达到10胜'), (880, '#42536a', '未达10胜')]:
        draw.rounded_rectangle((x, chart_top+83, x+16, chart_top+99), fill=color, radius=3)
        text(x+26, chart_top+75, label, 20, base.MUTED)
    for x, label, align in [(76,'结束天数','left'), (238,'出场分布','left'), (585,'出场数','right'), (710,'占比','right'), (845,'10胜数','right'), (1004,'10胜率','right')]:
        text(x, chart_top+127, label, 22, base.MUTED, align=align)
    draw.line((76, chart_top+168, 1004, chart_top+168), fill=base.LINE)
    max_count = max((r['total'] for r in rows), default=1)
    for i, row in enumerate(rows):
        y = chart_top+180+i*50
        peak, best = row in peaks, row in bests
        if peak: panel((65, y-2, 1016, y+47), '#302b23', '#6a5940', 10)
        text(79, y+8, f"第 {row['day']} 天", 24, base.GOLD if peak else base.TEXT, peak)
        n, w = row['total'], row['ten_win']
        if 0<n<30: text(205,y+8,'*',23,base.MUTED)
        if n:
            length = max(3, round(260*n/max_count))
            draw.rounded_rectangle((237,y+20,237+length,y+31),radius=5,fill='#42536a')
            good = round(length*w/n)
            if good: draw.rounded_rectangle((237,y+20,237+good,y+31),radius=5,fill=base.GREEN)
        text(585,y+8,n,24,base.GOLD if peak else base.TEXT,peak,'right')
        text(710,y+8,f'{n/valid_total:.1%}',24,base.GOLD if peak else base.MUTED,peak,'right')
        text(845,y+8,w,24,align='right')
        if best: panel((881,y+3,1011,y+43),'#23443d','#426f60',9)
        text(1000,y+8,f'{w/n:.1%}' if n else '—',24,base.GREEN if best else base.TEXT,best,'right')
    if not rows: text(540,chart_top+182,'暂无有效天数样本',26,base.MUTED,align='center')
    text(76,chart_bottom-34,'金色：出场最集中    绿色：10胜率最高（并列均展示）',20,base.MUTED)
    text(50,chart_bottom+26,'10胜率 = 达到10胜的对局数 ÷ 对应分组的出场数',23,base.MUTED)
    text(50,chart_bottom+66,'分布占比按第 10 天起结束的对局计算，顶部整体统计纳入全部样本。',22,base.MUTED)
    detail = '* 少于 30 局，样本较少。'
    if unknown['total']:
        detail += f" {unknown['total']:,} 局天数未知，计入顶部整体统计。"
    text(50,chart_bottom+106,detail,22,base.MUTED)
    text(50,chart_bottom+147,'统计于 '+queried_at.strftime('%Y-%m-%d %H:%M'),20,base.MUTED)
    text(1032,chart_bottom+147,'#bz winrate',20,base.GOLD,align='right')
    folder = Path(output); folder.mkdir(parents=True, exist_ok=True)
    target = folder/('winrate-'+uuid.uuid4().hex+'.png')
    im.save(target)
    return str(target.resolve())
