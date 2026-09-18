from pathlib import Path


ROOT = Path(__file__).parents[1]
STATIC = ROOT / "web_runs" / "static"


def test_runs_page_loads_existing_unified_playful_query_progress_assets():
    html = (STATIC / "runs.html").read_text(encoding="utf-8")
    css_path = STATIC / "query-progress.css"
    js_path = STATIC / "query-progress.js"

    assert '<link rel="stylesheet" href="/static/query-progress.css">' in html
    assert '<script src="/static/query-progress.js"></script>' in html
    assert css_path.is_file() and css_path.stat().st_size > 0
    assert js_path.is_file() and js_path.stat().st_size > 0
    assert ".bz-query-progress.is-active" in css_path.read_text(encoding="utf-8")
    assert "食人鱼正在阵容海里追踪完整构筑" in js_path.read_text(encoding="utf-8")


def test_runs_page_cancels_active_request_before_rate_limit_can_return():
    html = (STATIC / "runs.html").read_text(encoding="utf-8")
    start = html.index("async function queryRuns(page)")
    end = html.index("\nfunction renderRuns", start)
    function = html[start:end]

    abort_pos = function.index("activeRunsController.abort()")
    rate_pos = function.index("const err = checkRate();")
    assert abort_pos < rate_pos


def test_runs_page_has_timeout_and_cancels_previous_request():
    html = (STATIC / "runs.html").read_text(encoding="utf-8")
    start = html.index("async function queryRuns(page)")
    end = html.index("\nfunction renderRuns", start)
    function = html[start:end]

    assert "new AbortController()" in html
    assert "activeRunsController.abort()" in function
    assert "setTimeout" in function
    assert "clearInterval(_runsTimer)" in function
    assert "finally" in function
