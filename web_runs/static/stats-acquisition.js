(() => {
  'use strict';
  const panel = document.getElementById('acquisition-panel');
  if (!panel) return;
  const el = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const names = {direct:'直接访问／来源未知',bilibili:'B站',qq:'QQ',wechat:'微信',player_share:'玩家分享',google:'Google',bing:'必应',baidu:'百度',referral:'其他外部网站'};
  let days = 7, serial = 0, controller;
  async function load() {
    const requestId = ++serial;
    controller?.abort(); controller = new AbortController();
    el('acquisition-status').textContent = '正在加载访问来源…';
    try {
      const response = await fetch('/api/stats/acquisition?days='+days, {signal:controller.signal});
      if (!response.ok) throw new Error('请求失败');
      const data = await response.json();
      if (requestId !== serial) return;
      const s = data.summary;
      el('acquisition-summary').textContent = `近 ${days} 天：${s.uv} 个匿名设备 · ${s.pv} 次浏览 · ${s.query_uv} 个设备主动查询 · ${s.queries} 次查询`;
      el('acquisition-status').textContent = data.rows.length ? `开始记录：${data.tracking_since?.slice(0,10) || '—'}（UTC）。历史来源无法补录；未带标记且没有来源信息的访问归入“直接访问／来源未知”。` : '来源统计已就绪，尚未收到访问记录。历史流量不会自动补录。';
      el('acquisition-body').innerHTML = data.rows.length ? data.rows.map(r => `<tr><td>${esc(names[r.source] || r.source)}<small style="display:block">${esc(r.medium)}</small></td><td>${esc(r.campaign || '—')}<small style="display:block">${esc(r.content || '—')}</small></td><td>${esc(r.landing)}</td><td>${r.uv}</td><td>${r.pv}</td><td>${r.query_uv}</td><td>${r.query_rate === null ? '—' : (r.query_rate*100).toFixed(1)+'%'}</td></tr>`).join('') : '<tr><td colspan="7">暂无来源数据</td></tr>';
    } catch (error) {
      if (requestId !== serial || error.name === 'AbortError') return;
      el('acquisition-status').textContent = '来源统计加载失败，请重试。';
    }
  }
  el('acquisition-refresh').addEventListener('click', load);
  window.addEventListener('stats:period-change', event => { days = event.detail.days; load(); });
  const form = el('campaign-link-form');
  function generate(event) {
    event?.preventDefault();
    const fields = new FormData(form), output = el('campaign-link-output');
    try {
      const url = new URL(fields.get('landing'), 'https://bazaarqiubot.com');
      if (url.origin !== 'https://bazaarqiubot.com' || !/^\/(builds(?:\/(vanessa|dooley|pygmalien|mak|stelle|jules|karnok|dragons))?|trivia(?:\/[1-9][0-9]*)?|runs|topcard|winrate|partner)?$/.test(url.pathname)) throw new Error();
      for (const key of ['source','medium','campaign','content']) {
        const value = String(fields.get(key) || '').trim().toLowerCase();
        if (value && !/^[a-z0-9_-]{1,64}$/.test(value)) throw new Error();
        if (value) url.searchParams.set('utm_'+key, value);
      }
      output.value = url.href;
      el('campaign-link-hint').textContent = '复制此链接到对应视频或社群；制作二维码时使用同一链接。';
    } catch (_) { output.value = ''; el('campaign-link-hint').textContent = '请使用本站内容路径，标记只支持 64 位以内的英文字母、数字、下划线和连字符。'; }
  }
  form.addEventListener('submit', generate);
  el('campaign-link-copy').addEventListener('click', async () => {
    generate();
    if (!el('campaign-link-output').value) return;
    try { await navigator.clipboard.writeText(el('campaign-link-output').value); el('campaign-link-hint').textContent = '已复制渠道链接'; }
    catch (_) { el('campaign-link-output').select(); el('campaign-link-hint').textContent = '请复制已选中的链接'; }
  });
  generate(); load();
})();
