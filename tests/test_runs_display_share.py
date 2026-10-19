"""Terminal qualities, skills, historical direct links and page-only enrichment."""
import ast
import json
import sqlite3
from pathlib import Path

import pytest
from flask import Flask, jsonify

from test_runs_list_performance import RunsQuery, runs_query, _make_db
from plugins.bazaar_plugin.run_instances import terminal_instances


@pytest.fixture
def query(tmp_path, monkeypatch):
    path = tmp_path / 'runs.db'
    _make_db(path, count=3)
    q = RunsQuery(db_path=str(path))
    q.conn = sqlite3.connect(path)
    q.conn.execute('ALTER TABLE runs ADD COLUMN raw_json TEXT')
    q.card_mapping = {'a': {'name': 'Alpha'}, 's': {'name': 'Skill'}}
    q.name_to_ids = {'alpha': ['a'], 'skill': ['s']}
    monkeypatch.setattr(q, '_card_image_info', lambda cid: {
        'name': '技能名称' if cid == 's' else '物品名称', 'size': 'Medium',
        'art': '/static/art.webp', 'tier': 'Diamond',  # Never used as run quality.
    })
    yield q
    q.conn.close()


def update(query, items, skills, raw=None, combats=None, rid='run-2'):
    query.conn.execute('UPDATE runs SET items_json=?,skills_json=?,raw_json=?,combats_json=? WHERE id=?',
                       (json.dumps(items), json.dumps(skills), json.dumps(raw), json.dumps(combats), rid))
    query.conn.commit()


def test_instance_qualities_and_skills_preserve_duplicate_slots_and_legendary(query):
    update(query, [{'cardId': 'a', 'slotPosition': 2}, {'cardId': 'a', 'slotPosition': 0}],
           [{'skillId': 's', 'tierOverride': 'silver'}, {'cardId': 's', 'isLegendary': True}],
           {'items': [{'cardId': 'a', 'slotPosition': 0, 'tier': 'Bronze'},
                      {'cardId': 'a', 'slotPosition': 2, 'tierOverride': 'Gold', 'enchantment': 'Fiery'}]},
           [{'d': 3}, {'d': 8}, {'d': True}, {'d': '9'}])
    run = query.query(min_wins=10, page_size=1)['runs'][0]
    assert [c['tier'] for c in run['cards']] == ['Bronze', 'Gold']
    assert run['cards'][1]['enchantment_zh'] == '炽焰'
    assert [c['tier'] for c in run['skills']] == ['Silver', 'Legendary']
    assert all(c['type'] == 'Skill' and c['cardId'] == 's' for c in run['skills'])
    assert query.query(card_filters=[{'name': 'skill'}])['total'] == 1
    assert query.query(cards=['skill'])['total'] == 1
    assert run['day'] == 8
    assert 'attributes' not in run['cards'][0]


def test_unknown_quality_is_not_inferred_from_static_data_or_ambiguous_instances(query):
    update(query, [{'cardId': 'a'}, {'cardId': 'a', 'tier': None}], [],
           {'items': [{'cardId': 'a', 'tier': 'Gold'}, {'cardId': 'a', 'tier': 'Diamond'}]})
    run = query.get_run('run-2')
    assert [c['tier'] for c in run['cards']] == [None, None]
    assert run['skills'] == []
    assert run['day'] is None  # Never wins + losses.
    assert terminal_instances([{'cardId': 'a', 'tier': None}],
                              {'items': [{'cardId': 'a', 'tier': 'Gold'}]}, 'items')[0]['tier'] is None


def test_skill_matches_are_highlighted_and_display_work_is_only_for_current_page(query, monkeypatch):
    for i in range(3):
        update(query, [{'cardId': 'a'}], [{'cardId': 's', 'tier': 'Gold'}], rid=f'run-{i}')
    calls, statements = [], []
    monkeypatch.setattr(query, '_card_image_info', lambda cid: calls.append(cid) or {})
    query.conn.set_trace_callback(statements.append)
    result = query.query(card_filters=[{'name': 'skill'}], page_size=1, page=2)
    assert result['total'] == 3
    assert calls == ['a', 's']
    assert result['runs'][0]['skills'][0]['matched'] is True
    raw_reads = [sql for sql in statements if sql.startswith('SELECT id, raw_json')]
    assert len(raw_reads) == 1 and "IN ('run-1')" in raw_reads[0]


def test_direct_link_reads_original_scope_and_only_public_fields(query):
    update(query, [], [{'cardId': 's'}], {'profileId': 'private-profile', 'skills': []})
    query.conn.execute("UPDATE runs SET season=1,phase='old',screenshot_url='/shot.webp' WHERE id='run-2'")
    query.conn.commit()
    assert query.query()['total'] == 2
    run = query.get_run('run-2')
    assert (run['season'], run['phase']) == (1, 'old')
    assert run['screenshot'] == 'https://usercontent.bzdb.network/shot.webp'
    assert 'raw_json' not in run and 'private-profile' not in json.dumps(run)
    assert query.get_run("' OR 1=1 --") is None
    assert query.get_run('removed') is None


@pytest.mark.parametrize('value', ['{bad', '{}', 'null', '[null,3,{},"invalid"]'])
def test_malformed_historical_data_does_not_break_result(query, value):
    query.conn.execute('UPDATE runs SET items_json=?,skills_json=?,combats_json=? WHERE id=?',
                       (value, value, value, 'run-2'))
    run = query.get_run('run-2')
    assert run['cards'] == []
    assert isinstance(run['skills'], list)
    assert run['day'] is None


def test_direct_link_route_returns_404_validation_and_closes_connections(query):
    tree = ast.parse((Path(__file__).parents[1] / 'web_runs/app.py').read_text(encoding='utf-8'))
    route = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'api_run_detail')
    route.decorator_list = []
    app = Flask(__name__)
    connections = []

    def factory(**kwargs):
        q = RunsQuery(db_path=query.db_path)
        q.conn = sqlite3.connect(query.db_path)
        q._card_image_info = lambda cid: {}
        connections.append(q.conn)
        return q

    namespace = dict(RunsQuery=factory, RUNS_DB_PATH=query.db_path, jsonify=jsonify, app=app, re=__import__('re'))
    exec(compile(ast.Module(body=[route], type_ignores=[]), '<api_run_detail>', 'exec'), namespace)
    app.add_url_rule('/api/runs/<run_id>', view_func=namespace['api_run_detail'])
    with app.test_client() as client:
        assert client.get('/api/runs/run-2').json['run']['id'] == 'run-2'
        assert client.get('/api/runs/removed').status_code == 404
        assert client.get('/api/runs/bad%27id').status_code == 400
        assert client.get('/api/runs/' + 'a' * 129).status_code == 400
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError):
            connection.execute('SELECT 1')
