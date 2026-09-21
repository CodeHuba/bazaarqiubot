import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "web_runs" / "static" / "routes.js"


def source():
    return JS.read_text(encoding="utf-8")


def test_routes_canvas_uses_expand_response_for_converged_node_samples():
    js = source()
    assert "current.path_run_count=data.path_run_count" in js
    assert "mergeBranch(k,branch,data.path_observable_runs)" in js
    assert "observable,rate:branch.transition_rate" in js
    assert "${fmt(e.count)}/${fmt(e.observable)}局 · ${pct(e.rate)}" in js


def test_routes_canvas_discards_stale_expands_and_retries_after_new_paths_arrive():
    js = source()
    assert "graphGeneration" in js
    assert "pathRevision" in js
    assert "expandRequests" in js
    assert "generation!==state.graphGeneration||revision!==state.pathRevision.get(k)" in js
    assert "expandNode(k)" in js


def test_routes_canvas_prunes_invalidated_subtrees_but_keeps_shared_nodes():
    js = source()
    assert "function invalidateOutgoing(parentKey)" in js
    assert "incomingEdges(childKey).length" in js
    assert "state.nodes.delete(childKey)" in js
    assert "refreshNodePaths(childKey)" in js


def test_routes_canvas_guards_hero_day_and_detail_requests_from_stale_overwrite():
    js = source()
    assert "pageToken" in js and "pageController" in js
    assert "detailToken" in js and "detailController" in js
    assert "token!==state.pageToken" in js
    assert "token!==state.detailToken||state.selected!==k" in js
    assert "AbortError" in js


def test_routes_canvas_canonicalizes_deduplicates_and_caps_complete_paths():
    js = source()
    assert "const MAX_PATHS=" in js
    assert "const pathKey=path=>" in js
    assert "function canonicalPaths(paths)" in js
    assert ".slice(0,MAX_PATHS)" not in js
    assert "超过 ${fmt(MAX_PATHS)} 条安全上限，暂不继续展开" in js
    assert "if(uniqueCount>MAX_PATHS)" in js
    assert "paths:requestPaths" in js
    assert "run_id" not in js


def test_routes_canvas_has_pan_zoom_fit_and_node_rankings_without_runs_jump():
    js = source()
    for token in ("pointerdown", "pointermove", "wheel", "fitView", "推荐阵容 Top 3", "关联牌 Top 8"):
        assert token in js
    assert "查询相似真实阵容" not in js
    assert "/runs?" not in js


def test_routes_canvas_usability_tweak_uses_ctrl_wheel_focus_and_larger_nodes():
    js = source()
    html = (ROOT / "web_runs" / "static" / "routes.html").read_text(encoding="utf-8")
    css = (ROOT / "web_runs" / "static" / "routes.css").read_text(encoding="utf-8")
    assert "if(!e.ctrlKey)return" in js
    assert "function setFocusMode(on)" in js
    assert "focus-view" in html and "聚焦画板" in html
    assert "width:clamp(520px,45vw,720px)" in css
    assert ".canvas-node{width:320px" in css
    assert ".canvas-node .route-cards{overflow:visible;flex-wrap:wrap" in css
    assert ".route-workspace.focused{position:fixed;inset:0" in css


def test_routes_javascript_syntax_is_valid():
    result = subprocess.run(["node", "--check", str(JS)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
