"""Execute the real query method with an isolated SQLite fixture."""
import ast
from datetime import datetime,timezone
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest


@pytest.fixture
def query():
    path=Path(__file__).parents[1]/'plugins/bazaar_plugin/runs_query.py'
    cls=next(n for n in ast.parse(path.read_text(encoding='utf-8')).body if isinstance(n,ast.ClassDef) and n.name=='RunsQuery')
    fn=next(n for n in cls.body if getattr(n,'name','')=='winrate')
    ns=dict(sqlite3=sqlite3,RUNS_SEASON_ID=20,CURRENT_PHASE='20.1',budgeted_rows=lambda rows:rows)
    exec(compile(ast.Module(body=[fn],type_ignores=[]),str(path),'exec'),ns)
    conn=sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE phases(season INTEGER,phase TEXT,start_time TEXT)')
    conn.executemany('INSERT INTO phases VALUES(?,?,?)',[(20,'20.0','2026-11-01T00:00:00Z'),
        (20,'20.1','2026-11-06T22:00:00+00:00'),(19,'19.4','2026-10-01T00:00:00Z')])
    conn.execute('CREATE TABLE runs(card_ids_text TEXT,stat_wins INTEGER,season INTEGER,phase TEXT,created_at TEXT)')
    obj=SimpleNamespace(conn=conn,find_card_ids=lambda name:['a'],card_display_info=lambda cid:dict(name='原子钟'))
    yield lambda **kwargs:ns['winrate'](obj,['原子钟'],**kwargs),conn
    conn.close()


def test_scope_comes_from_phase_metadata_even_without_samples(query):
    run,_=query
    result=run()
    assert result['scope']['start_at']=='2026-11-06T22:00:00+00:00'
    assert result['scope']['phase']=='20.1' and result['total']==0


def test_recent_days_start_is_clamped_to_phase_boundary(query):
    run,conn=query
    # A future phase boundary must dominate the trailing-days lower bound.
    conn.execute("UPDATE phases SET start_time='2099-01-01T00:00:00Z' WHERE phase='20.1'")
    assert run(days=1)['scope']['start_at']=='2099-01-01T00:00:00+00:00'


def test_recent_days_metadata_and_sql_use_the_same_cutoff(query):
    run,conn=query
    statements=[];conn.set_trace_callback(statements.append)
    scope=run(days=1)['scope']
    start=datetime.fromisoformat(scope['start_at'])
    end=datetime.fromisoformat(scope['end_at'])
    assert (end-start).total_seconds()==86400
    assert any(start.replace(tzinfo=None).isoformat() in sql for sql in statements if 'SELECT card_ids_text' in sql)


def test_whole_season_uses_earliest_phase_in_that_season(query):
    run,_=query
    scope=run(all_phases=True)['scope']
    assert scope['phase'] is None and scope['start_at']=='2026-11-01T00:00:00+00:00'


def test_missing_phase_metadata_does_not_invent_a_start(query):
    run,conn=query
    conn.execute('DROP TABLE phases')
    assert run()['scope']['start_at'] is None
