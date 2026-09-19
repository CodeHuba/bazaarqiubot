(() => {
  'use strict';
  const state = {
    version: 'latest', hero: 'Vanessa', day: 3, cores: [], selected: null,
    requestToken: 0, routeToken: 0, coresController: null, routeController: null,
  };
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  const pct = value => `${(Number(value || 0) * 100).toFixed(1)}%`;
  const fmt = value => Number(value || 0).toLocaleString('zh-CN');
  const safeImage = value => {
    if (!value) return '';
    try { const url = new URL(value, location.origin); return ['http:', 'https:'].includes(url.protocol) ? url.href : ''; }
    catch (_) { return ''; }
  };
  const api = async (path, signal) => {
    const response = await fetch(path, { credentials: 'same-origin', headers: { Accept: 'application/json' }, signal });
    if (!response.ok) { let message = `请求失败（${response.status}）`; try { message = (await response.json()).error || message; } catch (_) {} throw new Error(message); }
    return response.json();
  };
  const status = (message = '', error = false) => { const el = $('status'); el.textContent = message; el.className = `status${message ? ' visible' : ''}${error ? ' error' : ''}`; };
  const setMetadata = data => {
    $('meta-version').textContent = data.version_id || '—';
    $('meta-cutoff').textContent = data.source_cutoff ? new Date(data.source_cutoff).toLocaleString('zh-CN', {hour12:false}) : '—';
    const counts = data.sample_counts || data;
    $('meta-runs').textContent = fmt(counts.source_success_run_count);
    $('meta-snapshots').textContent = fmt(counts.source_snapshot_count);
    $('meta-finals').textContent = fmt(counts.final_composition_count);
  };
  const cardHtml = card => {
    const src = safeImage(card.img);
    const name = card.name || card.name_en || card.cardId;
    return `<div class="card" title="${esc(name)}"><div class="card-art">${src ? `<img src="${esc(src)}" alt="${esc(name)}" loading="lazy" referrerpolicy="no-referrer" onerror="this.replaceWith(Object.assign(document.createElement('span'),{className:'card-fallback',textContent:this.alt}))">` : `<span class="card-fallback">${esc(name)}</span>`}</div><div class="card-name">${esc(name)}</div></div>`;
  };
  const cardsHtml = cards => `<div class="cards">${(cards || []).map(cardHtml).join('') || '<span class="card-fallback">无卡牌信息</span>'}</div>`;
  const renderCores = () => {
    $('core-count').textContent = state.cores.length;
    if (!state.cores.length) { $('core-list').innerHTML = '<div class="route-empty">当前职业与 Day 暂无满足阈值的核心组合。</div>'; return; }
    $('core-list').innerHTML = state.cores.map(core => `<button class="core-item${state.selected === core.core_id ? ' active' : ''}" data-core="${esc(core.core_id)}"><div class="core-top"><span class="rank">#${fmt(core.rank)}</span><span class="coverage">覆盖 ${pct(core.coverage)}</span></div>${cardsHtml(core.cards)}<div class="core-stats"><div><span>support</span><strong>${fmt(core.support_runs)}</strong></div><div><span>observable</span><strong>${fmt(core.observable_runs)}</strong></div><div><span>coverage</span><strong>${pct(core.coverage)}</strong></div></div></button>`).join('');
    document.querySelectorAll('.core-item').forEach(button => button.addEventListener('click', () => selectCore(button.dataset.core)));
  };
  const signatureLabel = node => node.is_other ? 'OTHER（低频路线汇总）' : (node.cards || []).map(card => card.name || card.name_en).join(' + ') || '空构筑';
  const changeLine = (label, cards, ids, className) => {
    const names = (cards || []).map(card => card.name || card.name_en || card.cardId);
    const values = names.length ? names : (ids || []);
    return `<div class="change ${className}"><b>${label}</b>${esc(values.join('、') || '无')}</div>`;
  };
  const renderRoute = data => {
    const grouped = new Map();
    (data.nodes || []).forEach(node => { if (!grouped.has(node.day)) grouped.set(node.day, []); grouped.get(node.day).push(node); });
    const edgesByParent = new Map();
    (data.edges || []).forEach(edge => { const key = `${edge.parent_day}|${edge.parent_signature}`; if (!edgesByParent.has(key)) edgesByParent.set(key, []); edgesByParent.get(key).push(edge); });
    const signatureNames = new Map((data.nodes || []).map(node => [`${node.day}|${node.signature}`, signatureLabel(node)]));
    if (!data.nodes?.length) { $('route-content').className = 'route-empty'; $('route-content').textContent = '该核心暂无路线节点。'; return; }
    const columns = [...grouped.entries()].sort((a,b) => a[0]-b[0]).map(([day, nodes]) => `<section class="day-column"><div class="day-header">Day ${day} · ${nodes.length} 个主要节点</div>${nodes.map(node => {
      const outgoing = edgesByParent.get(`${node.day}|${node.signature}`) || [];
      return `<article class="route-node${node.is_other ? ' other' : ''}"><div class="node-title"><strong>${esc(signatureLabel(node))}</strong><span>${fmt(node.run_count)} 局</span></div>${node.is_other ? '<div class="edge-chip">OTHER：未达到主要路线展示阈值的低频构筑合并项</div>' : cardsHtml(node.cards)}<div class="node-rates"><div>起始占比<b>${pct(node.start_rate)}</b></div><div>当日样本占比<b>${pct(node.day_observable_rate)}</b></div></div><div class="changes">${changeLine('新增',node.added_cards,node.added,'added')}${changeLine('移除',node.removed_cards,node.removed,'removed')}${changeLine('保留',node.retained_cards,node.retained,'retained')}</div>${outgoing.length ? `<div class="edge-list"><span class="eyebrow">连接到下一 Day</span>${outgoing.map(edge => `<div class="edge-chip">→ ${esc(signatureNames.get(`${edge.child_day}|${edge.child_signature}`) || (edge.child_signature === '__OTHER__' ? 'OTHER' : '未知节点'))}<br><b>转型率 ${pct(edge.transition_rate)}</b> · ${fmt(edge.run_count)}/${fmt(edge.observable_runs)} 局</div>`).join('')}</div>` : ''}</article>`;
    }).join('')}</section>`).join('');
    $('route-content').className = 'route-scroll';
    $('route-content').innerHTML = `<div class="route-grid">${columns}</div>`;
  };
  const selectCore = async (coreId, requestToken = state.requestToken) => {
    state.routeController?.abort();
    const controller = new AbortController();
    state.routeController = controller;
    const routeToken = ++state.routeToken;
    state.selected = coreId; renderCores();
    const core = state.cores.find(item => item.core_id === coreId);
    $('route-title').textContent = core ? `#${core.rank} 核心 · 逐 Day 路线` : '逐 Day 发展路线';
    $('route-content').className = 'route-empty'; $('route-content').textContent = '正在加载路线…';
    try {
      const data = await api(`/api/day-stats/cores/${encodeURIComponent(coreId)}/route?version=${encodeURIComponent(state.version)}`, controller.signal);
      if (requestToken !== state.requestToken || routeToken !== state.routeToken || state.selected !== coreId) return;
      renderRoute(data);
    } catch (error) {
      if (error.name === 'AbortError' || requestToken !== state.requestToken || routeToken !== state.routeToken) return;
      $('route-content').className = 'route-empty'; $('route-content').textContent = `路线加载失败：${error.message}`;
    }
  };
  const loadCores = async () => {
    state.coresController?.abort(); state.routeController?.abort();
    const controller = new AbortController();
    state.coresController = controller;
    const requestToken = ++state.requestToken;
    status(`正在加载 ${$('hero-select').selectedOptions[0].text} Day ${state.day} 核心…`);
    state.selected = null; $('route-title').textContent = '逐 Day 发展路线'; $('route-content').className = 'route-empty'; $('route-content').textContent = '选择左侧核心组合，查看各 Day 主要节点与连接信息。';
    try {
      const data = await api(`/api/day-stats/cores?version=${encodeURIComponent(state.version)}&hero=${encodeURIComponent(state.hero)}&day=${state.day}`, controller.signal);
      if (requestToken !== state.requestToken) return;
      state.version = data.version_id; state.cores = data.cores || []; setMetadata(data); renderCores(); status();
      if (state.cores.length) selectCore(state.cores[0].core_id, requestToken);
    } catch (error) {
      if (error.name === 'AbortError' || requestToken !== state.requestToken) return;
      state.cores = []; renderCores(); status(`核心列表加载失败：${error.message}`, true);
    }
  };
  const init = async () => {
    $('hero-select').addEventListener('change', event => { state.hero = event.target.value; loadCores(); });
    document.querySelectorAll('.day-btn').forEach(button => button.addEventListener('click', () => { document.querySelectorAll('.day-btn').forEach(item => item.classList.toggle('active', item === button)); state.day = Number(button.dataset.day); loadCores(); }));
    try { const latest = await api('/api/day-stats/latest'); state.version = latest.version_id; setMetadata(latest); await loadCores(); }
    catch (error) { status(`发布版本加载失败：${error.message}`, true); }
  };
  init();
})();
