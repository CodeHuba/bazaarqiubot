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
    assert "branch.path_indexes" in js
    assert "parentPaths.filter" in js
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


def test_routes_canvas_reflows_from_real_dom_heights_with_a_40px_gap():
    js = source()
    assert "function measureAndRelayout()" in js
    assert "offsetHeight" in js
    assert "NODE_GAP=40" in js
    assert "requestAnimationFrame(measureAndRelayout)" in js
    assert "i*300" not in js


def test_routes_canvas_separates_detail_selection_from_expand_toggle():
    js = source()
    css = (ROOT / "web_runs" / "static" / "routes.css").read_text(encoding="utf-8")
    assert 'class=\"node-expand\"' in js
    assert "toggleNode(k)" in js
    assert "collapseNode(k)" in js
    assert "b.querySelector('.node-expand').onclick" in js
    assert ".node-expand{" in css


def test_routes_canvas_allows_only_one_expanded_node_per_day_and_prunes_descendants():
    js = source()
    assert "function collapseNode(k)" in js
    assert "invalidateOutgoing(k)" in js
    assert "state.expanded.delete(k)" in js
    assert "function collapseExpandedOnDay(k)" in js
    assert "other!==k&&node.day===current.day" in js


def test_routes_canvas_labels_empty_cores_as_other_composition():
    js = source()
    assert "const nodeTitle=" in js
    assert "'其他阵容'" in js
    assert "nodeTitle(n)" in js


def test_routes_canvas_shows_representative_cards_without_empty_placeholder():
    js = source()
    css = (ROOT / "web_runs" / "static" / "routes.css").read_text(encoding="utf-8")
    assert "function nodeCards(n)" in js
    assert "function nodeCardsHtml(n" in js
    assert "代表卡牌" in js
    assert "signature_cards" in js
    assert "代表卡牌用于识别该分散阵容" in js
    assert "representative-card-list" in css
    assert "cardsHtml(nodeCards(n),true)" not in js


def test_routes_canvas_places_branches_on_actual_day_and_marks_cross_day_edges():
    js = source()
    css = (ROOT / "web_runs" / "static" / "routes.css").read_text(encoding="utf-8")
    assert "day:Number(branch.day||n.day)" in js
    assert "const dayGap=b.day-a.day" in js
    assert "cross-day" in js
    assert "缺失 Day" in js and "跨 Day" in js
    assert "${fmt(e.count)}/${fmt(e.observable)}局 · ${pct(e.rate)}" in js
    assert ".route-edges .cross-day" in css
    assert "d<=99" in js
    assert "maxY=Math.max(...ns.map(n=>n.y+(n.measuredHeight||220)))" in js
    assert "正在展开后续观测" in js
    assert "暂无后续观测快照" in js


