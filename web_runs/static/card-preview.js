/* Shared encyclopedia preview. Existing card-selection, statistics and share actions keep ownership. */
(() => {
  'use strict';
  if (window.BazaarCardPreview) return;
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const safeImage = value => { try { const url = new URL(value, location.origin); return value && ['https:','http:'].includes(url.protocol) ? url.href : ''; } catch (_) { return ''; } };
  const qualityColors = {Bronze:'#c79b71',Silver:'#cdd7e8',Gold:'#f1ce76',Diamond:'#83ddd9',Legendary:'#d3a0f6'};
  const cache = new Map(), pending = new Map();
  let panel, content, current = null, pinned = false, serial = 0, openTimer, closeTimer, lastTrigger = null, suppressFocus = false;
  const nativeAction = trigger => trigger.closest('button,a,input,select,[role="option"],.wr-ms-item,[onclick]');

  function attrs(card, interactive = false) {
    const id = card?.cardId || card?.card_id;
    if (!id) return '';
    const label = card.name || card.name_zh || card.zh || card.name_en || card.en || '卡牌';
    const quality = Object.hasOwn(qualityColors,card.quality||card.tier||'') ? (card.quality||card.tier) : '';
    return `data-card-preview="${esc(id)}" data-card-label="${esc(label)}" data-card-image="${esc(safeImage(card.img))}" data-card-quality="${esc(quality)}" data-card-enchantment="${esc(card.enchantment||'')}"${interactive ? '' : ' tabindex="0" role="button" aria-haspopup="dialog" aria-label="'+esc('查看'+label+'的基础效果')+'"'}`;
  }

  async function load(id, quality = '', enchantment = '') {
    const key = [id, quality, enchantment].join('|');
    const hit = cache.get(key);
    if (hit && Date.now() - hit.at < 60000) return hit.data;
    if (pending.has(key)) return pending.get(key);
    const task = (async () => {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 10000);
      try {
        const query = new URLSearchParams({lang:'zh-CN'});
        if (quality) query.set('quality',quality);
        if (enchantment) query.set('enchantment',enchantment);
        const response = await fetch('/api/card-preview/'+encodeURIComponent(id)+'?'+query, {signal:controller.signal});
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || '卡牌详情暂不可用');
        cache.set(key,{data,at:Date.now()});
        if (cache.size > 160) cache.delete(cache.keys().next().value);
        return data;
      } finally { clearTimeout(timeout); }
    })();
    pending.set(key,task);
    try { return await task; } finally { pending.delete(key); }
  }

  function effectText(value) {
    // Split before escaping so digits in entities such as &#39; are never treated as stats.
    return String(value??'').split(/(-?\d+(?:\.\d+)?%?)/g).map((part,index)=>index%2?'<mark>'+esc(part)+'</mark>':esc(part)).join('').replace(/\n/g,'<br>');
  }

  function details(data, fallback) {
    const image = safeImage(data.img) || safeImage(fallback?.image);
    const units = ({Small:1,Medium:2,Large:3})[data.size] || 1;
    const options = (values, selected) => values.map(v=>`<option value="${esc(v.value)}"${v.value===selected?' selected':''}>${esc(v.label)}</option>`).join('');
    const blocks = [['主动效果',e=>e.source==='base'&&e.type==='Active'],['被动与条件效果',e=>e.source==='base'&&e.type!=='Active'],['附魔效果',e=>e.source==='enchantment'],['任务与奖励',e=>e.source==='quest']].map(([name,filter])=>{
      const effects=(data.effects||[]).filter(filter);
      return effects.length ? `<section class="bcp-group"><div class="bcp-group-title">${name}</div>${effects.map(e=>`<p class="bcp-effect">${e.condition?`<span class="bcp-condition">${esc(e.condition)}</span>`:''}${effectText(e.text)}</p>`).join('')}</section>` : '';
    }).join('');
    return `<div class="bcp-art-wrap">${image?`<img class="bcp-art" style="--bcp-units:${units}" src="${esc(image)}" alt="${esc(data.name)}牌面">`:'<span class="bcp-art-missing">暂无牌面图片</span>'}</div>
      <h2 class="bcp-name">${esc(data.name)}</h2><div class="bcp-en">${esc(data.name_en)}</div>
      <div><span class="bcp-quality">${esc(data.qualityLabel)}</span> · ${esc(({Small:'小型',Medium:'中型',Large:'大型'})[data.size]||'')} · ${data.type==='Skill'?'技能':'物品'}</div>
      <div class="bcp-tags">${(data.tags||[]).map(tag=>`<span class="bcp-tag">${esc(tag)}</span>`).join('')}</div>
      <div class="bcp-controls"><label>预览品质<select data-bcp-quality>${options(data.availableQualities||[],data.quality)}</select></label><label>预览附魔<select data-bcp-enchantment><option value="">无附魔</option>${options(data.availableEnchantments||[],data.enchantment)}</select></label></div>
      <div class="bcp-attributes">${(data.attributes||[]).map(a=>`<span class="bcp-attribute">${esc(a.label)}<strong>${esc(a.value)}</strong></span>`).join('')}</div>
      ${blocks||'<p class="bcp-note">该品质暂无效果说明。</p>'}
      ${data.hasDynamicValues?'<p class="bcp-dynamic">部分数值随阵容或对局变化，不能从图鉴数据确定。</p>':''}`;
  }

  function bindImages(root) {
    root.querySelectorAll('.bcp-art').forEach(image=>image.addEventListener('error',()=>{
      const fallback=document.createElement('span');fallback.className='bcp-art-missing';fallback.textContent='牌面加载失败';image.replaceWith(fallback);
    },{once:true}));
  }

  function position() {
    if (!panel || panel.hidden || !current) return;
    if (!current.trigger.isConnected) { hide(false); return; }
    if (innerWidth<=700) return;
    const anchor=current.trigger.getBoundingClientRect(), box=panel.getBoundingClientRect();
    const right=anchor.right+10, left=anchor.left-box.width-10;
    panel.style.left=Math.max(12,Math.min(right+box.width<=innerWidth-12?right:left,innerWidth-box.width-12))+'px';
    panel.style.top=Math.max(12,Math.min(anchor.top,innerHeight-box.height-12))+'px';
  }

  function ensurePanel() {
    if (panel) return;
    panel=document.createElement('section');panel.id='bazaar-card-preview';panel.className='bcp-panel';panel.hidden=true;
    panel.setAttribute('role','dialog');panel.setAttribute('aria-label','卡牌基础效果');panel.setAttribute('aria-modal','false');panel.setAttribute('data-html2canvas-ignore','true');
    panel.innerHTML='<div class="bcp-toolbar"><span class="bcp-caption">卡牌基础效果</span><div class="bcp-toolbar-actions"><button type="button" class="bcp-close" data-bcp-close aria-label="关闭卡牌详情">×</button></div></div><div class="bcp-content" aria-live="polite"></div>';
    document.body.appendChild(panel);content=panel.querySelector('.bcp-content');
    new MutationObserver(()=>{if(current&&!current.trigger.isConnected)hide(false);}).observe(document.body,{childList:true,subtree:true});
    panel.querySelector('[data-bcp-close]').onclick=()=>hide(true);
    panel.addEventListener('pointerenter',()=>clearTimeout(closeTimer));
    panel.addEventListener('pointerleave',()=>scheduleClose());
    panel.addEventListener('focusin',event=>{clearTimeout(closeTimer);if(event.target.matches('select'))setPinned(true);});
    panel.addEventListener('focusout',()=>scheduleClose());
    panel.addEventListener('change',event=>{ if(current){clearTimeout(closeTimer);setPinned(true);renderCurrent(content.querySelector('[data-bcp-quality]')?.value,content.querySelector('[data-bcp-enchantment]')?.value,event.target.hasAttribute('data-bcp-quality')?'[data-bcp-quality]':'[data-bcp-enchantment]');} });
  }

  function setPinned(value) {
    pinned=value;
  }
  function pin() {
    if (!current) return;
    clearTimeout(closeTimer);setPinned(true);
    panel.querySelector('[data-bcp-close]').focus({preventScroll:true});
  }
  function hide(restore) {
    clearTimeout(openTimer);clearTimeout(closeTimer);serial++;
    if(panel)panel.hidden=true;
    const trigger=current?.trigger;
    if(trigger){trigger.removeAttribute('aria-controls');trigger.removeAttribute('aria-expanded');}
    current=null;pinned=false;
    if(restore&&trigger?.isConnected){suppressFocus=true;trigger.focus({preventScroll:true});suppressFocus=false;}
  }
  function scheduleClose() {
    clearTimeout(closeTimer);
    if (pinned) return;
    closeTimer=setTimeout(()=>{
      if(!current||current.trigger.matches(':hover')||panel.matches(':hover')||panel.contains(document.activeElement)||current.trigger.contains(document.activeElement))return;
      hide(false);
    },160);
  }
  async function renderCurrent(quality='',enchantment='',focusControl='') {
    const ticket=++serial, owner=current;
    content.innerHTML=`<div class="bcp-status" role="status">正在读取${esc(owner.label)}的效果…</div>`;position();
    try {
      const data=await load(owner.id,quality,enchantment);
      if(ticket!==serial||current!==owner)return;
      owner.data=data;panel.style.setProperty('--bcp-quality',qualityColors[data.quality]||qualityColors.Bronze);
      content.innerHTML=details(data,owner);bindImages(content);position();
      if(focusControl)content.querySelector(focusControl)?.focus({preventScroll:true});
    } catch(error) {
      if(ticket!==serial||current!==owner)return;
      content.innerHTML='<p class="bcp-status">卡牌详情暂不可用，请稍后重试。</p><button type="button" data-bcp-retry>重试</button>';
      content.querySelector('[data-bcp-retry]').onclick=()=>renderCurrent(quality,enchantment);position();
    }
  }
  function show(trigger,fixed=false) {
    clearTimeout(openTimer);clearTimeout(closeTimer);ensurePanel();
    if(current?.trigger===trigger){if(fixed)pin();return;}
    if(pinned&&!fixed)return;
    if(current)hide(false);
    current={trigger,id:trigger.dataset.cardPreview,label:trigger.dataset.cardLabel||'卡牌',image:trigger.dataset.cardImage,quality:trigger.dataset.cardQuality||'',enchantment:trigger.dataset.cardEnchantment||''};
    panel.hidden=false;setPinned(fixed);trigger.setAttribute('aria-controls',panel.id);trigger.setAttribute('aria-expanded','true');
    renderCurrent(current.quality,current.enchantment);if(fixed)pin();
  }

  function mount(container,card) {
    const id=card?.cardId||card?.card_id;if(!container||!id)return;
    const section=document.createElement('section');section.className='bcp-inline';section.setAttribute('aria-label','卡牌基础效果');container.appendChild(section);
    const modal=container.closest('.tier-modal'), previous=document.activeElement;
    if(modal){
      modal.setAttribute('role','dialog');modal.setAttribute('aria-modal','true');modal.setAttribute('aria-label',(card.name||card.name_zh||'卡牌')+'统计与效果');
      const restore=()=>{if(previous?.isConnected){suppressFocus=true;previous.focus({preventScroll:true});suppressFocus=false;}};
      modal.querySelector('.tier-modal-close')?.addEventListener('click',restore);
      modal.addEventListener('click',event=>{if(event.target===modal)restore();});
      modal.addEventListener('keydown',event=>{
        if(event.key==='Escape'){event.preventDefault();event.stopPropagation();modal.remove();restore();}
        if(event.key==='Tab'){
          const controls=[...modal.querySelectorAll('button:not(:disabled),select:not(:disabled),a[href],[tabindex="0"]')].filter(el=>el.getClientRects().length);
          const first=controls[0],last=controls[controls.length-1];
          if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus();}
          else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}
        }
      });
      modal.querySelector('.tier-modal-close')?.focus({preventScroll:true});
    }
    let ticket=0;
    async function render(quality='',enchantment='',focusControl='') {
      const sequence=++ticket;section.innerHTML='<p class="bcp-status">正在读取卡牌效果…</p>';
      try {
        const data=await load(id,quality,enchantment);if(sequence!==ticket||!section.isConnected)return;
        section.style.setProperty('--bcp-quality',qualityColors[data.quality]||qualityColors.Bronze);
        section.innerHTML=details(data,{image:card.img});bindImages(section);
        if(focusControl)section.querySelector(focusControl)?.focus({preventScroll:true});
      } catch (_) {if(sequence!==ticket)return;section.innerHTML='<p class="bcp-status">卡牌效果暂不可用。</p><button type="button">重试</button>';section.querySelector('button').onclick=()=>render(quality,enchantment);}
    }
    section.addEventListener('change',event=>render(section.querySelector('[data-bcp-quality]')?.value,section.querySelector('[data-bcp-enchantment]')?.value,event.target.hasAttribute('data-bcp-quality')?'[data-bcp-quality]':'[data-bcp-enchantment]'));render();
  }

  document.addEventListener('pointerover',event=>{
    if(event.pointerType==='touch')return;
    const trigger=event.target.closest('[data-card-preview]');
    if(!trigger||trigger.contains(event.relatedTarget))return;
    clearTimeout(closeTimer);clearTimeout(openTimer);lastTrigger=trigger;
    if(pinned)return;
    if(current&&current.trigger!==trigger)hide(false);
    openTimer=setTimeout(()=>{if(trigger.isConnected&&lastTrigger===trigger)show(trigger);},200);
  });
  document.addEventListener('pointerout',event=>{
    const trigger=event.target.closest('[data-card-preview]');
    if(!trigger||trigger.contains(event.relatedTarget))return;
    clearTimeout(openTimer);lastTrigger=null;scheduleClose();
  });
  document.addEventListener('focusin',event=>{const trigger=event.target.closest('[data-card-preview]');if(trigger&&!suppressFocus)show(trigger);});
  document.addEventListener('focusout',event=>{if(event.target.closest('[data-card-preview]'))scheduleClose();});
  document.addEventListener('click',event=>{
    const trigger=event.target.closest('[data-card-preview]');
    if(trigger&&!nativeAction(trigger)){show(trigger,true);return;}
    if(trigger&&nativeAction(trigger)&&!pinned){hide(false);return;}
    if(pinned&&!event.composedPath().includes(panel)&&!current?.trigger.contains(event.target))hide(false);
  });
  document.addEventListener('keydown',event=>{
    if(event.key==='Escape'&&current){event.preventDefault();hide(pinned);return;}
    const trigger=event.target.closest('[data-card-preview]');
    if(trigger&&!nativeAction(trigger)&&(event.key==='Enter'||event.key===' ')){event.preventDefault();show(trigger,true);}
  },true);
  window.addEventListener('resize',position);window.addEventListener('scroll',event=>{if(panel?.contains(event.target))return;if(!pinned&&current&&!panel.contains(document.activeElement)&&!current.trigger.contains(document.activeElement))hide(false);else position();},true);
  window.BazaarCardPreview={attrs,mount};
})();
