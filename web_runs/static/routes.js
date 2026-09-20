(() => {
  'use strict';

  const HERO_ZH = {Vanessa:'瓦内莎',Dooley:'杜利',Mak:'马克',Pygmalien:'皮格马利翁',Stelle:'斯黛尔',Jules:'朱尔斯',Karnok:'卡诺克','The Dragons':'双龙'};
  const STAGES = [
    {id:'early',label:'前期',min:1,max:3},
    {id:'middle',label:'中期',min:4,max:7},
    {id:'late',label:'后期',min:8,max:11},
    {id:'very-late',label:'大后期',min:12,max:99},
  ];
  const state = {version:'latest',hero:'Vanessa',day:3,summary:[],daily:null,selected:null,token:0,controller:null};
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  const pct = value => `${(Number(value || 0) * 100).toFixed(1)}%`;
  const fmt = value => Number(value || 0).toLocaleString('zh-CN');
  const stageFor = day => STAGES.find(stage => day >= stage.min && day <= stage.max) || STAGES[3];
  const cardName = card => card?.name || card?.name_en || card?.cardId || '未知卡牌';
  const safeImage = value => {
    if (!value) return '';
    try {
      const url = new URL(value, location.origin);
      const allowedHosts = new Set([location.host,'s.bazaardb.gg','cdn.bazaardb.gg','usercontent.bzdb.network']);
      const sameOrigin = url.origin === location.origin;
      if (url.protocol !== 'https:' && !sameOrigin) return '';
      if (!sameOrigin && !allowedHosts.has(url.host)) return '';
      return url.href;
    } catch (_) { return ''; }
  };
  const api = async (path, signal) => {
    const response = await fetch(path,{headers:{Accept:'application/json'},signal});
    if (!response.ok) {
      let message=`请求失败（${response.status}）`;
      try { message=(await response.json()).error || message; } catch (_) {}
      throw new Error(message);
    }
    return response.json();
  };
  const setStatus = (message='',error=false) => {
    const el=$('status'); el.textContent=message;
    el.className=`status${message?' visible':''}${error?' error':''}`;
  };
  const setMetadata = data => {
    $('meta-version').textContent=data.version_id || '—';
    $('meta-cutoff').textContent=data.source_cutoff ? new Date(data.source_cutoff).toLocaleString('zh-CN',{hour12:false}) : '—';
    const counts=data.sample_counts || data;
    $('meta-runs').textContent=fmt(counts.source_success_run_count);
    $('meta-snapshots').textContent=fmt(counts.source_snapshot_count);
    $('meta-finals').textContent=fmt(counts.final_composition_count);
  };
  const cardHtml = card => {
    const src=safeImage(card?.img); const name=cardName(card);
    const size=['Small','Medium','Large'].includes(card?.size) ? card.size : 'Small';
    return `<div class="route-card size-${size}" title="${esc(name)}"><div class="route-card-art">${src?`<img src="${esc(src)}" alt="${esc(name)}" loading="lazy" referrerpolicy="no-referrer">`:`<span>${esc(name)}</span>`}</div><small>${esc(name)}</small></div>`;
  };
  const cardsHtml = cards => `<div class="route-cards">${(cards||[]).map(cardHtml).join('') || '<span class="no-card">暂无卡牌信息</span>'}</div>`;
  const runsLink = cards => {
    const names=(cards||[]).map(cardName).filter(Boolean).join('+');
    const params=new URLSearchParams({hero:HERO_ZH[state.hero]||state.hero,cards:names,rank:'legendary',auto:'1'});
    return `<a class="runs-link" href="/runs?${esc(params.toString())}">查询相似真实阵容 →</a>`;
  };
  const readUrl = () => {
    const params=new URLSearchParams(location.search);
    const hero=params.get('hero'); const day=Number(params.get('day'));
    if (hero && Object.hasOwn(HERO_ZH,hero)) state.hero=hero;
    if (Number.isInteger(day) && day>=1 && day<=16) state.day=day;
    state.selected=params.get('node') || null;
    $('hero-select').value=state.hero;
  };
  const updateUrl = () => {
    const params=new URLSearchParams({hero:state.hero,day:String(state.day)});
    if (state.selected) params.set('node',state.selected);
    history.replaceState(null,'',`/routes?${params}`);
  };
  const renderDayTabs = () => {
    const days=[...new Set(state.summary.map(node=>Number(node.day)))].sort((a,b)=>a-b);
    const grouped=STAGES.map(stage=>{
      const stageDays=days.filter(day=>day>=stage.min&&day<=stage.max);
      if (!stageDays.length) return '';
      return `<div class="day-group ${stage.id}"><span>${stage.label}</span><div>${stageDays.map(day=>`<button class="day-tab${day===state.day?' active':''}" data-day="${day}" role="tab" aria-selected="${day===state.day}">Day ${day}</button>`).join('')}</div></div>`;
    }).join('');
    $('day-tabs').innerHTML=grouped || '<div class="empty-state">当前职业暂无逐日统计。</div>';
    document.querySelectorAll('.day-tab').forEach(button=>button.addEventListener('click',()=>{
      state.day=Number(button.dataset.day); state.selected=null; updateUrl(); renderDayTabs(); loadDay();
    }));
  };
  const nodeTitle = node => (node.cards||[]).map(cardName).join(' + ') || `Day ${state.day} 阵容 #${node.rank}`;
  const renderArchetypes = () => {
    const nodes=state.daily?.nodes || [];
    const stage=stageFor(state.day);
    $('stage-label').textContent=stage.label;
    $('stage-label').className=`stage-badge ${stage.id}`;
    $('day-title').textContent=`Day ${state.day} 主流阵容`;
    $('archetype-count').textContent=nodes.length;
    if (!nodes.length) {
      $('archetype-list').innerHTML='<div class="empty-state">当天没有满足展示条件的阵容。</div>';
      return;
    }
    $('archetype-list').innerHTML=nodes.map(node=>`<button class="archetype-item${node.node_id===state.selected?' active':''}" data-node="${esc(node.node_id)}"><div class="archetype-top"><b>#${fmt(node.rank)} ${esc(nodeTitle(node))}</b><span>${pct(node.day_share)}</span></div>${cardsHtml(node.cards)}<div class="sample-row"><span>${fmt(node.run_count)} 局</span><span>核心共现 ${fmt(node.core_support_runs)} 局 · ${pct(node.core_support_rate)}</span><span>当天样本 ${fmt(node.day_observable_runs)}</span></div></button>`).join('');
    document.querySelectorAll('.archetype-item').forEach(button=>button.addEventListener('click',()=>selectNode(button.dataset.node)));
  };
  const renderChanges = (parent,child) => {
    const parentIds=new Set(parent?.core_items||[]); const childIds=new Set(child?.core_items||[]);
    const parentCards=new Map((parent?.cards||[]).map(card=>[card.cardId,card]));
    const childCards=new Map((child?.cards||[]).map(card=>[card.cardId,card]));
    const retained=[...parentIds].filter(id=>childIds.has(id));
    const added=[...childIds].filter(id=>!parentIds.has(id));
    const removed=[...parentIds].filter(id=>!childIds.has(id));
    const tags=[
      ...retained.map(id=>`<span class="change-tag retained_core">保留核心 · ${esc(cardName(childCards.get(id)||parentCards.get(id)))}</span>`),
      ...added.map(id=>`<span class="change-tag added_core">新增核心 · ${esc(cardName(childCards.get(id)))}</span>`),
      ...removed.map(id=>`<span class="change-tag removed_core">退出核心 · ${esc(cardName(parentCards.get(id)))}</span>`),
    ];
    return tags.length
      ? `<div class="change-summary">${tags.join('')}</div>`
      : '<div class="change-summary muted">核心标签未发生变化。</div>';
  };
  const selectNode = nodeId => {
    state.selected=nodeId; updateUrl(); renderArchetypes(); renderEvolution();
  };
  const renderEvolution = () => {
    const node=(state.daily?.nodes||[]).find(item=>item.node_id===state.selected);
    if (!node) {
      $('evolution-title').textContent='选择一套阵容查看演变';
      $('evolution-content').className='empty-state';
      $('evolution-content').innerHTML='<strong>从左侧选择一套主流阵容</strong><span>这里会显示它下一步最常演变成什么。</span>';
      return;
    }
    $('evolution-title').textContent=`Day ${state.day} · ${nodeTitle(node)}`;
    const directions=state.daily?.directions?.[node.node_id] || [];
    if (!directions.length) {
      $('evolution-content').className='empty-state';
      $('evolution-content').innerHTML='<strong>暂无达到门槛的路线方向</strong><span>低样本路线仍计入可观测分母，但不会展示。</span>';
      return;
    }
    const coverage=directions[0]?.coverage_rate || 0;
    const coverageNote=coverage>=.7?'路线较集中，参考价值较高':coverage>=.5?'存在多种发展方向':'构筑较为分散，以下仅供参考';
    const labels=['主流路线','第二路线','第三路线'];
    $('evolution-content').className='evolution-list';
    $('evolution-content').innerHTML=`<div class="selected-origin"><div><span>当前阵容</span><strong>${esc(nodeTitle(node))}</strong></div>${cardsHtml(node.cards)}<div class="origin-stats"><b>${fmt(node.run_count)} 局</b><span>主要方向覆盖 ${pct(coverage)} · ${esc(coverageNote)}</span></div></div><div class="flow-arrow">向后发展 ↓</div>${directions.map((direction,index)=>{
      const dayText=(direction.arrival_days||[]).map(row=>`Day ${row.day} ${pct(row.rate)}`).join(' · ');
      const associated=(direction.associated_cards||[]).map(row=>`<li>${cardHtml(row.display)}<span><b>${pct(row.support_rate)}</b><small>${fmt(row.support_runs)} / ${fmt(direction.run_count)} 局</small></span></li>`).join('') || '<li class="muted">暂无达到门槛的非核心搭配牌</li>';
      const variants=(direction.variants||[]).slice(0,5).map(row=>`<li><div><b>${esc((row.target_cards||[]).map(cardName).join(' + ')||'目标核心')}</b><span>Day ${fmt(row.child_day)} · ${fmt(row.run_count)} 局 · ${pct(row.direction_share)}</span></div>${cardsHtml(row.target_cards)}</li>`).join('');
      const detailId=`route-detail-${index}`;
      const proof=direction.representative_run_id?`证据 ${direction.representative_run_id.slice(0,8)}`:'证据编号不可用';
      return `<article class="branch-card${index===0?' primary':''}"><div class="branch-rank"><span>${labels[index]}</span><b>${pct(direction.transition_rate)}</b></div><div class="branch-head"><div><small>${esc(dayText||'下一次实际观测')}</small><h3>${esc((direction.target_cards||[]).map(cardName).join(' + ')||'目标核心')}</h3></div><div class="branch-sample"><b>${fmt(direction.run_count)} 局</b><span>/ ${fmt(direction.parent_observable_runs)} 可观测局</span></div></div>${cardsHtml(direction.target_cards)}${renderChanges(node,{core_items:direction.target_core_items,cards:direction.target_cards})}<button class="detail-toggle" type="button" data-detail="${detailId}">查看路线详情</button><div id="${detailId}" class="route-detail" hidden><section><h4>真实代表阵容</h4><p>Day ${fmt(direction.representative_day)} · ${esc(proof)}</p>${cardsHtml(direction.representative_cards)}${runsLink(direction.representative_cards)}</section><section><h4>关联牌推荐</h4><ol class="association-list">${associated}</ol></section><section><h4>常见变体</h4><ul class="variant-list">${variants}</ul></section><section><h4>到达 Day 分布</h4><p>${esc(dayText)}</p><small>展示门槛：${fmt(direction.display_threshold)} 局；低样本路线已隐藏但仍计入分母。</small></section></div></article>`;
    }).join('')}`;
    document.querySelectorAll('.detail-toggle').forEach(button=>button.addEventListener('click',()=>{const panel=$(button.dataset.detail);const open=panel.hidden;panel.hidden=!open;button.textContent=open?'收起路线详情':'查看路线详情';}));
  };
  const loadDay = async () => {
    state.controller?.abort(); const controller=new AbortController(); state.controller=controller;
    const token=++state.token; setStatus(`正在加载 ${HERO_ZH[state.hero]} Day ${state.day} 的阵容演变…`);
    try {
      const data=await api(`/api/routes/daily?version=${encodeURIComponent(state.version)}&hero=${encodeURIComponent(state.hero)}&day=${state.day}`,controller.signal);
      if (token!==state.token) return;
      state.version=data.version_id; state.daily=data;
      state.selected=(data.nodes||[]).some(node=>node.node_id===state.selected) ? state.selected : (data.nodes?.[0]?.node_id || null);
      setMetadata(data); renderArchetypes(); renderEvolution(); renderDayTabs(); updateUrl(); setStatus();
    } catch (error) {
      if (error.name==='AbortError'||token!==state.token) return;
      state.daily={nodes:[],edges:[]}; renderArchetypes(); renderEvolution(); setStatus(`逐日路线加载失败：${error.message}`,true);
    }
  };
  const loadHero = async () => {
    state.controller?.abort(); const controller=new AbortController(); state.controller=controller;
    const token=++state.token; setStatus(`正在加载 ${HERO_ZH[state.hero]} 的逐日统计…`);
    try {
      const latest=await api('/api/routes/latest',controller.signal);
      if (token!==state.token) return;
      state.version=latest.version_id; setMetadata(latest);
      const summary=await api(`/api/routes/daily/summary?version=${encodeURIComponent(state.version)}&hero=${encodeURIComponent(state.hero)}`,controller.signal);
      if (token!==state.token) return;
      state.summary=summary.nodes||[];
      const days=[...new Set(state.summary.map(node=>Number(node.day)))].sort((a,b)=>a-b);
      if (!days.includes(state.day)) state.day=days.includes(3)?3:(days[0]||3);
      renderDayTabs(); await loadDay();
    } catch (error) {
      if (error.name==='AbortError'||token!==state.token) return;
      setStatus(`统计加载失败：${error.message}`,true);
    }
  };
  const init = () => {
    readUrl();
    $('hero-select').addEventListener('change',event=>{state.hero=event.target.value;state.version='latest';state.selected=null;state.summary=[];loadHero();});
    $('details-toggle').addEventListener('click',()=>{const panel=$('details-panel');const open=panel.hidden;panel.hidden=!open;$('details-toggle').setAttribute('aria-expanded',String(open));});
    loadHero();
  };
  init();
})();
