import ast
import os
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
import pytest


def load_cutoff(name="collection_created_after"):
    source = Path(__file__).parents[1] / "tools" / "bazaardb_runs_collector.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    selected = [
        node for node in tree.body
        if (isinstance(node, ast.FunctionDef) and node.name in (
            "collection_created_after", "run_in_current_phase", "save_runs"))
        or (isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "PHASE_START_UTC"
            for target in node.targets
        ))
    ]
    namespace = dict(os=os, datetime=datetime, timedelta=timedelta,
                     timezone=timezone, format_datetime=format_datetime)
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(source), "exec"), namespace)
    return namespace[name]


def test_first_collection_query_starts_at_phase_boundary(monkeypatch):
    monkeypatch.delenv("BAZAAR_RUNS_CREATED_AFTER", raising=False)
    now = datetime(2026, 10, 28, 17, 30, tzinfo=timezone.utc)
    assert load_cutoff()(now) == "Wed, 28 Oct 2026 16:00:00 GMT"


def test_collection_keeps_rolling_window_after_phase_boundary(monkeypatch):
    monkeypatch.delenv("BAZAAR_RUNS_CREATED_AFTER", raising=False)
    now = datetime(2026, 10, 28, 22, 30, tzinfo=timezone.utc)
    assert load_cutoff()(now) == "Wed, 28 Oct 2026 18:30:00 GMT"


def test_explicit_collection_override_is_preserved(monkeypatch):
    monkeypatch.setenv("BAZAAR_RUNS_CREATED_AFTER", "Thu, 08 Oct 2026 01:00:00 GMT")
    assert load_cutoff()() == "Thu, 08 Oct 2026 01:00:00 GMT"


@pytest.mark.parametrize("timestamp,accepted", [
    ("2026-10-28T15:59:59.999Z", False),
    ("2026-10-28T16:00:00Z", True),
    ("2026-10-29T00:00:00+08:00", True),
    ("2026-10-28T17:30:00+00:00", True),
    ("2026-10-29T00:00:00", False),
    ("invalid", False),
    (None, False),
])
def test_ingest_boundary_handles_offsets_and_invalid_dates(timestamp, accepted):
    assert load_cutoff("run_in_current_phase")({"createdAt": timestamp}) is accepted


def test_previous_phase_is_neither_posted_nor_recorded_locally():
    save = load_cutoff("save_runs")
    calls = []

    class Session:
        def post(self, url, json, **kwargs):
            calls.append(json)
            return self

        def raise_for_status(self):
            pass

        def json(self):
            return {"new": 1, "total": 1}

    class Connection:
        recorded = []

        def execute(self, sql, params):
            self.recorded.append(params[0])

        def commit(self):
            pass

    session = Session()
    save.__globals__.update(API_SESSION=session, requests=session,
                            INGEST_API="test", INGEST_TOKEN="test")
    old = {"id": "old", "createdAt": "2026-10-28T15:59:59Z", "statWins": 10}
    new = {"id": "new", "createdAt": "2026-10-28T16:01:00Z", "statWins": 1}
    low_wins = {"id": "zero", "createdAt": "2026-10-28T16:01:00Z", "statWins": 0}
    conn = Connection()
    assert save(conn, [old, new, low_wins]) == 1
    assert calls == [[new]]
    assert conn.recorded == ["new"]
