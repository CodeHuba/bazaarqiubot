(() => {
  'use strict';
  const el = id => document.getElementById(id);
  if (!el('search-ranking-body')) return;
  const escape = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  const num = value => Number(value || 0).toLocaleString('zh-CN');
  const rate = value => value == null ? '—' : Number(value).toFixed(1) + '%';
  const features = {runs:'阵容列表',comp_card:'阵容分析',winrate:'胜率查询',partner:'搭配查询'};
  const outcomes = {success:'有结果',partial:'部分有结果',empty:'无匹配',insufficient:'样本不足',unrecognized:'卡牌未识别',error:'查询异常',unverified:'结果未验证'};
  const sources = {network:'网络响应（含服务端缓存）',browser_cache:'浏览器缓存',unknown:'来源未确定'};
  let days = typeof _statsPeriod === 'number' ? _statsPeriod : 7;
  let offset = 0, total = 0, requestId = 0, detailId = 0, loaded = false;
  let controller, detailController;
  const visible = () => !el('panel-features').hidden;
  function parameters() {
    const params = new URLSearchParams({days,feature:el('search-feature').value,board:el('search-board').value,offset,limit:20});
    for (const name of ['hero','rank','day','enchantment']) {
      const value = el('search-' + name).value;
      if (value) params.set(name,value);
    }
    if (el('search-combos').checked) params.set('combos','1');
    return params;
  }
  function clearDetail() {
    detailId++;
    detailController?.abort();
    el('search-detail').hidden = true;
    el('search-detail').replaceChildren();
  }
  async function load() {
    if (!visible()) { loaded = false; return; }
    const id = ++requestId;
    controller?.abort();
    controller = new AbortController();
    clearDetail();
    el('search-status').textContent = '正在加载搜索榜单…';
    el('search-prev').disabled = el('search-next').disabled = true;
    el('search-ranking-body').replaceChildren();
    el('search-page-info').textContent = '';
    const params = parameters();
    try {
      const response = await fetch('/api/stats/search?' + params,{signal:controller.signal});
      if (!response.ok) throw new Error(response.status === 401 ? '登录已失效，请刷新页面' : '暂时无法读取搜索统计');
      const data = await response.json();
      if (id !== requestId) return;
      if (data.version !== 2 || !Array.isArray(data.rows)) throw new Error('搜索统计暂未就绪');
      loaded = true;
      total = data.total;
      el('search-item-heading').textContent = params.get('board') === 'cards' ? '卡牌（点击查看详情）' : '完整条件（点击查看详情）';
      el('search-ranking-body').innerHTML = data.rows.length ? data.rows.map((row,index) => `<tr><td>${offset+index+1}</td><td><button type="button" class="search-item-button" data-search-item="${escape(row.key)}">${escape(row.label)}</button></td><td>${num(row.searches)}</td><td>${num(row.visitors)}</td><td>${rate(row.empty_rate)}</td></tr>`).join('') : '<tr><td colspan="5" class="empty-cell">暂无符合条件的主动搜索记录</td></tr>';
      el('search-page-info').textContent = total ? `第 ${offset+1}–${Math.min(offset+20,total)} 项，共 ${num(total)} 项` : '共 0 项';
      el('search-prev').disabled = offset === 0;
      el('search-next').disabled = offset+20 >= total;
      const since = new Date(data.tracking_since);
      el('search-coverage').textContent = `新口径采集起点：${Number.isNaN(since.getTime()) ? '尚未开始' : since.toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})+'（北京时间）'}。${data.summary ? `当前筛选共 ${num(data.summary.searches)} 次主动搜索、${num(data.summary.visitors)} 位独立访客。` : ''}当前不展示跨口径增长率。`;
      el('search-status').textContent = params.get('board') === 'empty' ? '仅展示至少 5 次已完成查询的条件，按无匹配与样本不足次数排序。' : '';
      el('search-ranking-body').querySelectorAll('[data-search-item]').forEach(button => button.addEventListener('click',() => showDetail(button.dataset.searchItem,params)));
    } catch (error) {
      if (id !== requestId || error.name === 'AbortError') return;
      loaded = false;
      el('search-status').textContent = '加载失败：' + error.message + '。可点击“刷新榜单”重试。';
    }
  }
  function list(entries) {
    return '<ul>' + entries.map(([label,value]) => `<li><span>${escape(label)}</span><strong>${num(value)}</strong></li>`).join('') + '</ul>';
  }
  async function showDetail(key, baseParams) {
    const id = ++detailId;
    detailController?.abort();
    detailController = new AbortController();
    const box = el('search-detail');
    box.hidden = false;
    box.textContent = '正在加载搜索详情…';
    const params = new URLSearchParams(baseParams);
    params.set('item', key);
    try {
      const response = await fetch('/api/stats/search?' + params,{signal:detailController.signal});
      if (!response.ok) throw new Error('详情读取失败');
      const data = await response.json(), row = data.detail;
      if (id !== detailId) return;
      if (!row) throw new Error('该项已不在当前统计范围，请刷新榜单');
      box.innerHTML = `<div class="section-heading"><h3 id="search-detail-title">${escape(row.label)}</h3><button type="button" class="admin-button" id="search-detail-close">收起详情</button></div><p class="panel-note">${num(row.searches)} 次搜索 · ${num(row.visitors)} 位独立访客 · 空／样本不足率 ${rate(row.empty_rate)}</p>${row.result_counts ? `<p class="panel-note">已验证查询返回样本数范围：${num(row.result_counts.min)}–${num(row.result_counts.max)}。完整条件中的多卡比较按各组相加，样本可能重叠；卡牌详情取各组最大值。</p>` : ''}<div class="search-detail-grid"><div><h4>结果分类</h4>${list(Object.entries(outcomes).map(([key,label]) => [label,row.outcomes[key]]))}</div><div><h4>搜索入口</h4>${list(Object.entries(features).map(([key,label]) => [label,row.features[key]]))}<h4>响应来源</h4>${list(Object.entries(sources).map(([key,label]) => [label,row.sources[key]]))}</div><div><h4>每日搜索次数（北京时间）</h4>${list(row.daily.map(day => [day.date,day.count]))}</div><div><h4>常见完整条件 · Top 10</h4>${list(row.conditions.map(condition => [condition.label,condition.count]))}</div></div>`;
      el('search-detail-close').addEventListener('click', clearDetail);
    } catch (error) {
      if (id !== detailId || error.name === 'AbortError') return;
      box.textContent = error.message;
    }
  }
  const reset = () => { offset = 0; loaded = false; void load(); };
  for (const name of ['feature','board','hero','rank','day','enchantment','combos']) el('search-'+name).addEventListener('change', reset);
  el('search-refresh').addEventListener('click',reset);
  el('search-prev').addEventListener('click',() => {offset = Math.max(0,offset-20); void load();});
  el('search-next').addEventListener('click',() => {if(offset+20 < total) {offset+=20; void load();}});
  window.addEventListener('stats:period-change', event => {days=event.detail.days; reset();});
  window.addEventListener('stats:panel-change', event => {
    days = event.detail.days;
    if (event.detail.panel === 'features' && !loaded) void load();
  });
  if (visible()) void load();
})();
