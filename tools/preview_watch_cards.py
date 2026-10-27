"""Local watch-card acceptance preview. Never changes subscriptions or sends QQ messages.

python tools/preview_watch_cards.py --username Koucha_ --username Hyx499 --refresh
"""
from __future__ import annotations
import argparse
from datetime import datetime
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data/charts/watch-preview-koucha'
# Load the shared renderer without importing the live QQ plugin runtime.
_spec = importlib.util.spec_from_file_location('watch_preview_cards', ROOT/'plugins/bazaar_plugin/watch_cards.py')
_cards = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_cards)
CST = _cards.CST
report_window = _cards.report_window
summarize = _cards.summarize
subscription_card = _cards.subscription_card
watchlist_card = _cards.watchlist_card
daily_card = _cards.daily_card


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--username', action='append', help='Repeat to include multiple players')
    parser.add_argument('--refresh', action='store_true')
    parser.add_argument('--output', type=Path, default=OUT)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    usernames = list(dict.fromkeys(args.username or ['Koucha_']))
    if any(not u or len(u) > 30 or any(c in u for c in '/\\<>:"|?*') or u in ('.', '..') for u in usernames):
        parser.error('用户名格式不合法')
    now = datetime.now(CST)
    start, end = report_window(now)
    players = []
    for username in usernames:
        folder = args.output/username
        folder.mkdir(parents=True, exist_ok=True)
        if args.refresh:
            import httpx
            endpoints = [
                ('profile', 'https://bazaar.mrmao.life/api/user/season-vip-info', {'username': username}),
                ('stat', 'https://bazaarapi.mrmao.life/api/user/comprehensive-info', {'username': username, 'seasonId': 20}),
            ]
            with httpx.Client(timeout=30, headers={'User-Agent': 'Mozilla/5.0 (compatible; QiuBot-BazaarPlugin/1.0)'}) as client:
                for name, url, params in endpoints:
                    response = client.get(url, params=params)
                    response.raise_for_status()
                    body = response.json()
                    if not body.get('success'):
                        raise RuntimeError(body.get('message') or f'{name} 查询失败')
                    (folder/f'{name}.json').write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding='utf-8')
        if not (folder/'profile.json').is_file() and len(usernames) == 1:
            folder = args.output  # Read the original single-player preview cache.
        profile = json.loads((folder/'profile.json').read_text(encoding='utf-8'))['data']
        data = json.loads((folder/'stat.json').read_text(encoding='utf-8'))['data']
        players.append(dict(username=username, current=profile['currentRating'], summary=summarize(data['ratingHistory'], start, end)))
    newest = players[-1]
    files = [
        subscription_card(newest['username'], newest['current'], now, args.output/'01-subscription.png', count=len(players)),
        watchlist_card(players, now, args.output/'02-watchlist.png'),
    ]
    pages = _cards.render_pages('daily', players, now, args.output,
                                start=start, end=end, preview=True)
    for index, page in enumerate(pages, 1):
        target = args.output/f'03-daily-report-{index}.png'
        Path(page).replace(target)
        files.append(str(target.resolve()))
    metadata = dict(usernames=usernames, generated_at=now.isoformat(), start=start.isoformat(), end=end.isoformat(), players=players, images=files)
    (args.output/'preview.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    summaries = [dict(username=p['username'], state=p['summary']['state'], rating_delta=p['summary']['rating_delta'], rank_gain=p['summary']['rank_gain']) for p in players]
    print(json.dumps(dict(images=files, players=summaries), ensure_ascii=False))


if __name__ == '__main__':
    main()
