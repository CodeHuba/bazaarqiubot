"""Single-item QQ composition queries backed by the website's shared cache."""
import shlex
import threading
import time
from urllib.parse import urlencode

import httpx

HELP = ('用法: #bz 阵容 <物品名> [--hero 英雄] [--days 1–365] [--legendary]\n'
        '示例: #bz 阵容 原子钟\n英文别名: #bz comp Atomic Clock\n默认按出场局数降序，一次查询一件物品。')
API_URL = 'http://127.0.0.1:1027/api/comp/card'
_request_lock = threading.Lock()
_last_request = 0.0


def _request_slot():
    """Respect the website's one-second per-IP query interval across QQ users."""
    global _last_request
    with _request_lock:
        delay = _last_request+1.1-time.monotonic()
        if delay > 0:
            time.sleep(delay)
        _last_request = time.monotonic()


class CompInputError(ValueError):
    pass


class CompUnavailable(RuntimeError):
    pass


def parse_args(arg):
    try:
        tokens = shlex.split(arg)
    except ValueError as exc:
        raise CompInputError('物品名引号未闭合，请补全后重试。') from exc
    names, options, seen = [], {'hero': '', 'days': None, 'rank': 'all'}, set()
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token in ('--hero', '--days', '--legendary'):
            if token in seen:
                raise CompInputError('参数重复: '+token)
            seen.add(token)
            if token == '--legendary':
                options['rank'] = 'legendary'
            else:
                if i+1 >= len(tokens) or tokens[i+1].startswith('--'):
                    raise CompInputError('请提供 '+token+' 的参数。')
                i += 1
                value = tokens[i]
                if token == '--days':
                    if not value.isascii() or not value.isdecimal() or not 1 <= int(value) <= 365:
                        raise CompInputError('--days 必须为 1–365 的整数。')
                    options['days'] = int(value)
                else:
                    options['hero'] = value
        elif token.startswith('--'):
            raise CompInputError('不支持的参数: '+token+'\n'+HELP)
        else:
            names.append(token)
        i += 1
    name = ' '.join(names).strip()
    if not name:
        raise CompInputError(HELP)
    if '+' in name or '＋' in name:
        raise CompInputError('阵容分析一次查询一件物品，请去掉其他物品后重试。')
    if len(name) > 160:
        raise CompInputError('物品名称过长，请使用完整物品名查询。')
    return dict(options, name=name)


def query_comp(options):
    from .runs_query import RunsQuery
    from .winrate_command import resolve_cards, WinrateInputError
    query = RunsQuery()
    try:
        names = resolve_cards(query, [options['name']])
        hero = query.resolve_hero(options['hero']) if options['hero'] else ''
        if options['hero'] and not hero:
            raise CompInputError('无法识别英雄: '+options['hero'])
    except WinrateInputError as exc:
        raise CompInputError(str(exc)) from exc
    finally:
        if query.conn is not None:
            query.conn.close()
    params = {'card': names[0], 'sort': 'appearance', 'rank': options['rank']}
    if hero:
        params['hero'] = hero
    if options['days'] is not None:
        params['days'] = options['days']
    try:
        _request_slot()
        response = httpx.get(API_URL, params=params, timeout=30, trust_env=False)
        if response.status_code in (429, 503):
            raise CompUnavailable('阵容查询繁忙或正在预热，请稍后重试。')
        if response.status_code in (400, 404):
            message = response.json().get('error') or '物品或筛选条件无效，请检查后重试。'
            raise CompInputError(message)
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict) or not result.get('anchors') or not isinstance(result.get('baseline'), dict):
            raise CompUnavailable('阵容分析暂时无法读取，请稍后重试。')
        return result
    except httpx.TimeoutException as exc:
        raise CompUnavailable('阵容分析超时，请稍后重试。') from exc
    except (httpx.HTTPError, ValueError) as exc:
        if isinstance(exc, CompInputError):
            raise
        raise CompUnavailable('阵容分析暂时无法读取，请稍后重试。') from exc


def selected_groups(data):
    ordered = sorted(data.get('groups') or [], key=lambda g: (-g['count'], -g['lower_bound'], g['id']))
    regular = [g for g in ordered if g['count'] >= 30][:3]
    return regular or [g for g in ordered if g['count'] >= 10][:2]


def website_link(data):
    scope = data['scope']
    params = {'mode': 'comp', 'cards': '+'.join(c['name'] for c in data['anchors']),
              'rank': scope['rank'], 'sort': 'appearance', 'auto': '1'}
    if scope.get('hero'):
        params['hero'] = scope['hero']
    if scope.get('days') is not None:
        params['days'] = scope['days']
    return 'https://bazaarqiubot.com/runs?'+urlencode(params)


def scope_label(data):
    from .runs_query import HERO_EN_TO_ZH
    scope = data['scope']
    parts = ['版本 '+str(scope['phase']), HERO_EN_TO_ZH.get(scope.get('hero'), '全部英雄'),
             '传奇' if scope['rank'] == 'legendary' else '全部段位']
    if scope.get('days') is not None:
        parts.append('近 '+str(scope['days'])+' 天')
    return ' · '.join(parts)


def format_text(data):
    base = data['baseline']
    lines = ['[巴扎] '+data['anchors'][0]['name']+' · 阵容分析', scope_label(data)]
    if base['count']:
        lines.append(f"命中 {base['count']:,} 局 · 整体10胜率 {base['rate']:.1%} · 平均 {base['average_wins']:.1f} 胜")
    else:
        lines.append('当前版本及筛选条件下暂无数据。')
    groups = selected_groups(data)
    for i, group in enumerate(groups, 1):
        core = ' + '.join(c['name'] for c in group['core'])
        level = '（小样本探索）' if group['count'] < 30 else ''
        lines.append(f"{i}. {group['hero_name']} · {core}{level}\n出场 {group['count']:,} 局 · 10胜率 {group['rate']:.1%}")
        for label, key in [('补充物品', 'winning_items'), ('常见技能', 'winning_skills')]:
            companions = group.get(key, [])[:3]
            if companions:
                lines.append(label+'：'+'、'.join(c['name']+(f" {c['ten_win_frequency']:.0%}" if c.get('ten_win_frequency') is not None else '') for c in companions))
    if base['count'] and not groups:
        lines.append('暂无达到10局的核心组合，可打开网站查看实战记录。')
    lines.extend(['按出场局数降序；搭配频率基于该组合的10胜局。',
                  '采集不含0胜局，结果不代表拿到物品后的获胜概率。'])
    return '\n'.join(lines)
