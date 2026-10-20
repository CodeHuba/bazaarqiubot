/* Composition analysis inside the existing /runs page. */
(() => {
  'use strict';
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const pct = n => (100 * (n || 0)).toFixed(1) + '%';
  const num = n => Number(n || 0).toLocaleString('zh-CN');
  const tiers = {Bronze:'青铜',Silver:'白银',Gold:'黄金',Diamond:'钻石',Legendary:'传奇'};
  const heroes = {Vanessa:'瓦内莎',Dooley:'杜利',Mak:'马克',Pygmalien:'皮格马利翁',Stelle:'斯黛尔',Jules:'朱尔斯',Karnok:'卡诺克','The Dragons':'双龙'};
  function track(action, group = '', detail = '', duration_ms) {
    try {
      const uuid = () => crypto.randomUUID?.() || 'comp-'+Date.now().toString(36)+'-'+Math.random().toString(36).slice(2);
      let fingerprint;
      try { fingerprint=localStorage.getItem('_bz_fp');if(!fingerprint){fingerprint=uuid();localStorage.setItem('_bz_fp',fingerprint);} } catch (_) {}
      fingerprint ||= window._compFingerprint ||= uuid();
      const body=JSON.stringify({id:uuid(),action,group,detail,...(duration_ms==null?{}:{duration_ms:Math.min(3600000,Math.max(0,Math.round(duration_ms)))})});
      const send = async retry => {
        try {
          const response=await fetch('/api/track/comp',{method:'POST',headers:{'Content-Type':'application/json','X-Fingerprint':fingerprint},body,keepalive:true});
          if(response.status>=500 && retry)setTimeout(()=>void send(false),1000);
        } catch (_) {if(retry)setTimeout(()=>void send(false),1000);}
      };
      void send(true);
    } catch (_) { /* Tracking never blocks querying or sharing. */ }
  }
  let controller, serial = 0, anchors = new Set();
  let mode = new URLSearchParams(location.search).get('sort') || 'reliable';
  if (!['reliable','rate','appearance'].includes(mode)) mode = 'reliable';
  const safeImage = value => {
    try { const url = new URL(value, location.origin); return value && ['http:','https:'].includes(url.protocol) ? url.href : ''; }
    catch (_) { return ''; }
  };
  function cancel() { serial += 1; controller?.abort(); controller = null; }
  function card(c, detail = '', actual = false) {
    const image = safeImage(c.img), tier = c.isLegendary === true ? 'Legendary' : c.tier;
    const quality = Object.hasOwn(tiers, tier || '') ? tier : 'Unknown';
    const enchantment = c.enchantment ? (typeof RUN_ENCHANTMENTS !== 'undefined' && RUN_ENCHANTMENTS[c.enchantment] || c.enchantment) : '';
    const units = {Small:1,Medium:2,Large:3}[c.size] || 1;
    const art = `${image?`<img class="comp-card-img size-${esc(c.type==='Skill'?'Skill':c.size||'Small')}" src="${esc(image)}" alt="" loading="lazy" onerror="this.style.display='none';this.nextElementSibling.hidden=false">`:''}<span class="comp-art-missing"${image?' hidden':''} aria-label="原始卡图暂缺">暂无<br>卡图</span>`;
    const framedArt = actual ? `<span class="comp-instance-art run-art tier-${quality.toLowerCase()}${c.type==='Skill'?' is-skill':''}" style="--art-height:32px;--units:${units}">${art}</span>` : art;
    return `<span class="comp-card-chip has-img bcp-trigger${anchors.has(c.cardId)?' comp-anchor':''}${actual?' comp-instance':''}" ${window.BazaarCardPreview?.attrs(c)||''}>${framedArt}<span>${esc(c.name)}${detail?`<small>${detail}</small>`:''}${actual&&enchantment?`<small>${esc(enchantment)}</small>`:''}</span></span>`;
  }
  function companions(rows = [], winning = false) {
    return rows.length ? `<div class="comp-rep-cards">${rows.slice(0,8).map(c=>card(c,`${pct(winning?c.ten_win_frequency:c.frequency)} · ${num(winning?c.ten_wins:c.count)} 局`)).join('')}</div>` : '<p class="comp-note">暂无搭配数据</p>';
  }
  function examples(rows = []) {
    return rows.filter(row => row.wins === 10).map((row, index) => {
      let screenshot = row.screenshot || '';
      if (screenshot.startsWith('/') && !screenshot.startsWith('//')) screenshot = 'https://usercontent.bzdb.network' + screenshot;
      screenshot = safeImage(screenshot);
      return `<article class="comp-example"><div class="comp-example-head"><b>实战样例 ${index+1}</b><span>${esc(heroes[row.hero]||row.hero)} · ${row.wins} 胜</span>${exampleFrequency(row)}<span class="comp-note">${esc(row.created_at?.slice(0,10)||'')}</span></div><div class="comp-rep-cards">${(row.items||[]).map(c=>card(c,'',true)).join('')}</div><details><summary>查看本局全部技能（${row.skills?.length||0}）</summary><div class="comp-rep-cards">${(row.skills||[]).map(c=>card(c,'',true)).join('')}</div></details><div class="comp-detail-actions"><a class="comp-link-btn" href="/runs?run=${encodeURIComponent(row.id)}" target="_blank" rel="noopener">打开本局阵容</a><a class="comp-link-btn" href="https://bazaardb.gg/run/tracker/${encodeURIComponent(row.id)}" target="_blank" rel="noopener">查看原始对局 ↗</a></div>${screenshot?`<details><summary>查看本局截图</summary><img class="comp-example-screenshot" src="${esc(screenshot)}" alt="本局对局截图" loading="lazy"></details>`:''}</article>`;
    }).join('');
  }
  function exampleFrequency(row) {
    return Number.isInteger(row.similar_ten_wins) && row.similar_ten_wins > 0 ? `<span class="comp-example-frequency">相近阵容：${num(row.similar_ten_wins)} 局 10 胜</span>` : '';
  }
  function groupLink(data, row, sort) {
    const url = new URL('/runs', location.origin);
    url.search = new URLSearchParams({mode:'comp',cards:data.anchors.map(c=>c.name).join('+'),
      rank:data.scope.rank,sort,auto:'1',group:row.id});
    if (data.scope.hero) url.searchParams.set('hero',heroes[data.scope.hero]||data.scope.hero);
    if (data.scope.days) url.searchParams.set('days',data.scope.days);
    return window.BazaarAcquisition?.shareUrl(url.href,'comp') || url.href;
  }
  function shareGroup(data, row, sort) {
    const link = groupLink(data,row,sort);
    const seen = new Set();
    const cases = (row.examples||[]).filter(example=>{
      if (example.wins!==10 || seen.has(example.id)) return false;
      seen.add(example.id);return true;
    }).slice(0,3);
    window.BazaarRuns.openShareCard({
      title:'分享核心组合',link,linkLabel:'复制组合链接',filename:`bazaarqiubot-comp-${row.id.replace(/[^A-Za-z0-9_-]/g,'-')}.png`,
      description:`分享图包含核心组合、表现数据、10 胜搭配和 ${cases.length} 套真实 10 胜阵容案例。`,
      sheetClass:'comp-share-sheet',
      onEvent:(action,detail,duration)=>track(action,row.id,detail,duration),
      renderSheet:(qr,shareImage)=>{
        const shareCard = (c, detail='', actual=false) => {
          const image = shareImage(c.img);
          const width = c.type==='Skill'?44:({Small:22,Medium:44,Large:66}[c.size]||22);
          const tier=c.isLegendary===true?'Legendary':c.tier;
          const quality=Object.hasOwn(tiers,tier||'')?tier.toLowerCase():'unknown';
          return `<div class="${actual?'comp-share-case-card':'comp-share-card'}"><span class="comp-share-art${actual?' run-art tier-'+quality:''}${c.type==='Skill'?' is-skill':''}" style="width:${width}px;--art-height:44px">${image?`<img data-share-src="${esc(image)}" alt="">`:''}<span class="run-art-missing">暂无<br>卡图</span></span><span class="comp-share-name">${esc(c.name)}${detail?`<small>${esc(detail)}</small>`:''}</span></div>`;
        };
        const list = rows => (rows||[]).slice(0,8).map(c=>shareCard(c,`${pct(c.ten_win_frequency)} · ${num(c.ten_wins)} 局`)).join('') || '<p class="run-data-note">暂无搭配数据</p>';
        const caseCard = (c,type) => shareCard({...c,type},c.enchantment?(c.enchantment_zh||(typeof RUN_ENCHANTMENTS!=='undefined'&&RUN_ENCHANTMENTS[c.enchantment])||c.enchantment):'',true);
        const exampleMarkup = cases.map((example,index)=>{
          const created=new Date(example.created_at||NaN);
          const date=Number.isNaN(created.getTime())?'':created.toLocaleDateString('zh-CN',{timeZone:'Asia/Shanghai'});
          return `<article class="comp-share-example" data-example-id="${esc(example.id)}"><div class="comp-share-example-head"><b>案例 ${index+1} · ${esc(heroes[example.hero]||example.hero)} · 10 胜</b>${exampleFrequency(example)}<span>${esc(date)}</span></div><div class="comp-share-case-label">物品 · ${example.items?.length||0} 张</div><div class="comp-share-case-cards comp-share-case-items">${(example.items||[]).map(c=>caseCard(c,'Item')).join('')||'<span class="run-data-note">暂无物品数据</span>'}</div><div class="comp-share-case-label">技能 · ${example.skills?.length||0} 个</div><div class="comp-share-case-cards comp-share-case-skills">${(example.skills||[]).map(c=>caseCard(c,'Skill')).join('')||'<span class="run-data-note">暂无技能数据</span>'}</div></article>`;
        }).join('');
        const scope = `${data.scope.phase} · ${data.scope.rank==='legendary'?'传奇':'全部段位'} · ${data.scope.days?'最近 '+data.scope.days+' 天':'当前阶段全部时间'}`;
        return `<header class="run-share-header"><div class="run-share-info"><div class="comp-share-brand">巴扎丘Bot · 阵容分析${location.pathname.startsWith('/comp-preview')?' · 验收预览':''}</div><h2>${esc(row.hero_name)} · 核心组合</h2><p class="run-data-note">${esc(scope)} · ${row.sample_level==='explore'?'小样本探索':'主推荐'}</p></div><div class="run-share-qr"><img src="${esc(qr)}" alt="核心组合直达二维码"><div>扫码查看此组合</div></div></header>
          <section class="comp-share-core"><h3>核心物品</h3><div>${row.core.map(c=>shareCard(c)).join('')}</div></section>
          <div class="comp-share-metrics">${[[pct(row.rate),'样本 10 胜率'],[row.average_wins.toFixed(1),'平均胜场'],[num(row.count),'样本局数'],[num(row.ten_wins),'10 胜局数']].map(([value,label])=>`<div><strong>${value}</strong><span>${label}</span></div>`).join('')}</div>
          <div class="comp-share-companions"><section><h3>10 胜样本 · 物品</h3>${list(row.winning_items)}</section><section><h3>10 胜样本 · 技能</h3>${list(row.winning_skills)}</section></div>
          <section class="comp-share-examples"><h3>10 胜阵容案例 · ${cases.length} 套</h3>${exampleMarkup||'<p class="run-data-note">该组合暂无可展示的 10 胜阵容案例。</p>'}</section>
          <p class="comp-share-note">搭配频率基于该组合的 ${num(row.ten_wins)} 局 10 胜记录；原始采集不含 0 胜局。</p><div class="run-share-watermark">bazaarqiubot.com</div>`;
      }
    });
  }
  function group(row, index) {
    const winningExamples = (row.examples || []).filter(example => example.wins === 10);
    const winning = row.ten_wins ? `
      <details class="comp-winning-details"><summary>10 胜样本高频物品与技能</summary><div class="comp-companions">
        <section><h3>10 胜样本 · 物品</h3>${companions(row.winning_items,true)}</section>
        <section><h3>10 胜样本 · 技能</h3>${companions(row.winning_skills,true)}</section>
      </div></details>
      <details data-comp-examples open><summary>查看真实完整阵容（${winningExamples.length} 局 10 胜）</summary>${examples(winningExamples)}</details>
    ` : '<p class="comp-note">该组合暂无 10 胜样本，暂不展示物品与技能搭配。</p>';
    return `<article class="comp-idea-card comp-analysis-group" data-comp-group="${esc(row.id)}">
      <div class="comp-idea-head"><div>
        <div class="comp-idea-title">${index+1}. ${esc(row.hero_name)} <span class="comp-sample-badge ${row.sample_level==='explore'?'is-explore':''}">${row.sample_level==='explore'?'小样本探索':'主推荐'}</span></div>
        <div class="comp-rep-cards">${row.core.map(c=>card(c)).join('')}</div>
      </div><div class="comp-metrics">
        <div class="comp-metric"><strong>${pct(row.rate)}</strong><span>样本 10 胜率</span></div>
        <div class="comp-metric"><strong>${row.average_wins.toFixed(1)}</strong><span>平均胜场</span></div>
        <div class="comp-metric"><strong>${num(row.count)}</strong><span>样本局数</span></div>
      </div></div>
      <p class="comp-note">10 胜 ${num(row.ten_wins)} 局</p>
      ${winning}
      <div class="comp-group-actions"><button type="button" class="run-action" data-comp-share="${esc(row.id)}">分享组合</button></div>
    </article>`;
  }
  function render(data) {
    const el = document.getElementById('comp-result');
    anchors = new Set(data.anchors.map(c=>c.cardId));
    const base = data.baseline, scope = data.scope;
    const regular = data.groups.filter(g=>g.sample_level==='regular');
    const explore = data.groups.filter(g=>g.sample_level==='explore');
    const state = `${scope.phase} · ${heroes[scope.hero]||'全部英雄'} · ${scope.rank==='legendary'?'传奇':'全部段位'} · ${scope.days?'最近 '+scope.days+' 天':'当前阶段全部时间'}`;
    const statistics = `<details class="comp-statistics"><summary>统计说明</summary><div class="comp-note">
      <p>10 胜率、平均胜场和样本局数基于全部匹配对局计算。原始采集不含 0 胜局，展示结果不能视为拿到物品后的获胜概率。</p>
      <p>搭配频率以各组合的 10 胜局数为分母；补充物品不含上方核心物品。实战样例中的物品、技能与截图来自同一局。</p>
      <p>主推荐至少 30 局；10–29 局列为小样本探索。每个核心组合最多补充 3 张物品。稳健推荐按 10 胜率的 95% Wilson 下界排序，兼顾样本量；不同组合可能覆盖同一局。</p>
      <p>实战案例优先选高频相近阵容，同频时兼顾本家及中立物品，再参考时间。相近阵容忽略品质、附魔、技能和位置，组内任意两套物品最多差 1 件；次数为组内累计 10 胜局数。</p>
    </div></details>`;
    el.innerHTML = `
      <div class="comp-analysis-scope">${esc(state)} · 同时包含 ${data.anchors.map(c=>esc(c.name)).join('、')}</div>
      <div class="comp-summary">
        <div class="comp-summary-card"><div class="comp-summary-label">命中有效对局</div><div class="comp-summary-value">${num(base.count)}</div></div>
        <div class="comp-summary-card"><div class="comp-summary-label">查询样本 10 胜率</div><div class="comp-summary-value">${pct(base.rate)}</div></div>
        <div class="comp-summary-card"><div class="comp-summary-label">平均胜场</div><div class="comp-summary-value">${base.average_wins.toFixed(1)}</div></div>
        <div class="comp-summary-card"><div class="comp-summary-label">达到 10 局的候选组合</div><div class="comp-summary-value">${num(data.candidate_count)}</div></div>
      </div>
      ${base.count?`
        <div class="comp-toolbar"><b>推荐核心组合</b><div class="comp-sort-tabs">${[['reliable','稳健推荐'],['rate','10 胜率'],['appearance','出现次数']].map(([value,label])=>`<button type="button" class="comp-sort-btn ${mode===value?'active':''}" data-comp-sort="${value}" aria-pressed="${mode===value}">${label}</button>`).join('')}</div></div>
        ${regular.length?regular.map(group).join(''):'<div class="result-card"><div class="empty">暂无达到 30 局的主推荐组合</div></div>'}
        ${explore.length?`<p class="comp-explore-title">小样本探索</p>${explore.map((g,i)=>group(g,i+regular.length)).join('')}`:''}
        ${!data.groups.length?`<section class="comp-idea-card"><b>暂未形成足够样本的扩展组合</b><p class="comp-note">以下为匹配物品的 10 胜实战样例。</p>${examples(data.examples) || '<p class="comp-note">暂无 10 胜实战样例。</p>'}</section>`:''}
      `:'<div class="result-card"><div class="empty">没有同时满足这些物品和筛选条件的对局，可减少物品或放宽筛选。</div></div>'}
      ${statistics}`;
    el.querySelectorAll('[data-comp-sort]').forEach(button=>button.onclick=()=>{ mode=button.dataset.compSort;track('sort','',mode);void query('sort'); });
    const sort = mode;
    el.querySelectorAll('[data-comp-share]').forEach(button=>button.onclick=()=>shareGroup(data,data.groups.find(g=>g.id===button.dataset.compShare),sort));
    el.querySelectorAll('[data-comp-examples]').forEach(details=>details.querySelector('summary').addEventListener('click',()=>{
      if(!details.open)track('examples_open',details.closest('[data-comp-group]').dataset.compGroup);
    }));
    el.querySelectorAll('.comp-detail-actions a').forEach(link=>link.addEventListener('click',()=>{
      track(link.getAttribute('href').startsWith('/runs?')?'run_open':'source_open',link.closest('[data-comp-group]')?.dataset.compGroup||'');
    }));
    el.dispatchEvent(new CustomEvent('comp-rendered', {detail:{total:base.count}}));
  }
  async function query(origin = 'user') {
    const el = document.getElementById('comp-result');
    cancel();
    if (!selectedRunCards.length || selectedRunCards.length > 5) {
      el.innerHTML='<div class="result-card"><div class="empty">请选择 1–5 张物品，多张物品需同时包含</div></div>'; return;
    }
    if (selectedRunCards.some(c=>c.enchantment)) {
      el.innerHTML='<div class="result-card"><div class="empty">附魔筛选目前仅支持阵容列表；查看阵容分析请将所有物品改为不限附魔</div></div>'; return;
    }
    if (selectedRunCards.some(c=>String(c.type||'').toLowerCase()==='skill')) {
      el.innerHTML='<div class="result-card"><div class="empty">阵容分析仅支持物品筛选，技能作为搭配结果展示</div></div>'; return;
    }
    if (document.getElementById('runs-card-search').value.trim()) { showError('runs-error','请先从联想列表选择输入的物品，或清空输入框后再查询'); return; }
    const days=document.getElementById('runs-days').value;
    if (days && (!/^\d+$/.test(days)||Number(days)<1||Number(days)>365)) { showError('runs-error','最近天数须为 1–365 的整数'); return; }
    const request = serial, current = controller = new AbortController();
    const targetGroup = origin==='auto' ? new URLSearchParams(location.search).get('group') : '';
    const selected = selectedRunCards.map(c=>({...c}));
    const hero=document.getElementById('runs-hero').value, rank=document.getElementById('runs-rank').value;
    const params=new URLSearchParams({cards:JSON.stringify(selected.map(c=>c.name)),rank,sort:mode});
    if(selected.every(c=>c.cardId))params.set('card_ids',JSON.stringify(selected.map(c=>c.cardId)));
    if(hero)params.set('hero',hero);if(days)params.set('days',days);
    el.innerHTML='<div class="runs-progress-wrap"><div class="runs-progress-label">正在分析阵容数据，请稍候…</div><div class="runs-progress-bar-bg"><div class="runs-progress-bar comp-loading-bar"></div></div></div>';
    const timeout=setTimeout(()=>current.abort(new DOMException('查询超时','TimeoutError')),30000);
    const finishProgress=window.BZQueryProgress?.startManual('comp');
    try {
      const url='/api/comp/card?'+params;
      const options={signal:current.signal,bazaarProgress:'manual'};
      const response=await (window.BazaarSearch?BazaarSearch.query('comp_card',url,options,origin==='user'):fetch(url,options));
      const data=await response.json();
      if(request!==serial)return;
      if(!response.ok||data.error)throw Error(data.error||'阵容分析失败');
      render(data);
      const share=new URLSearchParams({mode:'comp',cards:selected.map(c=>c.name).join('+'),rank,sort:mode,auto:'1'});
      if(hero)share.set('hero',hero);if(days)share.set('days',days);
      if(targetGroup)share.set('group',targetGroup);
      history.replaceState(null,'','/runs?'+share);
      if (targetGroup) {
        const target = [...el.querySelectorAll('[data-comp-group]')].find(group=>group.dataset.compGroup===targetGroup);
        if (target) {
          target.classList.add('comp-shared-group');target.tabIndex=-1;
          track('share_landing',targetGroup);
          requestAnimationFrame(()=>{target.scrollIntoView({block:'start'});target.focus({preventScroll:true});});
        } else {
          const note=document.createElement('p');note.className='comp-note';note.setAttribute('role','status');
          note.textContent='分享的组合暂未进入当前推荐列表，可查看下方结果或调整筛选。';el.prepend(note);
        }
      }
    } catch(error) {
      if(request!==serial)return;
      const message=current.signal.aborted?'阵容分析超时，请稍后重试':error.message;
      el.innerHTML=`<div class="error-msg">${esc(message)}</div>`;
    } finally { finishProgress?.();clearTimeout(timeout);if(request===serial)controller=null; }
  }
  window.BazaarComp={query,cancel,render,track};
})();
