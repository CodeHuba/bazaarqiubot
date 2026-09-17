from pathlib import Path


APP = Path(__file__).parents[1] / "web_runs" / "app.py"


def test_ingest_invalidates_all_run_derived_caches():
    text = APP.read_text(encoding="utf-8")
    start = text.index("def api_ingest()")
    next_route = text.find("\n@app.route(", start + 1)
    ingest = text[start : next_route if next_route != -1 else len(text)]

    assert "if new_count:" in ingest
    assert "_runs_query._card_tier_cache.clear()" in ingest
    assert "_runs_query._hero_overview_cache.clear()" in ingest
