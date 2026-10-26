'use strict';

let _days = 7;
let _trendChart = null;
let _statsData = null;
let _statsRequest = 0;
let _statsController = null;
let _statsPeriod = 7;
let _adminPanel = 'overview';
let _trendView = 'daily';

const TAB_NAMES = {library:'图鉴构筑', runs:'阵容查询', winrate:'胜率查询', partner:'最佳搭档', topcard:'职业榜单', feedback:'意见反馈', stats:'数据看板'};
const API_NAMES = {
  '/api/comp/card':'阵容分析','/api/runs':'阵容查询','/api/winrate':'胜率查询','/api/partner':'最佳搭档',
  '/api/topcard':'职业榜单','/api/feedback':'意见反馈','/api/track/pv':'页面埋点','/api/ingest':'数据采集'
};
const MODULE_NAMES = {library:'图鉴构筑',
  runs:'阵容查询', winrate:'胜率查询', partner:'最佳搭档', topcard:'职业榜单',
  routes:'阵容路线', feedback:'意见反馈', trivia:'冷知识', support:'支持',
  stats:'数据看板'
};
const FEATURE_INFO = {
  runs_query:{name:'阵容查询 → 阵容列表', description:'选择“阵容列表”模式后点击“查询”；翻页也可能产生统计。'},
  comp_card_query:{name:'阵容查询 → 阵容分析', description:'选择 1–5 件物品进行阵容分析。此处为接口请求（含自动加载、排序）；主动查询与分享见上方专栏。'},
  winrate_query:{name:'胜率查询 → 10胜率查询', description:'在“10胜率查询”表单设置卡牌等条件后点击“查询”。'},
  partner_query:{name:'最佳搭档 → 最佳搭档查询', description:'在“最佳搭档查询”表单输入卡牌后点击“查询”。'},
  hero_overview:{name:'职业榜单 → 职业概况', description:'页面顶部的“职业概况”区域；首次打开自动加载，修改时间范围或段位也可能重新加载。'},
  topcard_query:{name:'职业榜单 → 卡牌榜', description:'职业选择区下方的“卡牌榜”标签；首次打开自动加载，切换职业或筛选条件也可能重新加载。'},
  comp_query:{name:'职业榜单 → 阵容榜', description:'职业选择区下方的“阵容榜”标签；切换职业或段位也可能重新加载。'},
  card_tier_query:{name:'职业榜单 → 卡牌 T 表', description:'职业选择区下方的“卡牌 T 表”标签；切换职业、时间范围或段位也可能重新加载。'},
  feedback_submit:{name:'意见反馈 → 提交反馈', description:'填写反馈内容后点击“提交反馈”。'},
  feedback_like:{name:'意见反馈 → 点赞', description:'点击某条反馈下方的“❤️”点赞按钮。'},
  feedback_comment:{name:'意见反馈 → 发送评论／回复', description:'在某条反馈的评论区发送评论或回复；仅展开评论区不计入。'},
  trivia_submit:{name:'冷知识 → 提交问题', description:'在“提个问题”表单填写问题后点击“提交问题”。'},
  trivia_vote:{name:'冷知识 → 赞同／不赞同', description:'点击某条冷知识下方的“👍”或“👎”，两种投票合并统计。'},
  trivia_share:{name:'冷知识 → 分享图片', description:'点击某条冷知识的“分享”，并成功生成分享图片。'},
  donation_qr_open:{name:'支持 → 查看赞赏码', description:'在“微信赞赏”区域点击“查看赞赏码”；表示打开二维码，不代表已付款。'},
  support_page_view:{name:'支持 → 页面访问', description:'打开“支持”页面时自动记录，没有独立操作按钮。', group:'auto'},
  routes_query:{name:'阵容路线 → 每天的主流阵容', description:'原页面加载某职业、某个 Day 的主流阵容。', group:'retired'},
  routes_summary:{name:'阵容路线 → Day 概览加载', description:'原页面打开或切换职业时自动加载各 Day 的概览。', group:'retired'},
  routes_latest:{name:'阵容路线 → 统计版本加载', description:'原页面打开或切换职业时自动获取统计版本。', group:'retired'},
  routes_node_detail:{name:'阵容路线 → 节点详情', description:'历史路线画布中，点击阵容节点打开详情。', group:'retired'},
  routes_expand:{name:'阵容路线 → 展开分支', description:'历史路线画布中，展开节点分支；选择起点时也可能自动展开。', group:'retired'},
  routes_cores:{name:'阵容路线 → 旧版核心列表', description:'更早版本的核心列表加载，后续页面已无对应入口。', group:'retired'},
  routes_detail:{name:'阵容路线 → 旧版核心路线详情', description:'更早版本的单个核心路线详情加载，不是“查看路线详情”按钮的点击量。', group:'retired'}
};



