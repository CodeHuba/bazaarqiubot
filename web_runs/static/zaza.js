'use strict';
const $ = selector => document.querySelector(selector);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const labels = {pending:'待审核',creating:'创建中',active:'已启用',failed:'收集失败',stopped:'已停止',rejected:'已拒绝',cancelled:'已取消',deleted:'已删除'};
const state = {id:null,page:1,cards:[],detailRequest:0,listRequest:0,suggestionRequest:0,poll:null,detailController:null};
async function api(url, options = {}) {
  const response = await fetch(url, {credentials:'same-origin',...options});
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw Error(body.error || (response.status === 401 ? '管理身份已失效，请重新登录。' : `请求失败 (${response.status})`));
  return body;
}
function safeImage(value) { return /^(https?:\/\/|\/[^/])/.test(String(value || '')) ? value : ''; }
function cardMarkup(card, skill = false) {
  const name = card.name || card.zh || card.name_en || card.card_id || card.cardId || card.skill_id || '未知卡牌';
  const size = String(card.size || 'Small').toLowerCase();
  const width = skill ? 68 : ({small:70,medium:140,large:210}[size] || 70);
  const image = safeImage(card.img || card.art);
  return `<div class="card" style="width:${width}px">${image ? `<img src="${esc(image)}" alt="${esc(name)}" loading="lazy">` : '<div class="muted">暂无图片</div>'}<div>${esc(name)}</div></div>`;
}
function dateText(value) { if (!value) return '时间未知'; const date = new Date(value); return isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN'); }
async function list() {
  const request = ++state.listRequest;
  const query = $('#search').value.trim();
  try {
    const data = await api('/api/zaza/spaces?query=' + encodeURIComponent(query));
    if (request !== state.listRequest || $('#search').value.trim() !== query) return;
    $('#spaces').innerHTML = data.spaces.length ? data.spaces.map(space => `<a class="space" href="#${encodeURIComponent(space.id)}"><b>${esc(space.name)}</b><span class="status">${esc(labels[space.status] || space.status)}</span></a>`).join('') : '<p class="muted">暂无匹配空间</p>';
  } catch (error) { if (request === state.listRequest) $('#spaces').textContent = error.message; }
}
function filters() {
  const query = new URLSearchParams({page:state.page,perPage:5});
  if ($('#hero').value) query.set('hero',$('#hero').value);
  const days = Number($('#recent-days').value);
  if (Number.isInteger(days) && days > 0) query.set('start',new Date(Date.now() - days * 86400000).toISOString());
  if (state.cards.length) query.set('cards',state.cards.map(card => card.cardId).join(','));
  return query;
}
function renderDetail(data) {
  const summary = data.summary || {}, scope = data.scope || {}, profile = data.profile || {};
  $('#title').textContent = data.space.name;
  const url = /^https:\/\/bazaardb\.gg\/run\/profile\/[0-9a-f-]+\/?$/i.test(profile.url || '') ? profile.url : '';
  $('#profile-info').innerHTML = `<strong>${esc(profile.nickname || '昵称待收集')}</strong>${url ? ` · <a class="tag" href="${esc(url)}" target="_blank" rel="noopener noreferrer">BazaarDB 个人页 ↗</a>` : ''}`;
  $('#scope').textContent = `第 ${scope.season ?? '—'} 赛季 · ${scope.phase ?? '阶段待确认'} · 仅统计此阶段本地已收集对局`;
  const metrics = [['本地对局',summary.run_count ?? 0],['平均胜场',summary.average_wins ?? 0],['10 胜率',`${(Number(summary.ten_win_rate ?? 0)*100).toFixed(1)}%`],['空间状态',labels[data.space.status] || data.space.status]];
  $('#metrics').innerHTML = metrics.map(([name,value]) => `<div class="metric"><b>${esc(value)}</b>${name}</div>`).join('');
  const heroes = summary.heroes || summary.hero_distribution || {};
  const distribution = Array.isArray(heroes) ? heroes.map(h => [h.hero,h.count]) : Object.entries(heroes);
  $('#hero-distribution').innerHTML = distribution.map(([hero,count]) => `<span class="tag">${esc(hero)} ${esc(count)} 局</span>`).join('');
  const selectedHero = $('#hero').value;
  $('#hero').innerHTML = '<option value="">全部职业</option>' + distribution.map(([hero]) => `<option value="${esc(hero)}">${esc(hero)}</option>`).join('');
  if (selectedHero && !distribution.some(([hero]) => hero === selectedHero)) $('#hero').add(new Option(selectedHero,selectedHero));
  $('#hero').value = selectedHero;
  $('#runs').innerHTML = data.runs.length ? data.runs.map(run => {
    const cards = run.items || run.cards || [];
    const heroZh = {Vanessa:'瓦内莎',Dooley:'杜利',Mak:'马克',Pygmalien:'皮格马利翁',Stelle:'斯黛尔',Jules:'朱尔斯',Karnok:'卡诺克','The Dragons':'双龙'};
    const chips = cards.map(card => window.zazaCardMarkup(card)).join('');
    return `<article class="run-item"><div class="run-row"><span class="run-hero">${esc(heroZh[run.hero] || run.hero || '未知职业')}</span><div class="run-meta"><div class="run-title">${esc(profile.nickname || '匿名')} · ${esc(dateText(run.time))}</div><div class="run-items compact-board">${chips}</div></div><span class="win-badge">${esc(run.wins ?? 0)}胜${esc(run.losses ?? 0)}负</span></div><a class="replay-link" href="/zaza/replay?space=${encodeURIComponent(state.id)}&run=${encodeURIComponent(run.run_key)}">打开回放详情 →</a></article>`;
  }).join('') : '<p class="muted">当前条件下暂无本地对局记录。</p>';
  const pagination = data.pagination || {page:state.page,total:data.runs.length,total_pages:1};
  const pages = pagination.total_pages ?? pagination.totalPages ?? Math.max(1,Math.ceil((pagination.total ?? 0)/5));
  $('#page-info').textContent = `第 ${pagination.page ?? state.page} / ${Math.max(1,pages)} 页 · ${pagination.total ?? data.runs.length} 局`;
  $('#previous').disabled = state.page <= 1; $('#next').disabled = !(pagination.has_next ?? pagination.hasNext ?? (state.page < pages));
}
async function load(poll = false) {
  if (!state.id) return;
  clearTimeout(state.poll);
  const request = ++state.detailRequest, id = state.id;
  state.detailController?.abort(); state.detailController = new AbortController();
  $('#detail').hidden = false; $('#detail').setAttribute('aria-busy','true');
  $('#detail-error').textContent = '';
  if (!poll) $('#runs').innerHTML = '<p class="muted">正在加载对局…</p>';
  try {
    const data = await api('/api/zaza/spaces/' + encodeURIComponent(id) + '?' + filters(), {signal:state.detailController.signal});
    if (request !== state.detailRequest || state.id !== id) return;
    renderDetail(data);
  } catch (error) { if (error.name !== 'AbortError' && request === state.detailRequest) $('#detail-error').textContent = error.message; }
  finally { if (request === state.detailRequest) { $('#detail').setAttribute('aria-busy','false'); state.poll = setTimeout(() => { if (!document.hidden) { load(true); list(); } else state.poll = setTimeout(() => load(true),15000); },15000); } }
}
function resetFilters() { state.page = 1; state.cards = []; $('#filters').reset(); $('#card-tags').innerHTML = ''; $('#card-suggestions').hidden = true; ++state.suggestionRequest; }
function selectSpace() {
  let id; try { id = decodeURIComponent(location.hash.slice(1)); } catch { $('#result').textContent = '空间链接无效'; return; }
  if (id === state.id) return;
  state.id = id || null; state.detailController?.abort(); ++state.detailRequest; clearTimeout(state.poll); resetFilters();
  $('#detail').hidden = !id;
  if (id) { $('#title').textContent = '正在加载空间…'; for (const field of ['profile-info','scope','metrics','hero-distribution']) $('#'+field).innerHTML = ''; load(); }
}
function renderTags() { $('#card-tags').innerHTML = state.cards.map((card,index) => `<span class="tag">${esc(card.zh || card.en)} <button type="button" data-index="${index}" aria-label="移除 ${esc(card.zh || card.en)}">×</button></span>`).join(''); }
let searchTimer, cardTimer;
$('#search').oninput = () => { ++state.listRequest; clearTimeout(searchTimer); searchTimer = setTimeout(list,250); };
$('#list-refresh').onclick = list; $('#detail-refresh').onclick = () => load();
$('#create').onsubmit = async event => {
  event.preventDefault(); const profileUrl = $('#profile').value.trim(), name = $('#name').value.trim();
  if (!/^https:\/\/bazaardb\.gg\/run\/profile\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/?$/i.test(profileUrl) || !name.endsWith('空间')) { $('#result').className = 'error'; $('#result').textContent = '请填写 https://bazaardb.gg/run/profile/UUID，名称须以空间结尾。'; return; }
  $('#create-submit').disabled = true; $('#result').className = 'muted'; $('#result').textContent = '提交中…';
  try { const data = await api('/api/zaza/spaces',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({profileUrl,name})}); $('#result').textContent = `已提交：${data.space.name} · ${labels[data.space.status] || data.space.status}`; event.target.reset(); location.hash = data.space.id; list(); }
  catch (error) { $('#result').className = 'error'; $('#result').textContent = error.message; }
  finally { $('#create-submit').disabled = false; }
};
$('#filters').onsubmit = event => { event.preventDefault(); const value = $('#recent-days').value; if (value && (!Number.isSafeInteger(Number(value)) || Number(value) < 1 || Number(value) > 365000)) { $('#detail-error').textContent = '最近天数请输入 1–365000 的整数，留空表示不限'; return; } state.page = 1; load(); };
$('#reset-filters').onclick = () => { resetFilters(); load(); };
$('#previous').onclick = () => { if (state.page > 1) { --state.page; load(); } }; $('#next').onclick = () => { ++state.page; load(); };
$('#card-search').oninput = () => {
  clearTimeout(cardTimer); const query = $('#card-search').value.trim(), request = ++state.suggestionRequest;
  $('#card-suggestions').hidden = true; $('#card-search').setAttribute('aria-expanded','false');
  if (!query) return;
  cardTimer = setTimeout(async () => {
    try { const rows = await api('/api/card_search?q=' + encodeURIComponent(query)); if (request !== state.suggestionRequest || $('#card-search').value.trim() !== query) return;
      const cards = rows.filter(card => card.cardId && !state.cards.some(selected => selected.cardId === card.cardId));
      $('#card-suggestions').innerHTML = cards.map((card,index) => `<button type="button" class="suggestion" role="option" data-index="${index}">${safeImage(card.img) ? `<img src="${esc(card.img)}" alt="">` : ''}<span>${esc(card.zh || card.en)}<br><small>${esc(card.en)}</small></span></button>`).join('') || '<p class="muted">没有找到相关卡牌</p>';
      $('#card-suggestions').hidden = false; $('#card-search').setAttribute('aria-expanded','true');
      $('#card-suggestions').onclick = event => { const button = event.target.closest('button[data-index]'); if (!button) return; state.cards.push(cards[Number(button.dataset.index)]); renderTags(); $('#card-search').value = ''; $('#card-suggestions').hidden = true; $('#card-search').setAttribute('aria-expanded','false'); ++state.suggestionRequest; $('#card-search').focus(); };
    } catch (error) { if (request === state.suggestionRequest) $('#detail-error').textContent = '卡牌搜索失败：'+error.message; }
  },200);
};
$('#card-search').onkeydown = event => { if (event.key === 'Escape') { $('#card-suggestions').hidden = true; $('#card-search').setAttribute('aria-expanded','false'); } else if (event.key === 'ArrowDown') { event.preventDefault(); $('#card-suggestions button')?.focus(); } };
$('#card-tags').onclick = event => { const button = event.target.closest('button[data-index]'); if (button) { state.cards.splice(Number(button.dataset.index),1); renderTags(); } };
$('#share').onclick = async () => { try { await navigator.clipboard.writeText(location.origin+location.pathname+'#'+encodeURIComponent(state.id)); $('#detail-error').textContent = '空间链接已复制，改名后链接仍有效。'; } catch { $('#detail-error').textContent = '复制失败，请复制浏览器地址栏中的空间链接。'; } };
window.addEventListener('hashchange',selectSpace); window.addEventListener('pagehide',() => { clearTimeout(state.poll); state.detailController?.abort(); }); list(); selectSpace();
