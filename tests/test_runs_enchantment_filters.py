import ast
import json
import sqlite3
from pathlib import Path

import pytest
from flask import Flask, jsonify, request

from test_runs_list_performance import RunsQuery, runs_query, _make_db
from plugins.bazaar_plugin.run_card_filters import normalize_run_card_filters
from test_day_stats_api import _client, stats_db


@pytest.fixture
def query(tmp_path, monkeypatch):
    path = tmp_path / 'runs.db'
    _make_db(path, count=0)
    q = RunsQuery(db_path=str(path))
    q.conn = sqlite3.connect(path)
    q.name_to_ids = {'alpha': ['a', 'a-alt'], 'beta': ['b'], 'skill': ['s']}
    q.card_mapping = {cid: {'name': name} for cid, name in
                      [('a', 'Alpha'), ('a-alt', 'Alpha'), ('b', 'Beta'), ('s', 'Skill')]}
    monkeypatch.setattr(q, '_card_image_info', lambda cid: {})
    cases = [
        ('wrong', [{'cardId': 'a', 'enchantment': None}, {'cardId': 'b', 'enchantment': 'Fiery'}]),
        ('match', [{'cardId': 'a', 'enchantment': 'Fiery'}, {'cardId': 'b', 'enchantment': 'Shiny'}]),
        ('missing', [{'cardId': 'a'}, {'cardId': 'b', 'enchantment': 'Shiny'}]),
        ('null', [{'cardId': 'a', 'enchantment': None}]),
        ('duplicates', [{'cardId': 'a', 'enchantment': 'Fiery'}, {'cardId': 'a', 'enchantment': 'Toxic'}, {'cardId': 'b', 'enchantment': 'Shiny'}]),
        ('alt-id', [{'cardId': 'a-alt', 'enchantment': 'Fiery'}]),
        ('skill-only', [{'cardId': 'b', 'enchantment': 'Fiery'}]),
        ('broken', '{broken'),
        ('non-objects', [None, 3, 'a', {}, {'cardId': 'a', 'enchantment': 'Fiery'}]),
        ('other-hero', [{'cardId': 'a', 'enchantment': 'Fiery'}]),
        ('other-phase', [{'cardId': 'a', 'enchantment': 'Fiery'}]),
        ('low-wins', [{'cardId': 'a', 'enchantment': 'Fiery'}]),
    ]
    for index, (rid, items) in enumerate(cases):
        q.conn.execute('INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (
            rid, 'Dooley' if rid == 'other-hero' else 'Vanessa', 'tester',
            f'2026-10-15T{index:02d}:00:00+00:00',
            items if isinstance(items, str) else json.dumps(items),
            json.dumps([{'cardId': 'a', 'enchantment': 'Fiery'}, {'cardId': 's'}]) if rid == 'skill-only' else '[]',
            json.dumps([{'d': 6}]), 5 if rid == 'low-wins' else 10, 2, '',
            'Gold' if rid == 'other-hero' else 'Legendary', runs_query.RUNS_SEASON_ID,
            'old' if rid == 'other-phase' else runs_query.CURRENT_PHASE, '',
        ))
    q.conn.commit()
    yield q
    q.conn.close()


def filters(*pairs):
    return [{'name': name, 'enchantment': enchantment} for name, enchantment in pairs]


def test_card_and_enchantment_must_belong_to_the_same_item(query):
    result = query.query(card_filters=filters(('Alpha', 'Fiery')), min_wins=10, page_size=50)
    assert {r['id'] for r in result['runs']} == {'match', 'duplicates', 'alt-id', 'non-objects', 'other-hero'}
    for run in result['runs']:
        matched = [c for c in run['cards'] if c['matched']]
        assert matched and all(c['enchantment'] == 'Fiery' for c in matched)
        assert all(c['enchantment_zh'] == '炽焰' for c in matched)


def test_multiple_pairs_are_anded_and_same_card_can_have_different_enchantments(query):
    result = query.query(card_filters=filters(('Alpha', 'Fiery'), ('Beta', 'Shiny')), page_size=50)
    assert {r['id'] for r in result['runs']} == {'match', 'duplicates'}
    result = query.query(card_filters=filters(('Alpha', 'Fiery'), ('Alpha', 'Toxic')))
    assert [r['id'] for r in result['runs']] == ['duplicates']


def test_pagination_is_applied_after_filters_and_keeps_other_constraints(query):
    kwargs = dict(card_filters=filters(('Alpha', 'Fiery')), hero='Vanessa', min_wins=10,
                  rank_filter='legendary', day=6, page_size=2)
    first = query.query(page=1, **kwargs)
    second = query.query(page=2, **kwargs)
    assert first['total'] == second['total'] == 4
    assert first['pages'] == second['pages'] == 2
    assert [r['id'] for r in first['runs']] == ['non-objects', 'alt-id']
    assert [r['id'] for r in second['runs']] == ['duplicates', 'match']
    assert query.query(page=99, **kwargs)['page'] == 2
    assert query.query(day=7, card_filters=filters(('Alpha', 'Fiery')))['total'] == 0