const statsElement = id => document.getElementById(id);
const number = value => Number(value || 0).toLocaleString('zh-CN');
function fmtN(n) { return number(n); }
function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
}
function featureInfo(feature) {
  return Object.hasOwn(FEATURE_INFO, feature) ? FEATURE_INFO[feature]
    : { name:feature, description:'尚未配置前台位置说明，请核对该统计项。' };
}
function activeFeatures(features) {
  return features.filter(f => !featureInfo(f.feature).group);
}
function renderFeatureUsage(features) {
  if (!features.length) return '<tr><td colspan="7" class="empty-cell">暂无功能事件数据</td></tr>';
  return features.map(f => {
    const info = featureInfo(f.feature);
    const rate = f.uses ? (f.successes / f.uses * 100).toFixed(1) : '0.0';
    return `<tr data-feature="${escapeHtml(f.feature)}"><td class="feature-info"><div class="feature-name">${escapeHtml(info.name)}</div><div class="feature-description">${escapeHtml(info.description)}</div></td><td>${number(f.uses)}</td><td>${number(f.uv)}</td><td class="feature-detail-col">${number(f.successes)}</td><td class="feature-detail-col">${number(f.empty_results)}</td><td class="feature-detail-col">${number(f.errors)}</td><td>${rate}%</td></tr>`;
  }).join('');
}
function renderFeatureGroups(features) {
  const groups = {active:[], auto:[], retired:[]};
  features.forEach(f => groups[featureInfo(f.feature).group || 'active'].push(f));
  statsElement('feature-usage-body').innerHTML = renderFeatureUsage(groups.active);
  statsElement('feature-auto-body').innerHTML = renderFeatureUsage(groups.auto);
  statsElement('feature-retired-body').innerHTML = renderFeatureUsage(groups.retired);
  statsElement('feature-auto-count').textContent = `${groups.auto.length} 项`;
  statsElement('feature-retired-count').textContent = `${groups.retired.length} 项`;
}
function resizeStatsCharts() {
  requestAnimationFrame(() => {
    Object.values(window.Chart?.instances || {}).forEach(chart => {
      if (chart.canvas && !chart.canvas.closest('[hidden]')) chart.resize();
    });
  });
}
function showAdminPanel(panel, updateHash = true) {
  if (!['overview','features','monitor','manage'].includes(panel)) panel = 'overview';
  _adminPanel = panel;
  document.querySelectorAll('[data-admin-tab]').forEach(button => {
    const selected = button.dataset.adminTab === panel;
    button.setAttribute('aria-selected', String(selected));
    button.tabIndex = selected ? 0 : -1;
  });
  document.querySelectorAll('[data-admin-panel]').forEach(section => { section.hidden = section.dataset.adminPanel !== panel; });
  if (updateHash) history.replaceState(null, '', '#' + panel);
  resizeStatsCharts();
  window.dispatchEvent(new CustomEvent('stats:panel-change', {detail:{panel,days:_statsPeriod}}));
}
function showTrendView(view) {
  _trendView = view;
  const hourly = view === 'hourly';
  statsElement('trend-hourly').hidden = !hourly;
  statsElement('trend-range').hidden = hourly;
  document.querySelectorAll('[data-trend-view]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.trendView === view)));
  statsElement('trend-scope').textContent = hourly ? '独立日期 · 北京时间' : _statsPeriod === 1 ? '今天' : `近 ${_statsPeriod} 天`;
  resizeStatsCharts();
}
function setDays(days) {
  _days = days;
  document.querySelectorAll('[data-days]').forEach(button => {
    const selected = Number(button.dataset.days) === days;
    button.classList.toggle('active', selected);
    button.setAttribute('aria-pressed',String(selected));
  });
  showTrendView(days === 1 ? 'hourly' : 'daily');
  // The single-day explorer retains its own explicitly labelled historical date.
  if (days === 1) statsElement('intraday-today').click();
  return loadStats();
}
async function loadStats() {
  const id = ++_statsRequest, days = _days;
  if (_statsController) _statsController.abort();
  _statsController = new AbortController();
  statsElement('main-content').hidden = false;
  statsElement('main-content').textContent = _statsData ? '正在更新，暂时显示上次加载的数据…' : '正在加载统计…';
  statsElement('main-content').className = 'dashboard-status';
  try {
    const response = await fetch('/api/stats/overview?days=' + days, { signal:_statsController.signal });
    if (!response.ok) throw new Error('请求失败');
    const data = await response.json();
    if (id !== _statsRequest) return;
    _statsPeriod = days;
    _statsData = data;
    renderStats(data);
    statsElement('main-content').hidden = true;
    statsElement('update-time').textContent = `最后更新：${new Date().toLocaleString('zh-CN')} · 近 ${days} 天 · 每 30 分钟自动刷新`;
    document.querySelectorAll('[data-period-label]').forEach(el => { el.textContent = days === 1 ? '今天' : `近 ${days} 天`; });
    statsElement('trend-scope').textContent = _trendView === 'hourly' ? '独立日期 · 北京时间' : days === 1 ? '今天' : `近 ${days} 天`;
    window.dispatchEvent(new CustomEvent('stats:period-change', {detail:{days}}));
  } catch (error) {
    if (id !== _statsRequest || error.name === 'AbortError') return;
    statsElement('main-content').className = 'error-msg';
    statsElement('main-content').textContent = '加载失败：' + error.message + (_statsData ? `；保留上次近 ${_statsPeriod} 天的数据，请重试。` : '，请刷新重试。');
  }
}
function kpi(label, value, note) {
  return `<div class="kpi-card"><div class="kpi-label">${label}</div><div class="kpi-value">${value}</div><div class="kpi-sub">${note}</div></div>`;
}
function renderChart(canvasId, previous, config) {
  const wrap = statsElement('trend-chart-wrap');
  try {
    if (previous) previous.destroy();
    wrap.innerHTML = `<canvas id="${canvasId}" role="img" aria-label="所选周期按天统计趋势，数据详见下方每日明细"></canvas>`;
    if (typeof window.Chart !== 'function') throw new Error('Chart.js 未加载');
    return new window.Chart(statsElement(canvasId).getContext('2d'), config);
  } catch (error) {
    console.warn('统计图表加载失败:', canvasId, error);
    wrap.innerHTML = '<div class="loading" role="status">图表暂不可用，请查看下方每日明细</div>';
    statsElement('daily-details').open = true;
    return null;
  }
}
function renderDailyTrend() {
  if (!_statsData) return;
  const daily = _statsData.daily || [], metric = statsElement('daily-metric').value;
  const usesByDay = new Map();
  (_statsData.feature_daily || []).forEach(row => {
    if (!featureInfo(row.feature).group) usesByDay.set(row.day, (usesByDay.get(row.day) || 0) + Number(row.count || 0));
  });
  const days = [...new Set([...daily.map(row => row.day), ...usesByDay.keys()])].sort();
  const byDay = new Map(daily.map(row => [row.day,row]));
  const values = key => days.map(day => Number(byDay.get(day)?.[key] || 0));
  const uses = days.map(day => usesByDay.get(day) || 0);
  const datasets = metric === 'uses'
    ? [{label:'当前功能记录',data:uses,backgroundColor:'#f59e0b88',borderColor:'#f59e0b',borderWidth:1}]
    : metric === 'uv' ? [{label:'API 访客估算',data:values('uv'),backgroundColor:'#60a5fa88',borderColor:'#60a5fa',borderWidth:1}]
    : [{label:'API 调用量',data:values('cnt'),backgroundColor:'#f59e0b88',borderColor:'#f59e0b',borderWidth:1,yAxisID:'y'},
       {label:'API 访客估算',data:values('uv'),type:'line',borderColor:'#60a5fa',backgroundColor:'transparent',borderWidth:2,pointRadius:3,yAxisID:'y1'}];
  const scales = {x:{ticks:{color:'#6b7280',font:{size:10}},grid:{display:false}},y:{beginAtZero:true,ticks:{color:'#6b7280',precision:0},grid:{color:'#1f2937'}}};
  if (metric === 'api_calls') scales.y1 = {beginAtZero:true,ticks:{color:'#60a5fa',precision:0},grid:{display:false},position:'right'};
  _trendChart = renderChart('trend-chart', _trendChart, {type:'bar',data:{labels:days.map(day => day.slice(5)),datasets},
    options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{labels:{color:'#9ca3af',font:{size:11}}}},scales}});
  statsElement('daily-hourly-body').innerHTML = days.length ? days.map((day,index) => `<tr><td>${escapeHtml(day)}</td><td>${number(values('cnt')[index])}</td><td>${number(values('uv')[index])}</td><td>${number(uses[index])}</td></tr>`).join('')
    : '<tr><td colspan="4" class="empty-cell">暂无每日数据</td></tr>';
}
function renderStats(data) {
  const totalCalls = Number(data.total_calls || 0), apiDist = data.api_dist || [];
  const errors = apiDist.reduce((sum,row) => sum + Number(row.errors || 0), 0);
  const errorRate = totalCalls > 0 ? (errors / totalCalls * 100).toFixed(1) : '—';
  // Keep the existing mean calculation until the separate metric-definition migration.
  const avgMs = apiDist.length ? Math.round(apiDist.reduce((sum,row) => sum + (row.avg_ms || 0),0) / apiDist.length) : 0;
  const features = data.feature_usage || [], active = activeFeatures(features);
  const coreUses = active.reduce((sum,row) => sum + Number(row.uses || 0),0);
  const pv = (data.tab_pv || []).reduce((sum,row) => sum + Number(row.cnt || 0),0);
  statsElement('overview-kpis').innerHTML = '<div class="kpi-grid">'
    + kpi('页面访问 PV', number(pv), '页面访问记录，可重复访问')
    + kpi('访客 UV · API 估算', number(data.total_uv), '现有接口日志口径')
    + kpi('当前功能记录', number(coreUses), '含首次自动加载，10 秒去重')
    + kpi('接口错误率', errorRate === '—' ? '—' : errorRate + '%', totalCalls ? `${number(errors)} 次失败 / ${number(totalCalls)} 次调用` : '本周期暂无接口记录') + '</div>';
  statsElement('monitor-kpis').innerHTML = '<div class="kpi-grid">'
    + kpi('API 调用量',number(totalCalls),'已记录接口，保留现有覆盖范围')
    + kpi('访客 UV · API 估算',number(data.total_uv),'接口日志中的匿名身份估算')
    + kpi('平均响应时间',apiDist.length ? number(avgMs) + ' ms' : '—','现有各接口平均值的均值')
    + kpi('接口失败数',number(errors),'含历史下线接口的失败请求') + '</div>';
  const featureErrors = active.reduce((sum,row) => sum + Number(row.errors || 0),0);
  const alerts = statsElement('overview-alerts');
  alerts.hidden = !(errors || featureErrors);
  alerts.innerHTML = (errors ? `<button type="button" onclick="showAdminPanel('monitor')">已记录接口有 ${number(errors)} 次失败，查看运行监控 →</button>` : '')
    + (featureErrors ? `<button type="button" onclick="showAdminPanel('features')">当前功能有 ${number(featureErrors)} 条错误记录（10 秒去重），查看功能分析 →</button>` : '');
  const ranked = [...active].sort((a,b) => b.uses-a.uses || a.feature.localeCompare(b.feature));
  statsElement('overview-top-body').innerHTML = ranked.length ? ranked.slice(0,5).map((row,index) => `<tr><td>${index+1}</td><td>${escapeHtml(featureInfo(row.feature).name)}</td><td>${number(row.uses)}</td><td>${number(row.uv)}</td></tr>`).join('')
    : '<tr><td colspan="4" class="empty-cell">暂无当前功能记录</td></tr>';
  renderFeatureGroups(features);
  const modules = data.module_usage || [], maxUses = Math.max(...modules.map(row => row.uses),1);
  statsElement('module-usage-list').innerHTML = modules.length ? modules.map(row => `<div class="tab-bar-item"><div class="tab-bar-name">${escapeHtml(MODULE_NAMES[row.module] || row.module)}${row.module === 'routes' ? '（已下线历史）' : ''}</div><div class="tab-bar-wrap"><div class="tab-bar-fill" style="width:${Math.round(row.uses/maxUses*100)}%"></div></div><div class="tab-bar-count">${number(row.uses)}</div></div>`).join('') : '<div class="empty-cell">暂无模块数据</div>';
  const pages = data.tab_pv || [], maxPv = Math.max(...pages.map(row => row.cnt),1);
  statsElement('tab-pv-list').innerHTML = pages.length ? pages.map(row => `<div class="tab-bar-item"><div class="tab-bar-name">${escapeHtml(MODULE_NAMES[row.tab] || row.tab)}</div><div class="tab-bar-wrap"><div class="tab-bar-fill" style="width:${Math.round(row.cnt/maxPv*100)}%"></div></div><div class="tab-bar-count">${number(row.cnt)}</div></div>`).join('') : '<div class="empty-cell">暂无页面访问记录</div>';
  statsElement('hot-cards-body').innerHTML = (data.hot_cards || []).length ? data.hot_cards.map((row,index) => `<tr><td>${index+1}</td><td>${escapeHtml(row.card || '-')}</td><td>${number(row.cnt)}</td></tr>`).join('') : '<tr><td colspan="3" class="empty-cell">暂无热门查询卡牌</td></tr>';
  statsElement('api-detail-body').innerHTML = apiDist.length ? apiDist.map(row => {
    const pct = row.cnt > 0 ? ((row.errors || 0)/row.cnt*100).toFixed(0) : 0;
    return `<tr><td class="endpoint">${escapeHtml(row.endpoint)}</td><td>${number(row.cnt)}</td><td>${number(Math.round(row.avg_ms || 0))} ms</td><td>${number(row.errors)}</td><td><span class="badge ${pct>10?'badge-red':pct>3?'badge-yellow':'badge-green'}">${pct>10?'异常':pct>3?'注意':'正常'}</span></td></tr>`;
  }).join('') : '<tr><td colspan="5" class="empty-cell">暂无接口记录</td></tr>';
  for (const key of ['submit','like','comment']) statsElement('feedback-'+key+'-count').textContent = number(data.fb_actions?.[key]);
  renderDailyTrend();
  resizeStatsCharts();
}

