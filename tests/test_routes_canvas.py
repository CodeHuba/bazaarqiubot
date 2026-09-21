import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "web_runs" / "static" / "routes.js"


def test_routes_canvas_merges_same_day_node_and_invalidates_expanded_descendants():
    source = JS.read_text(encoding="utf-8")
    assert "const nodeKey=(day,id)=>`${day}:${id}`" in source
    assert "if(!state.nodes.has(k))" in source
    assert "if(merged.length!==before.length&&state.expanded.has(k))" in source
    assert "clearOutgoing(k)" in source


def test_routes_canvas_posts_path_sequences_without_run_ids():
    source = JS.read_text(encoding="utf-8")
    assert "api('/api/routes/daily/expand',{method:'POST'" in source
    assert "paths:state.paths.get(k)||[]" in source
    assert "run_id" not in source


def test_routes_canvas_has_pan_zoom_fit_and_node_rankings_without_runs_jump():
    source = JS.read_text(encoding="utf-8")
    for token in ("pointerdown", "pointermove", "wheel", "fitView", "推荐阵容 Top 3", "关联牌 Top 8"):
        assert token in source
    assert "查询相似真实阵容" not in source
    assert "/runs?" not in source


def test_routes_javascript_syntax_is_valid():
    result = subprocess.run(["node", "--check", str(JS)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
