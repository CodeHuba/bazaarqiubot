"""Check report-window correctness independently from the QQ runtime."""
from datetime import datetime, timedelta
import runpy
from pathlib import Path

MODULE = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'tools/preview_watch_cards.py'))
CST = MODULE['CST']
summarize = MODULE['summarize']


def test_last_scheduled_cutoff_before_ten_and_fixed_timezone():
    now = datetime(2026, 10, 26, 9, 59, tzinfo=CST)
    start, end = MODULE['report_window'](now)
    assert end == datetime(2026, 10, 25, 10, tzinfo=CST)
    assert end-start == timedelta(hours=24)


def test_report_excludes_future_points_and_sorts_history():
    start = datetime(2026, 10, 25, 10, tzinfo=CST)
    end = start+timedelta(days=1)
    result = summarize([
        dict(timestamp='2026-10-26 17:55:00', rating=999, position=800),
        dict(timestamp='2026-10-26 09:55:00', rating=967, position=863),
        dict(timestamp='2026-10-25 09:55:00', rating=938, position=1140),
    ], start, end)
    assert result['rating_delta'] == 29
    assert result['rank_gain'] == 277
    assert result['latest']['rating'] == 967


def test_old_records_are_not_repeated_as_new_changes():
    start = datetime(2026, 10, 25, 10, tzinfo=CST)
    result = summarize([
        dict(timestamp='2026-10-22 09:55:00', rating=900, position=1000),
        dict(timestamp='2026-10-23 09:55:00', rating=950, position=900),
    ], start, start+timedelta(days=1))
    assert result['state'] == 'no_new_records'
    assert result['rating_delta'] is None


def test_missing_baseline_is_not_zero_change():
    start = datetime(2026, 10, 25, 10, tzinfo=CST)
    result = summarize([dict(timestamp='2026-10-26 09:55:00', rating=967, position=863)], start, start+timedelta(days=1))
    assert result['state'] == 'baseline_missing'
    assert result['rank_gain'] is None