def test_routes_canvas_can_expand_again_after_collapsing():
    js = source()
    harness = r'''
renderGraph=()=>{};layoutGraph=()=>{};setStatus=()=>{};
globalThis.fetch=async()=>({ok:true,json:async()=>({path_run_count:5,path_observable_runs:5,display_threshold:3,branches:[]})});
const key='3:root';
state.version='v';state.hero='Vanessa';state.root=key;
state.nodes.set(key,{day:3,node_id:'root',run_count:5,path_run_count:5});
state.paths.set(key,[[{day:3,node_id:'root'}]]);state.pathRevision.set(key,0);
await expandNode(key);
if(!state.expanded.has(key))throw new Error('first expand failed');
collapseNode(key);
if(state.expanded.has(key))throw new Error('collapse failed');
await expandNode(key);
if(!state.expanded.has(key))throw new Error('second expand failed');
console.log('EXPAND_COLLAPSE_EXPAND_OK');
'''
    executable = js.replace("init();\n})();", "(async()=>{" + harness + "})().catch(e=>{console.error(e);process.exitCode=1});\n})();")
    result = subprocess.run(["node", "-e", executable], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "EXPAND_COLLAPSE_EXPAND_OK" in result.stdout


def test_routes_canvas_can_reopen_while_aborted_expand_is_finishing():
    js = source()
    harness = r'''
renderGraph=()=>{};layoutGraph=()=>{};setStatus=()=>{};
let calls=0;
globalThis.fetch=(url,options)=>new Promise((resolve,reject)=>{
  calls++;
  const timer=setTimeout(()=>resolve({ok:true,json:async()=>({path_run_count:5,path_observable_runs:5,display_threshold:3,branches:[]})}),calls===1?30:0);
  options.signal.addEventListener('abort',()=>{clearTimeout(timer);const e=new Error('aborted');e.name='AbortError';reject(e)});
});
const key='3:root';
state.version='v';state.hero='Vanessa';state.root=key;
state.nodes.set(key,{day:3,node_id:'root',run_count:5,path_run_count:5});
state.paths.set(key,[[{day:3,node_id:'root'}]]);state.pathRevision.set(key,0);
const first=expandNode(key);
collapseNode(key);
const second=expandNode(key);
await Promise.allSettled([first,second]);
if(calls!==2)throw new Error(`expected 2 requests, got ${calls}`);
if(!state.expanded.has(key))throw new Error('reopen after abort failed');
console.log('ABORT_REOPEN_OK');
'''
    executable = js.replace("init();\n})();", "(async()=>{" + harness + "})().catch(e=>{console.error(e);process.exitCode=1});\n})();")
    result = subprocess.run(["node", "-e", executable], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "ABORT_REOPEN_OK" in result.stdout


def test_routes_canvas_ignores_late_response_after_collapse_then_reopens():
    js = source()
    harness = r'''
renderGraph=()=>{};layoutGraph=()=>{};setStatus=()=>{};
let calls=0,releaseFirst;
globalThis.fetch=async()=>{
  calls++;
  if(calls===1)return {ok:true,json:()=>new Promise(resolve=>{releaseFirst=()=>resolve({path_run_count:5,path_observable_runs:5,display_threshold:3,branches:[]})})};
  return {ok:true,json:async()=>({path_run_count:5,path_observable_runs:5,display_threshold:3,branches:[]})};
};
const key='3:root';
state.version='v';state.hero='Vanessa';state.root=key;
state.nodes.set(key,{day:3,node_id:'root',run_count:5,path_run_count:5});
state.paths.set(key,[[{day:3,node_id:'root'}]]);state.pathRevision.set(key,0);
const first=expandNode(key);
await new Promise(resolve=>setTimeout(resolve,0));
collapseNode(key);
releaseFirst();
await first;
if(state.expanded.has(key))throw new Error('late response reopened collapsed node');
await expandNode(key);
if(calls!==2)throw new Error(`expected 2 requests, got ${calls}`);
if(!state.expanded.has(key))throw new Error('reopen after late response failed');
console.log('LATE_RESPONSE_REOPEN_OK');
'''
    executable = js.replace("init();\n})();", "(async()=>{" + harness + "})().catch(e=>{console.error(e);process.exitCode=1});\n})();")
    result = subprocess.run(["node", "-e", executable], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "LATE_RESPONSE_REOPEN_OK" in result.stdout


def test_route_share_links_preserve_preview_prefix():
    js = source()
    share_html = (ROOT / "web_runs" / "static" / "routes-share.html").read_text(encoding="utf-8")
    css = (ROOT / "web_runs" / "static" / "routes.css").read_text(encoding="utf-8")
    assert "function shareBasePath()" in js
    assert "${shareBasePath()}share/routes/" in js
    assert 'href="/share-static/routes.css' in share_html
    assert "../../api/routes/share/" in share_html
    assert "../../routes?hero=" in share_html
    assert "recommended_compositions:detail.recommended_compositions||[]" in share_html
    assert "function shareCardImage" in share_html
    assert "shareCardImage(c)" in share_html
    assert "shareHero=share.hero_zh||HERO_ZH[share.hero]||share.hero" in share_html
    assert "share-action-button" in share_html
    assert "preloadShareImages" in share_html
    assert "share-card-art size-${size}" in share_html
    assert "share-card-art.size-Small" in css
    assert "当天占比" in share_html
    assert "核心共现" in share_html
    assert "associated_cards:detail.associated_cards||[]" in share_html
    assert "associated_skills:detail.associated_skills||[]" in share_html
    assert "关联牌榜单" in share_html
    assert "关联技能榜单" in share_html
    assert "location.href.split('#')[0]" in share_html
    assert "share-public-url" in share_html
    assert "hidden id=\"share-route\"" in (ROOT / "web_runs" / "static" / "routes.html").read_text(encoding="utf-8")
    assert "id=\"share-status\"" not in share_html
    assert "share-card-items" in share_html
    assert "flex-wrap:nowrap" in css
    assert "share-data-region" in css


def test_routes_javascript_syntax_is_valid():
    result = subprocess.run(["node", "--check", str(JS)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