document.querySelectorAll('[data-admin-tab]').forEach(button => {
  button.addEventListener('click', () => showAdminPanel(button.dataset.adminTab));
  button.addEventListener('keydown', event => {
    const tabs = [...document.querySelectorAll('[data-admin-tab]')], index = tabs.indexOf(button);
    const next = event.key === 'ArrowRight' ? (index+1)%tabs.length : event.key === 'ArrowLeft' ? (index+tabs.length-1)%tabs.length : event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length-1 : null;
    if (next !== null) { event.preventDefault(); tabs[next].focus(); showAdminPanel(tabs[next].dataset.adminTab); }
  });
});
document.querySelectorAll('[data-trend-view]').forEach(button => button.addEventListener('click', () => showTrendView(button.dataset.trendView)));
statsElement('daily-metric').addEventListener('change',renderDailyTrend);
statsElement('feature-details-toggle').addEventListener('click',event => {
  const expanded = statsElement('feature-usage-section').classList.toggle('features-expanded');
  event.currentTarget.setAttribute('aria-pressed',String(expanded));
  event.currentTarget.textContent = expanded ? '收起说明与详细结果' : '说明与详细结果';
});
statsElement('dashboard-refresh').addEventListener('click',async event => {
  const button = event.currentTarget;
  button.disabled = true;
  try { await Promise.all([loadStats(),window.StatsIntraday?.refresh()]); }
  finally { button.disabled = false; }
});
window.addEventListener('hashchange', () => showAdminPanel(location.hash.slice(1),false));
showAdminPanel(location.hash.slice(1),false);
showTrendView('daily');
loadStats();
setInterval(() => { if (!document.hidden) loadStats(); },1800000);
