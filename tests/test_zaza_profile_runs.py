import sqlite3

import pytest

from tools.zaza_profile_runs import collect_profile_runs, initialize_store, store_runs


class FakeClient:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def get(self, path, params):
        self.calls.append((path, dict(params)))
        return self.pages[len(self.calls) - 1]


def test_first_sync_follows_observed_cursor_contract_until_short_page():
    first = [{"id": f"r{i}", "createdAt": f"2026-10-12T00:{19-i:02d}:00Z"} for i in range(20)]
    client = FakeClient([first, [{"id": "r-1", "createdAt": "2026-10-12T00:00:00Z"}]])

    result = collect_profile_runs(client, "profile-1", mode="initial")

    assert [run["id"] for run in result] == [f"r{i}" for i in range(20)] + ["r-1"]
    assert client.calls[0][1] == {"profileId": "profile-1", "sort": "newest", "order": "desc"}
    assert client.calls[1][1]["cursorCreatedAt"] == "2026-10-12T00:00:00Z"
    assert client.calls[1][1]["cursorId"] == "r19"


def test_scheduled_sync_requests_only_first_page_and_retries_are_deduped(tmp_path):
    db = tmp_path / "profile-runs.sqlite"
    initialize_store(db)
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO profile_runs VALUES (?, ?, ?)", ("profile-1", "old", '{"id":"old"}'))

    client = FakeClient([[{"id": "new", "createdAt": "2026-10-12T00:03:00Z"},
                         {"id": "old", "createdAt": "2026-10-12T00:02:00Z"}]])
    runs = collect_profile_runs(client, "profile-1", mode="scheduled")
    inserted = store_runs(db, "profile-1", runs)
    inserted_again = store_runs(db, "profile-1", runs)

    assert inserted == 1
    assert inserted_again == 0
    assert len(client.calls) == 1
    with sqlite3.connect(db) as conn:
        assert [r[0] for r in conn.execute("SELECT run_id FROM profile_runs ORDER BY run_id")] == ["new", "old"]


def test_scoped_sync_initial_then_first_page_and_phase_rollover(tmp_path):
    from tools.zaza_profile_runs import sync_profile_runs
    db = tmp_path / "isolated.sqlite"
    first = [{"id": f"r{i}", "profileId": "profile-1", "createdAt": "2026-10-12T00:10:00Z"} for i in range(20)]
    client = FakeClient([first, [{"id": "tail", "createdAt": "2026-10-12T00:01:00Z"}]])
    args = dict(season=19, phase="19.1", created_after="2026-10-12T00:00:00Z", created_before="2026-10-13T00:00:00Z")
    assert sync_profile_runs(db, client, "profile-1", **args)["mode"] == "initial"
    assert len(client.calls) == 2
    assert client.calls[0][1]["createdAfter"] == args["created_after"]
    repeat = FakeClient([[{"id": "fresh", "createdAt": "2026-10-12T00:11:00Z"}]])
    result = sync_profile_runs(db, repeat, "profile-1", **args)
    assert result["mode"] == "scheduled" and result["inserted"] == 1
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT count(*) FROM profile_runs").fetchone()[0] == 22
    args["phase"] = "19.2"
    assert sync_profile_runs(db, FakeClient([[]]), "profile-1", **args)["mode"] == "initial"


def test_failure_never_marks_complete_and_history_survives(tmp_path):
    from tools.zaza_profile_runs import sync_profile_runs
    db = tmp_path / "isolated.sqlite"
    initialize_store(db)
    store_runs(db, "profile-1", [{"id": "historic"}])
    args = dict(season=19, phase="19.1", created_after="2026-10-12T00:00:00Z")
    with pytest.raises(ValueError):
        sync_profile_runs(db, FakeClient([{"error": "Unauthorized"}]), "profile-1", **args)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT count(*) FROM profile_runs").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM profile_sync").fetchone()[0] == 0
    assert sync_profile_runs(db, FakeClient([[]]), "profile-1", **args)["mode"] == "initial"


def test_repeated_cursor_fails_instead_of_marking_complete():
    page = [{"id": f"r{i}", "createdAt": "2026-10-12T00:10:00Z"} for i in range(20)]
    with pytest.raises(ValueError, match="cursor"):
        collect_profile_runs(FakeClient([page, page]), "profile-1", mode="initial")


def test_isolation_guards_production_and_existing_runs_database(tmp_path):
    with pytest.raises(ValueError, match="isolated"):
        initialize_store("/opt/qiubot/data/bazaar_runs.db")
    db = tmp_path / "source.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE runs(id TEXT)")
    with pytest.raises(ValueError, match="isolated"):
        initialize_store(db)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [("runs",)]


def test_http_adapter_reports_auth_blocker():
    from types import SimpleNamespace
    from tools.zaza_profile_runs import HTTPProfileClient, ProfileRunsAuthError
    class Session:
        def get(self, url, **kwargs):
            return SimpleNamespace(status_code=401, text='{"error":"Unauthorized"}')
    with pytest.raises(ProfileRunsAuthError):
        collect_profile_runs(HTTPProfileClient(Session()), "profile-1", mode="initial")


def test_sync_rejects_missing_phase_boundary_and_cross_profile_rows(tmp_path):
    from tools.zaza_profile_runs import sync_profile_runs
    with pytest.raises(ValueError, match="boundary"):
        sync_profile_runs(tmp_path / "local.db", FakeClient([[]]), "profile-1", season=19, phase="19.1")
    with pytest.raises(ValueError, match="profile"):
        collect_profile_runs(FakeClient([[{"id":"other", "profileId":"profile-2", "createdAt":"2026-10-12T00:00:00Z"}]]), "profile-1")


def test_invalid_page_shape_is_not_treated_as_empty_or_saved():
    client = FakeClient([{"runs": []}])
    with pytest.raises(ValueError, match="array"):
        collect_profile_runs(client, "profile-1", mode="initial")


@pytest.mark.parametrize('in_scope', [0, 6])
def test_initial_sync_stops_when_profile_endpoint_ignores_lower_boundary(in_scope):
    page = [{'id': f'r{i}', 'profileId': 'profile-1',
             'createdAt': '2026-10-08T01:00:00Z' if i < in_scope else '2026-10-07T22:27:37.758125+00:00'}
            for i in range(20)]
    client = FakeClient([page])  # A historical second page must never be requested.
    runs = collect_profile_runs(client, 'profile-1', mode='initial', created_after='2026-10-08T01:00:00Z')
    assert len(runs) == in_scope
    assert len(client.calls) == 1


def test_initial_sync_continues_across_timestamp_ties_at_lower_boundary():
    page = [{'id': f'r{i}', 'createdAt': '2026-10-08T01:00:00Z'} for i in range(20)]
    client = FakeClient([page, [{'id':'tail','createdAt':'2026-10-08T01:00:00Z'}]])
    assert len(collect_profile_runs(client, 'profile-1', mode='initial', created_after='2026-10-08T01:00:00Z')) == 21
    assert len(client.calls) == 2
