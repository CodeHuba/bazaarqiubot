from pathlib import Path


APP = Path(__file__).parents[1] / 'web_runs' / 'app.py'
WORKER = Path(__file__).parents[1] / 'tools' / 'bazaardb_snapshot_worker.py'


def test_snapshot_api_limits_body_claim_input_and_does_not_leak_db_path():
    text = APP.read_text(encoding='utf-8')
    start = text.index("def api_snapshot_jobs_claim")
    end = text.find("\n@app.route(", start + 1)
    claim = text[start:end]
    status_start = text.index("def api_snapshot_jobs_status")
    status_end = text.find("\n@app.route(", status_start + 1)
    status = text[status_start:status_end]

    assert "MAX_CONTENT_LENGTH" in text
    assert "not 1 <= limit <= 20" in claim
    assert "DAY_SNAPSHOT_DB_PATH" not in status
    assert "'store': 'day-snapshots'" in status


def test_completion_requires_lease_token_and_caps_snapshot_count():
    text = APP.read_text(encoding='utf-8')
    start = text.index("def api_snapshot_job_complete")
    end = text.find("\n@app.route(", start + 1)
    route = text[start:end]

    assert "lease_token = body.get('leaseToken')" in route
    assert "valid leaseToken is required" in route
    assert "len(snapshots) > 50" in route


def test_worker_reports_auth_refresh_failure_for_retry_and_uses_five_parallel_slots():
    text = WORKER.read_text(encoding='utf-8')

    assert "except Exception as exc:" in text
    assert "report(retry_control, job['run_id'], job['lease_token'], error=exc)" in text
    assert "SNAPSHOT_WORKERS = int(os.getenv('SNAPSHOT_WORKERS', '5'))" in text
    assert "response.status_code in (401, 403, 429)" in text
