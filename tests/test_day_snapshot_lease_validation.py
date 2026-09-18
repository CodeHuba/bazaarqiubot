def test_stale_lease_cannot_overwrite_new_worker_result(tmp_path):
    from web_runs.day_snapshot_store import DaySnapshotStore

    store = DaySnapshotStore(tmp_path / 'day.db')
    store.initialize()
    store.enqueue_if_eligible('run-1', 18, '18.2', 'Vanessa', 'Legendary', 10,
                              now='2026-09-18T00:00:00')
    first = store.claim(now='2026-09-18T00:00:00', lease_minutes=1)[0]
    second = store.claim(now='2026-09-18T00:02:00', lease_minutes=1)[0]

    try:
        store.complete_success('run-1', [{'eventId': 'old', 'day': 1}], first['lease_token'])
    except ValueError as exc:
        assert 'stale' in str(exc)
    else:
        raise AssertionError('stale worker unexpectedly completed current lease')

    store.complete_success('run-1', [{'eventId': 'new', 'day': 1}], second['lease_token'])


def test_invalid_snapshot_entries_cannot_mark_job_success(tmp_path):
    from web_runs.day_snapshot_store import DaySnapshotStore

    store = DaySnapshotStore(tmp_path / 'day.db')
    store.initialize()
    store.enqueue_if_eligible('run-1', 18, '18.2', 'Vanessa', 'Legendary', 10,
                              now='2026-09-18T00:00:00')
    job = store.claim(now='2026-09-18T00:00:00')[0]

    try:
        store.complete_success('run-1', [{'eventId': '', 'day': 0}, {'eventId': 'e', 'day': True}],
                               job['lease_token'])
    except ValueError as exc:
        assert 'no valid' in str(exc)
    else:
        raise AssertionError('invalid snapshots unexpectedly completed job')


def test_snapshots_preserve_days_beyond_ten_for_ten_win_runs(tmp_path):
    import sqlite3
    from web_runs.day_snapshot_store import DaySnapshotStore

    store = DaySnapshotStore(tmp_path / 'day.db')
    store.initialize()
    store.enqueue_if_eligible('run-day-12', 18, '18.2', 'Vanessa', 'Legendary', 10)
    job = store.claim()[0]
    store.complete_success(
        'run-day-12',
        [
            {'eventId': 'day-1', 'day': 1},
            {'eventId': 'day-10', 'day': 10},
            {'eventId': 'day-11', 'day': 11},
            {'eventId': 'day-12', 'day': 12},
        ],
        job['lease_token'],
    )

    with sqlite3.connect(tmp_path / 'day.db') as conn:
        assert [row[0] for row in conn.execute(
            'SELECT day FROM combat_snapshots WHERE run_id=? ORDER BY day',
            ('run-day-12',),
        )] == [1, 10, 11, 12]


def test_max_attempt_processing_lease_is_not_failed_before_expiry(tmp_path):
    import sqlite3
    from web_runs.day_snapshot_store import DaySnapshotStore

    db_path = tmp_path / 'day.db'
    store = DaySnapshotStore(db_path)
    store.initialize()
    store.enqueue_if_eligible('run-1', 18, '18.2', 'Vanessa', 'Legendary', 10,
                              now='2026-09-18T00:00:00')
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE snapshot_jobs SET status='retry', attempts=4, next_attempt_at=? WHERE run_id=?",
            ('2026-09-18T00:05:00', 'run-1'),
        )

    fifth = store.claim(limit=1, now='2026-09-18T00:05:00',
                        lease_minutes=10, max_attempts=5)
    assert fifth and fifth[0]['attempts'] == 5

    assert store.claim(limit=1, now='2026-09-18T00:05:30',
                       lease_minutes=10, max_attempts=5) == []
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT status FROM snapshot_jobs WHERE run_id='run-1'"
        ).fetchone()[0] == 'processing'

    assert store.claim(limit=1, now='2026-09-18T00:16:00',
                       lease_minutes=10, max_attempts=5) == []
    with sqlite3.connect(db_path) as conn:
        assert conn.execute(
            "SELECT status FROM snapshot_jobs WHERE run_id='run-1'"
        ).fetchone()[0] == 'failed'
