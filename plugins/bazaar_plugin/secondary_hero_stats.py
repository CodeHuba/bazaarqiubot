"""Read-only, independently scoped finale-event statistics. No live QQ imports."""
from datetime import datetime, timezone
from fractions import Fraction
import json
import os
from pathlib import Path
import sqlite3

CONFIG = Path(__file__).with_name('secondary_hero_event.json')
# These IDs are the eight merchant-pool Legendary skills in native 19.4 GameData.
# Match the exact skill identity, never its translated name, tier or array position.
SKILL_HERO = {
    'afe451fd-7726-4ed6-9ad9-48d917a12012': 'Vanessa',
    '2f43270c-1f5b-456c-aa86-5c186e6d3fea': 'Dooley',
    '3bc339db-f463-4390-bf2e-31f2a5008b3f': 'Mak',
    '645e8d63-24be-4ce8-a633-7df736826d95': 'Pygmalien',
    'aff2654a-a1d4-4c04-8438-4e3f18e136cd': 'Stelle',
    '2e70face-81e0-4d1e-8c03-3bcc3efc2625': 'Jules',
    '84092bda-9617-431a-a12c-6f93edd024a9': 'Karnok',
    'c5541759-7bcb-4892-8fa5-b4c865eef40d': 'The Dragons',
}
USAGE = ('用法：#bz 副角色 [主角色] [选取率/胜率]\n'
         '例：#bz 副角色 杜利 / #bz 副角色 杜利 胜率\n'
         '省略主角色查看八位角色概览；胜率指10胜率。')


def aware_time(value):
    if not isinstance(value, str):
        raise ValueError('活动时间必须为带时区的 ISO 时间')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('活动时间必须包含时区')
    return result.astimezone(timezone.utc)


def load_event(path=None):
    event = json.loads(Path(path or os.getenv('BAZAAR_SECONDARY_EVENT_CONFIG') or CONFIG).read_text(encoding='utf-8'))
    if (not isinstance(event, dict) or not isinstance(event.get('id'), str) or not event['id']
            or not isinstance(event.get('name'), str) or not event['name']
            or type(event.get('season')) is not int or event['season'] <= 0
            or not isinstance(event.get('phases'), list) or not event['phases']
            or any(not isinstance(p, str) or not p for p in event['phases'])
            or type(event.get('min_wins')) is not int or event['min_wins'] not in (0, 1)):
        raise ValueError('副角色活动配置不合法')
    start = aware_time(event.get('start_at'))
    end = aware_time(event['end_at']) if event.get('end_at') is not None else None
    if end is not None and end <= start:
        raise ValueError('活动结束时间必须晚于开始时间')
    return event


def parse_args(arg, names, aliases):
    value, order = arg.strip(), 'pick'
    for suffix, mode in (('10胜率', 'win'), ('选取率', 'pick'), ('胜率', 'win')):
        # Longest token must win; "10胜率" is also a suffix of "胜率".
        if value == suffix or value.endswith(' '+suffix):
            value, order = value[:-len(suffix)].strip(), mode
            break
    if not value or value.lower() in {'all', '全部', '总览'}:
        return None, order
    normalized = value.casefold()
    by_name = {name.casefold(): hero for hero, name in names.items()}
    by_name.update({hero.casefold(): hero for hero in names})
    by_name.update({alias.casefold(): hero for alias, hero in aliases.items() if hero in names})
    hero = by_name.get(normalized)
    if hero is None:
        raise ValueError('未识别主角色。\n'+USAGE)
    return hero, order