def test_unconstrained_cards_keep_missing_null_and_skill_matches(query):
    result = query.query(card_filters=filters(('Alpha', None)), min_wins=10, page_size=50)
    ids = {r['id'] for r in result['runs']}
    assert {'wrong', 'missing', 'null', 'skill-only'} <= ids
    assert result['total'] == 9
    assert query.query(cards=['Alpha'], min_wins=10, page_size=50)['total'] == result['total']
    # Supplying both legacy and structured filters adds constraints, never drops either.
    result = query.query(cards=['Beta'], card_filters=filters(('Alpha', 'Fiery')), page_size=50)
    assert {r['id'] for r in result['runs']} == {'match', 'duplicates'}


def test_display_only_builds_current_page_and_unknown_card_is_empty(query, monkeypatch):
    calls = []
    monkeypatch.setattr(query, '_card_image_info', lambda cid: calls.append(cid) or {})
    result = query.query(card_filters=filters(('Alpha', 'Fiery')), min_wins=10, page_size=1)
    assert len(calls) == len(result['runs'][0]['cards'])
    assert query.query(card_filters=filters(('no-such-card', 'Fiery')))['total'] == 0


@pytest.mark.parametrize('value', ['{bad', '{}', 'null', [None], [{'name': ''}],
    [{'name': 'Alpha', 'enchantment': 'bad'}], [{'name': 'Alpha', 'enchantment': []}],
    [{'name': 'Alpha', 'enchantment': True}], [{'name': 'x' * 161}], [{}] * 21])
def test_invalid_filters_are_rejected(value):
    with pytest.raises(ValueError):
        normalize_run_card_filters(value)


def test_filters_normalize_and_deduplicate_without_losing_distinct_enchants():
    assert normalize_run_card_filters(json.dumps([
        {'name': ' Alpha ', 'enchantment': 'Fiery'}, {'name': 'Alpha', 'enchantment': 'Fiery'},
        {'name': 'Alpha', 'enchantment': 'Toxic'}, {'name': 'Beta'},
    ])) == filters(('Alpha', 'Fiery'), ('Alpha', 'Toxic'), ('Beta', None))


@pytest.fixture
def api(query):
    # Execute the actual route body without booting unrelated schedulers and services.
    root = Path(__file__).resolve().parents[1]
    tree = ast.parse((root / 'web_runs/app.py').read_text(encoding='utf-8'))
    route = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'api_runs')
    route.decorator_list = []
    class Factory:
        def __new__(cls, **kwargs):
            return query
    query.load = lambda: None
    logs = []
    namespace = dict(request=request, jsonify=jsonify, RunsQuery=Factory,
                     RUNS_DB_PATH=query.db_path, normalize_run_card_filters=normalize_run_card_filters,
                     _mask_ip=lambda ip: ip, _log_query=lambda *args: logs.append(args))
    exec(compile(ast.Module(body=[route], type_ignores=[]), str(root / 'web_runs/app.py'), 'exec'), namespace)
    app = Flask(__name__)
    app.add_url_rule('/api/runs', view_func=namespace['api_runs'])
    return app.test_client(), logs


def test_api_passes_filters_returns_display_and_preserves_popular_card_logs(api):
    client, logs = api
    response = client.get('/api/runs', query_string={
        'hero': '瓦内莎', 'rank': 'legendary', 'card_filters': json.dumps(filters(('Alpha', 'Fiery'))),
    })
    assert response.status_code == 200
    assert response.json['total'] == 4
    assert response.json['runs'][0]['cards'][0]['enchantment'] == 'Fiery'
    assert logs[-1][1]['cards'] == ['Alpha']
    assert logs[-1][1]['card_filters'] == filters(('Alpha', 'Fiery'))


def test_api_legacy_links_and_invalid_parameters(api):
    client, _ = api
    assert client.get('/api/runs', query_string={'cards': 'Alpha+Beta'}).json['total'] == 5
    for value in ('bad-json', '{}', '[{"name":"Alpha","enchantment":"unknown"}]'):
        response = client.get('/api/runs', query_string={'card_filters': value})
        assert response.status_code == 400
        assert response.json['error']


def test_registered_flask_route_with_real_query(query, monkeypatch, stats_db):
    client = _client(monkeypatch, stats_db)
    web_app = client.web_app
    query.load = lambda: None
    monkeypatch.setattr(web_app, 'RunsQuery', lambda **kwargs: query)
    web_app._rate_limit.clear()
    response = client.get('/api/runs', query_string={
        'card_filters': json.dumps(filters(('Alpha', 'Fiery'))), 'hero': '瓦内莎',
    })
    assert response.status_code == 200
    assert response.json['total'] == 4
    assert all(any(card['matched'] and card['enchantment'] == 'Fiery' for card in run['cards'])
               for run in response.json['runs'])
    web_app._rate_limit.clear()
    assert client.get('/api/runs', query_string={'card_filters': '{}'}).status_code == 400