def query_stats(names, db_path=None, *, event=None, now=None):
    """One SQL aggregation; malformed skills are excluded from rates, not hidden.

    Eligibility is fixed to this event, the selected phase(s), and the stated
    sample floor. Historical phases and future uploads never broaden the scope.
    The source created_at field is the same timestamp used by the runs collector.
    """
    event = load_event() if event is None else event
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('查询时间必须包含时区')
    now = now.astimezone(timezone.utc)
    end = aware_time(event['end_at']) if event.get('end_at') else None
    cutoff = min(now, end) if end else now
    path = Path(db_path or os.getenv('BAZAAR_SECONDARY_RUNS_DB_PATH')
                or os.getenv('RUNS_DB_PATH', '/opt/qiubot/data/bazaar_runs.db')).resolve()
    db = sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=5)
    try:
        db.execute('PRAGMA query_only=ON')
        db.execute('PRAGMA cache_size=-2048')
        # Bound CPU time as well as lock waits on a busy shared database.
        import time
        deadline = time.monotonic()+12
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
        mapped = ','.join('(?,?)' for _ in SKILL_HERO)
        phases = ','.join('?' for _ in event['phases'])
        heroes = ','.join('?' for _ in names)
        rows = db.execute(f'''
            WITH mapping(skill_id, secondary) AS (VALUES {mapped}),
            eligible AS (
              SELECT id,hero,stat_wins,skills_json FROM runs
              WHERE season=? AND phase IN ({phases}) AND hero IN ({heroes})
                AND typeof(stat_wins)='integer' AND stat_wins>=?
                AND julianday(created_at)>=julianday(?)
                AND julianday(created_at)<julianday(?)
            ), matches AS (
              SELECT e.id,e.hero,e.stat_wins,COUNT(DISTINCT m.secondary) AS hits,
                     MIN(m.secondary) AS secondary
              FROM eligible e
              LEFT JOIN json_each(CASE WHEN json_valid(e.skills_json) THEN
                  CASE WHEN json_type(e.skills_json)='array' THEN e.skills_json ELSE '[]' END
                  ELSE '[]' END) j ON 1=1
              LEFT JOIN mapping m ON m.skill_id=CASE j.type
                  WHEN 'text' THEN j.value
                  WHEN 'object' THEN COALESCE(json_extract(j.value,'$.cardId'),json_extract(j.value,'$.skillId'))
                  ELSE NULL END
              GROUP BY e.id,e.hero,e.stat_wins
            )
            SELECT hero,CASE WHEN hits=0 THEN 'missing'
                             WHEN hits>1 OR secondary=hero THEN 'conflict'
                             ELSE secondary END AS chosen,
                   COUNT(*),SUM(CASE WHEN stat_wins>=10 THEN 1 ELSE 0 END)
            FROM matches GROUP BY hero,chosen
            ''', (*[v for pair in SKILL_HERO.items() for v in pair], event['season'],
                  *event['phases'], *names, event['min_wins'], event['start_at'], cutoff.isoformat())).fetchall()
    finally:
        db.close()
    result = {hero: dict(hero=hero, name=name, total=0, valid=0, wins=0, missing=0, conflict=0,
                         rows={h: dict(hero=h, name=n, total=0, wins=0) for h,n in names.items() if h != hero})
              for hero,name in names.items()}
    for hero, chosen, total, wins in rows:
        record = result[hero]
        record['total'] += total
        if chosen in ('missing', 'conflict'):
            record[chosen] += total
        else:
            record['valid'] += total
            record['wins'] += wins
            record['rows'][chosen].update(total=total, wins=wins)
    for record in result.values():
        record['rows'] = list(record['rows'].values())
    return dict(event=dict(event), heroes=list(result.values()), queried_at=now.isoformat(),
                ended=bool(end and now >= end), source_note='')


def build_document(stats, *, hero=None, order='pick', preview=False):
    if order not in ('pick', 'win'):
        raise ValueError('无效排序')
    heroes = []
    for source in stats['heroes']:
        record = dict(source)
        rows = [dict(row) for row in source['rows']]
        for row in rows:
            row['pick_rate'] = row['total']/record['valid'] if record['valid'] else None
            row['win_rate'] = row['wins']/row['total'] if row['total'] else None
        def key(row):
            value = Fraction(row['wins'], row['total']) if order == 'win' and row['total'] else row['total']
            return (row['total'] == 0, -value, -row['total'], row['hero'])
        rows.sort(key=key)
        record['rows'] = rows
        heroes.append(record)
    if hero is not None and hero not in {r['hero'] for r in heroes}:
        raise ValueError('无效主角色')
    return dict(event=dict(stats['event']), heroes=heroes, hero=hero, order=order,
                queried_at=stats['queried_at'], ended=stats['ended'], preview=bool(preview),
                source_note=stats.get('source_note', ''))


def format_report(doc):
    event = doc['event']
    floor = '1胜及以上' if event['min_wins'] else '含0胜'
    lines = [event['name']+(' · 活动已结束 · 历史数据' if doc['ended'] else ''),
             '活动范围 S'+str(event['season'])+' / '+', '.join(event['phases'])+' · 全部段位',
             f'已收录{floor}样本 · 按'+('10胜率' if doc['order']=='win' else '选取率')+'排序']
    if doc['preview']:
        lines.insert(0, '【测试预览】')
    for record in doc['heroes']:
        if doc['hero'] is not None and record['hero'] != doc['hero']:
            continue
        coverage = f"{record['valid']/record['total']:.1%}" if record['total'] else '暂无数据'
        lines.append(f"{record['name']}：有效 {record['valid']:,}/{record['total']:,}场 · 识别覆盖率 {coverage}")
        for i,row in enumerate(record['rows'] if doc['hero'] else record['rows'][:3], 1):
            pick = f"{row['pick_rate']:.1%}" if row['pick_rate'] is not None else '暂无数据'
            win = f"{row['win_rate']:.1%}" if row['win_rate'] is not None else '暂无数据'
            label = '小样本' if 0 < row['total'] < 30 else '暂无数据' if not row['total'] else ''
            rank = str(i) if row['total'] else '—'
            lines.append(f"{rank}. {row['name']}：选取率 {pick} / {row['total']:,}场 / {row['wins']:,}场10胜 / 10胜率 {win} {label}".rstrip())
        lines.append(f"未识别 {record['missing']:,}场 · 冲突 {record['conflict']:,}场，均不计入排行")
    lines += ['选取率=该副角色场次÷该主角色有效识别场次；10胜率=该搭配10胜场次÷该搭配场次。',
              '基于已收录样本，非全服统计；不是三选一给到该选项时的被选概率。',
              '数据查询时间 '+doc['queried_at']]
    if doc['source_note']:
        lines.append(doc['source_note'])
    return '\n'.join(lines)
