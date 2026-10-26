/* Local acceptance preview. Static encyclopedia values, no combat simulation. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const base = document.querySelector('meta[name="library-base"]')?.content || '';
  const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  const heroNames = {Jules:'朱尔斯',Vanessa:'瓦内莎',Dooley:'杜利',Pygmalien:'皮格马利翁',Mak:'马克',Stelle:'斯黛尔',Karnok:'卡诺克',Hero8:'双龙'};
  const heroMarks = {Jules:'朱',Vanessa:'瓦',Dooley:'杜',Pygmalien:'皮',Mak:'马',Stelle:'斯',Karnok:'卡',Hero8:'双'};
  const qualities = {Bronze:'青铜',Silver:'白银',Gold:'黄金',Diamond:'钻石',Legendary:'传奇'};
  const qualityVar = {Bronze:'bronze',Silver:'silver',Gold:'qgold',Diamond:'diamond',Legendary:'legendary'};
  const qualityOrder = Object.keys(qualities);
  const sizeUnits = {Small:1,Medium:2,Large:3};
  const sizeNames = {Small:'小型',Medium:'中型',Large:'大型'};
  const DRAFT_KEY = 'bazaarqiubot-library-drafts-v1' + base, CURRENT_KEY = 'bazaarqiubot-library-current-v1' + base;
  const filter = {type:'Item',hero:'Jules',tags:[],sizes:[],qualities:[],merchant:'',q:'',hidden:false,common:false,sort:'hero'};
  const PAGE_SIZE = 12, IMAGE_CONCURRENCY = 4;
  let catalog = [], cardMap = new Map(), merchants = [], version = '', fingerprint = '', limit = PAGE_SIZE, allTags = false;
  let catalogImageObserver = null, catalogImageMargin = '', observedBuilderHeight = 0;
  const imageQueue = new Set(), imageJobs = new Map();
  let build = {schema:1,name:'未命名构筑',hero:'Jules',items:[],skills:[]}, past = [], future = [], moving = null;
  let detail = null, detailSerial = 0, toastTimer, detailsCache = new Map(), renderedCards = [];
  let drag = null, suppressClickUntil = 0;
  const grip = '<svg viewBox="0 0 20 20" fill="currentColor" aria-hidden="true"><circle cx="7" cy="5" r="1.4"/><circle cx="13" cy="5" r="1.4"/><circle cx="7" cy="10" r="1.4"/><circle cx="13" cy="10" r="1.4"/><circle cx="7" cy="15" r="1.4"/><circle cx="13" cy="15" r="1.4"/></svg>';

  function track(action,detail='',duration){try{window.BazaarLibraryAnalytics?.track(action,detail,duration);}catch(_){}}
  let searchTrackTimer;
  function notify(message) {
    $('toast').textContent = message; $('toast').hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => $('toast').hidden = true, 3600);
  }
  function storageGet(key, fallback) { try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch (_) { return fallback; } }
  function storageSet(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); return true; }
    catch (_) { $('saved-status').textContent = '本机存储不可用，请导出图片保留构筑'; return false; }
  }
  const clone = value => JSON.parse(JSON.stringify(value));
  function persist() {
    build.version = version; build.fingerprint = fingerprint;
    if (storageSet(CURRENT_KEY, build)) $('saved-status').textContent = '✓ 已自动保存到本机';
  }
  function commit(change,action='',detail='') {
    past.push(clone(build)); if (past.length > 60) past.shift(); future = [];
    change(); persist(); renderBuilder();if(action)track(action,detail);
  }
  function uid() { return crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(36).slice(2); }
  function unit(card) { return sizeUnits[card?.size] || 1; }
  function isPackage(card) { return card?.type==='Item' && (card.hiddenTags||[]).includes('Package'); }
  function occupied(exceptUid = '') {
    const result = new Set();
    for (const entry of build.items) if (entry.uid !== exceptUid) {
      for (let i = entry.start; i < entry.start + unit(cardMap.get(entry.id)); i++) result.add(i);
    }
    return result;
  }
  function fits(id, start, exceptUid = '') {
    const count = unit(cardMap.get(id)), taken = occupied(exceptUid);
    return Number.isInteger(start) && start >= 0 && start + count <= 10 && Array.from({length:count}, (_, i) => start + i).every(i => !taken.has(i));
  }
  function firstSpace(id) { for (let i = 0; i < 10; i++) if (fits(id, i)) return i; return -1; }
  function placement(id, start, entryUid = '') {
    return window.BazaarBoard.planPlacement(build.items.map(e=>({...e,width:unit(cardMap.get(e.id))})),entryUid,unit(cardMap.get(id)),start);
  }
  function applyPlacement(plan) {
    for (const position of plan.positions) build.items.find(e=>e.uid===position.uid).start=position.start;
  }
  function addCard(id, quality = '', enchantment = '', start = null) {
    const card = cardMap.get(id); if (!card) return;
    if(isPackage(card))return notify('包裹不作为构筑物品');
    const isSkill = card.type === 'Skill';
    if (isSkill && build.skills.length >= 60) return notify('本版最多保存 60 个技能');
    const free = !isSkill && firstSpace(id);
    const plan = isSkill ? null : placement(id,start ?? (free >= 0 ? free : 10-unit(card)));
    if (!isSkill && !plan) return notify('构筑已满，剩余格数不足；可先移除物品');
    const entry = {uid:uid(),id,quality:quality || card.quality,enchantment:enchantment || '',...(!isSkill ? {start:plan.start} : {})};
    commit(() => {if(plan)applyPlacement(plan);build[isSkill ? 'skills' : 'items'].push(entry);},'build_add',isSkill?'skill':'item');
    notify('已加入构筑：' + card.name);
  }
  function removeEntry(entryUid) {
    commit(() => { build.items = build.items.filter(e => e.uid !== entryUid); build.skills = build.skills.filter(e => e.uid !== entryUid); },'build_remove');
    if (moving === entryUid) moving = null;
  }
  function moveEntry(entryUid, start) {
    const entry = build.items.find(e => e.uid === entryUid);
    const plan=entry&&placement(entry.id,start,entryUid);
    if (!plan) return notify('这里放不下，已保留原来的位置');
    moving=null;
    if(entry.start===plan.start&&!plan.shifted){renderBuilder();return;}
    commit(() => {applyPlacement(plan);entry.start=plan.start;},'build_move');
  }
  function validateBuild(input) {
    if (!input || input.schema !== 1 || !Array.isArray(input.items) || !Array.isArray(input.skills)) throw new Error('构筑格式无法识别');
    const result = {schema:1,name:String(input.name || '未命名构筑').slice(0,48),hero:Object.hasOwn(heroNames,input.hero) ? input.hero : 'Jules',items:[],skills:[],version:input.version,fingerprint:input.fingerprint};
    const taken = new Set();
    function validEntry(entry, type) {
      const card = cardMap.get(entry?.id);
      if (!card || card.type !== type) throw new Error('构筑中存在当前数据未收录的卡牌');
      if (!card.availableQualities.some(q => q.value === entry.quality)) throw new Error('构筑中存在不可用的品质');
      if (entry.enchantment && !card.availableEnchantments.some(e => e.value === entry.enchantment)) throw new Error('构筑中存在不可用的附魔');
      return {uid:uid(),id:entry.id,quality:entry.quality,enchantment:entry.enchantment || ''};
    }
    if (input.items.length > 10 || input.skills.length > 60) throw new Error('构筑内容超出本版限制');
    for (const entry of input.items) {
      const value = validEntry(entry,'Item'), width = unit(cardMap.get(value.id));
      if (!Number.isInteger(entry.start) || entry.start < 0 || entry.start + width > 10) throw new Error('物品站位超出棋盘');
      for (let i = entry.start; i < entry.start + width; i++) { if (taken.has(i)) throw new Error('物品站位发生重叠'); taken.add(i); }
      result.items.push({...value,start:entry.start});
    }
    result.skills = input.skills.map(entry => validEntry(entry,'Skill'));
    return result;
  }

  function filteredCards() {
    const query = filter.q.trim().toLocaleLowerCase();
    return catalog.filter(card => {
      if (card.type !== filter.type) return false;
      if (filter.hero === 'Common' ? !card.heroes.includes('Common') : filter.hero && !card.heroes.includes(filter.hero) && !(filter.common && card.heroes.includes('Common'))) return false;
      if (filter.type === 'Item' && filter.sizes.length && !filter.sizes.includes(card.size)) return false;
      if (filter.qualities.length && !filter.qualities.includes(card.startingTier)) return false;
      if (filter.merchant && !card.merchants.includes(filter.merchant)) return false;
      const tags = filter.hidden ? [...card.tags,...card.hiddenTags] : card.tags;
      if (filter.tags.some(tag => !tags.includes(tag))) return false;
      return !query || [card.name,card.name_en,card.summary,...card.tags,...(filter.hidden ? card.hiddenTags : [])].join(' ').toLocaleLowerCase().includes(query);
    }).sort((a,b) => {
      if (filter.sort === 'size') return unit(a) - unit(b) || a.name_en.localeCompare(b.name_en);
      if (filter.sort === 'quality') return qualityOrder.indexOf(a.startingTier) - qualityOrder.indexOf(b.startingTier) || a.name_en.localeCompare(b.name_en);
      if (filter.sort === 'hero' && filter.hero) {
        const priority = card => card.heroes.length === 1 && card.heroes[0] === filter.hero ? 2 : card.heroes.includes(filter.hero) ? 1 : 0;
        return priority(b) - priority(a) || a.name_en.localeCompare(b.name_en);
      }
      return a.name.localeCompare(b.name,'zh-CN');
    });
  }
  function toggleValue(values, value) { const index = values.indexOf(value); index < 0 ? values.push(value) : values.splice(index,1); }
  function renderTags() {
    const counts = new Map(), query = $('tag-search').value.toLocaleLowerCase();
    for (const card of catalog) {
      if (card.type !== filter.type || (filter.hero && filter.hero !== 'Common' && !card.heroes.includes(filter.hero) && !(filter.common && card.heroes.includes('Common')))) continue;
      if (filter.hero === 'Common' && !card.heroes.includes('Common')) continue;
      for (const tag of new Set(filter.hidden ? [...card.tags,...card.hiddenTags] : card.tags)) counts.set(tag,(counts.get(tag)||0)+1);
    }
    let options = [...counts].filter(([tag]) => !query || tag.toLocaleLowerCase().includes(query)).sort((a,b)=>Number(filter.tags.includes(b[0]))-Number(filter.tags.includes(a[0])) || b[1]-a[1]);
    if (!allTags && !query) options = options.slice(0,12);
    $('tag-options').innerHTML = options.map(([tag,count])=>`<button class="tag-option${filter.tags.includes(tag)?' selected':''}" data-tag="${esc(tag)}" aria-pressed="${filter.tags.includes(tag)}">${esc(tag)}<small>${count}</small></button>`).join('') || '<span class="subtle">没有匹配的标签</span>';
  }
  function merchantAvatar(merchant) {
    return `<span class="merchant-avatar${merchant?.img?' has-image':''}" aria-hidden="true"><span class="merchant-initial">${esc(merchant?.name?.slice(0,1)||'◈')}</span>${merchant?.img?`<img src="${esc(merchant.img)}" alt="" loading="lazy" decoding="async">`:''}</span>`;
  }
  function renderMerchantOptions() {
    const query=$('merchant-search').value.trim().toLocaleLowerCase();
    const matches=merchants.filter(m=>!query||[m.name,m.name_en].join(' ').toLocaleLowerCase().includes(query));
    $('merchant-options').innerHTML=`<button type="button" class="merchant-option${!filter.merchant?' selected':''}" data-merchant="" aria-pressed="${!filter.merchant}">${merchantAvatar(null)}<span>全部商人</span></button>`+matches.map(m=>`<button type="button" class="merchant-option${filter.merchant===m.id?' selected':''}" data-merchant="${m.id}" aria-label="筛选商人：${esc(m.name)}" aria-pressed="${filter.merchant===m.id}">${merchantAvatar(m)}<span>${esc(m.name)}</span>${filter.merchant===m.id?'<span class="merchant-check" aria-hidden="true">✓</span>':''}</button>`).join('')+(!matches.length?'<p class="merchant-empty">没有匹配的商人</p>':'');
  }
  function renderMerchantPicker() {
    const selected=merchants.find(m=>m.id===filter.merchant);
    $('merchant-selected').innerHTML=merchantAvatar(selected)+`<span class="merchant-selected-name">${esc(selected?.name||'全部商人')}</span>`;
    $('merchant-summary').setAttribute('aria-label','选择商人：'+(selected?.name||'全部商人'));
    renderMerchantOptions();
  }
  function renderFilters() {
    document.querySelectorAll('[data-type]').forEach(button => { if (button.tagName !== 'BUTTON') return; const active = button.dataset.type === filter.type; button.classList.toggle('selected',active); button.setAttribute('aria-pressed',active); });
    document.querySelectorAll('[data-hero]').forEach(button => { const active = button.dataset.hero === filter.hero; button.classList.toggle('selected',active); button.setAttribute('aria-pressed',active); });
    document.querySelectorAll('[data-size]').forEach(button => { button.classList.toggle('selected',filter.sizes.includes(button.dataset.size)); button.setAttribute('aria-pressed',filter.sizes.includes(button.dataset.size)); });
    document.querySelectorAll('[data-quality]').forEach(button => { button.classList.toggle('selected',filter.qualities.includes(button.dataset.quality)); button.setAttribute('aria-pressed',filter.qualities.includes(button.dataset.quality)); });
    $('include-common').checked = filter.common; $('include-common').disabled = !filter.hero || filter.hero === 'Common';
    $('hidden-tags').checked = filter.hidden; renderMerchantPicker();
    $('hero-caption').textContent = filter.hero === 'Common' ? '通用' : heroNames[filter.hero] || '全部英雄';
    $('scope-label').textContent = $('hero-caption').textContent + (filter.hero && filter.hero !== 'Common' && filter.common ? ' + 通用' : '');
    $('size-filter').hidden = filter.type === 'Skill';
    renderTags();
    const chips = [];
    for (const [key,values] of [['tags',filter.tags],['sizes',filter.type === 'Item' ? filter.sizes : []],['qualities',filter.qualities]]) for (const value of values) chips.push({key,value,label:key==='sizes'?sizeNames[value]:key==='qualities'?qualities[value]:value});
    if (filter.merchant) chips.push({key:'merchant',label:merchants.find(m=>m.id===filter.merchant)?.name});
    $('active-filters').innerHTML = chips.map((chip,i)=>`<button class="active-filter" data-chip="${i}">${esc(chip.label)} ×</button>`).join('');
    $('active-filters').querySelectorAll('button').forEach((button,i)=>button.onclick=()=>{const chip=chips[i]; if(Array.isArray(filter[chip.key])) toggleValue(filter[chip.key],chip.value); else filter[chip.key]=''; refresh();});
    $('filter-count').textContent = chips.length + (filter.hero ? 1 : 0);
  }
  function cardHTML(card) {
    return `<article class="catalog-card" data-type="${esc(card.type)}" style="--units:${unit(card)}" data-drag-card="${card.cardId}"><button class="card-detail-button" data-detail="${card.cardId}" aria-label="查看${esc(card.name)}详情"><div class="card-art image-pending"><img loading="lazy" decoding="async" draggable="false" data-src="${card.img}" alt="${esc(card.name)}"><span class="art-missing">◈</span></div><div class="card-info"><span class="card-name">${esc(card.name)}</span></div></button><button class="drag-handle" data-drag-handle aria-label="拖动${esc(card.name)}到构筑" title="拖动到构筑">${grip}</button><button class="add-card" data-add="${card.cardId}" aria-label="加入${esc(card.name)}到构筑" title="加入构筑">+</button></article>`;
  }
  function pumpCatalogImages() {
    for(const image of imageQueue) {
      if(imageJobs.size>=IMAGE_CONCURRENCY)break;
      imageQueue.delete(image);
      if(!image.isConnected||!image.dataset.src)continue;
      catalogImageObserver?.unobserve(image);
      const source=image.dataset.src;delete image.dataset.src;
      const cleanup=()=>{image.removeEventListener('load',done);image.removeEventListener('error',done);imageJobs.delete(image);};
      const done=()=>{cleanup();image.parentElement?.classList.remove('image-pending');pumpCatalogImages();};
      imageJobs.set(image,cleanup);
      image.addEventListener('load',done);image.addEventListener('error',done);
      image.src=source;
    }
  }
  function observeCatalogImages() {
    // Re-observe only the current grid so removed cards cannot retain pending work.
    catalogImageObserver?.disconnect();imageQueue.clear();
    for(const [image,cleanup] of imageJobs)if(!image.isConnected){cleanup();image.removeAttribute('src');}
    const margin=`100px 0px ${100-Math.ceil($('builder').offsetHeight)}px 0px`;
    if(catalogImageMargin!==margin){catalogImageObserver=null;catalogImageMargin=margin;}
    if(!catalogImageObserver&&window.IntersectionObserver)catalogImageObserver=new IntersectionObserver(entries=>{
      for(const entry of entries){if(entry.isIntersecting)imageQueue.add(entry.target);else imageQueue.delete(entry.target);}
      pumpCatalogImages();
    },{rootMargin:margin,threshold:0.01});
    for(const image of $('card-grid').querySelectorAll('img[data-src]')) {
      if(catalogImageObserver)catalogImageObserver.observe(image);else imageQueue.add(image);
    }
    pumpCatalogImages();
  }
  function renderGrid({append=false}={}) {
    renderedCards = filteredCards();
    $('result-count').textContent = renderedCards.length.toLocaleString();
    $('result-label').textContent = filter.type === 'Skill' ? '个技能' : '件物品';
    const previous=append?$('card-grid').querySelectorAll('.catalog-card').length:0;
    const cards=renderedCards.slice(previous,limit).map(cardHTML).join('');
    if(append)$('card-grid').insertAdjacentHTML('beforeend',cards);
    else $('card-grid').innerHTML=cards||'<div class="empty-state"><strong>还没有找到匹配的卡牌</strong>试试减少筛选条件，或换一个关键词。</div>';
    observeCatalogImages();
    $('load-more').hidden = renderedCards.length <= limit;
    const remaining=Math.max(0,renderedCards.length-limit);
    $('load-more').textContent = `再加载 ${Math.min(PAGE_SIZE,remaining)} 张 · 剩余 ${remaining} 张`;
  }
  function refresh() { limit = PAGE_SIZE; renderFilters(); renderGrid(); }

  function renderBuilder() {
    $('build-name').value = build.name;
    const used = build.items.reduce((sum,e)=>sum+unit(cardMap.get(e.id)),0);
    $('slots-used').textContent = used; $('mobile-count').textContent = used+'/10'; $('skill-count').textContent = build.skills.length+' 个';
    $('undo').disabled = !past.length; $('redo').disabled = !future.length;
    $('board').innerHTML = Array.from({length:10},(_,i)=>`<button class="board-slot" data-slot="${i}" style="grid-column:${i+1}" aria-label="第${i+1}格${moving?'：放置物品':''}"><span>＋</span><small>${String(i+1).padStart(2,'0')}</small></button>`).join('') + build.items.map(entry => {
      const card = cardMap.get(entry.id);
      return `<div class="board-card${moving===entry.uid?' moving':''}" role="button" tabindex="0" data-entry="${entry.uid}" aria-label="${esc(card.name)}，第${entry.start+1}格，${qualities[entry.quality]}${entry.enchantment?'，已附魔':''}" style="grid-column:${entry.start+1}/span ${unit(card)};--quality:var(--${qualityVar[entry.quality]});--units:${unit(card)}"><img src="${card.img}" alt="${esc(card.name)}" draggable="false"><button class="drag-handle" data-drag-handle aria-label="拖动${esc(card.name)}调整位置" title="拖动调整位置">${grip}</button>${entry.enchantment?'<span class="enchant-mark">✦</span>':''}<button class="remove-entry" data-remove="${entry.uid}" aria-label="移除${esc(card.name)}">×</button></div>`;
    }).join('');
    $('skills').innerHTML = build.skills.length ? build.skills.map(entry=>{ const card=cardMap.get(entry.id); return `<button class="build-skill" data-entry="${entry.uid}" title="${esc(card.name)}" aria-label="编辑技能${esc(card.name)}" style="--quality:var(--${qualityVar[entry.quality]})"><img src="${card.img}" alt="${esc(card.name)}"></button>`; }).join('') : '<span class="skill-placeholder">为这套构筑挑选技能</span>';
    updateBuilderHeight();
  }
  function setBuilderExpanded(expanded) {
    if(expanded&&$('builder').classList.contains('collapsed'))track('builder_open');
    $('builder').classList.toggle('collapsed',!expanded);
    $('builder-content').hidden=!expanded;
    $('builder-toggle').setAttribute('aria-expanded',String(expanded));
    $('builder-toggle').setAttribute('aria-label',expanded?'收起构筑':'展开构筑');
    $('builder-toggle-label').textContent=expanded?'收起构筑':'展开构筑';
    $('mobile-build').setAttribute('aria-expanded',String(expanded));
    updateBuilderHeight();
  }
  function updateBuilderHeight() { requestAnimationFrame(()=>{
    const height=$('builder').offsetHeight;
    document.documentElement.style.setProperty('--builder-height',height+'px');
    if(height!==observedBuilderHeight){observedBuilderHeight=height;if(catalog.length)observeCatalogImages();}
  }); }

  function resetPlacementPreview() {
    for(const element of $('board').querySelectorAll('.board-card')) {
      const entry=build.items.find(e=>e.uid===element.dataset.entry);
      element.style.gridColumn=`${entry.start+1}/span ${unit(cardMap.get(entry.id))}`;
      element.classList.remove('shift-preview');
    }
    $('board').querySelector('.drop-preview')?.remove();
    $('board').classList.remove('drop-invalid');
    $('skills').classList.remove('drop-ready');
  }
  function previewDrag(event) {
    const card=cardMap.get(drag.id), rect=$('board').getBoundingClientRect(), builderRect=$('builder').getBoundingClientRect();
    const inBoard=event.clientX>=rect.left&&event.clientX<=rect.right&&event.clientY>=rect.top-15&&event.clientY<=rect.bottom+15;
    drag.ghost.style.left=Math.max(8,Math.min(innerWidth-drag.ghost.offsetWidth-8,event.clientX-drag.ghost.offsetWidth/2))+'px';
    drag.ghost.style.top=Math.max(8,event.clientY-drag.ghost.offsetHeight-24)+'px';
    resetPlacementPreview(); drag.plan=null; drag.skillTarget=false;
    if(card.type==='Skill') {
      drag.skillTarget=event.clientX>=builderRect.left&&event.clientX<=builderRect.right&&event.clientY>=rect.top-15&&event.clientY<=builderRect.bottom;
      const full=build.skills.length>=60;
      $('skills').classList.toggle('drop-ready',drag.skillTarget&&!full);
      $('placement-status').textContent=full?'技能数量已达上限':drag.skillTarget?'松手加入技能':'拖到下方构筑，加入技能';
      return;
    }
    if(!inBoard){$('placement-status').textContent='拖到物品栏，预览放置位置';return;}
    const gap=parseFloat(getComputedStyle($('board')).columnGap)||0, pitch=(rect.width+gap)/10;
    drag.plan=placement(drag.id,Math.round((event.clientX-rect.left)/pitch-drag.grabOffset),drag.entryUid);
    if(!drag.plan){$('board').classList.add('drop-invalid');$('placement-status').textContent='剩余格数不足，松手不会改变构筑';return;}
    for(const position of drag.plan.positions) {
      const element=$('board').querySelector(`[data-entry="${position.uid}"]`);
      const entry=build.items.find(e=>e.uid===position.uid);
      element.style.gridColumn=`${position.start+1}/span ${unit(cardMap.get(entry.id))}`;
      element.classList.toggle('shift-preview',position.start!==entry.start);
    }
    const preview=document.createElement('div');preview.className='drop-preview';
    preview.style.gridColumn=`${drag.plan.start+1}/span ${unit(card)}`;
    preview.textContent='↓';preview.setAttribute('aria-hidden','true');$('board').append(preview);
    $('placement-status').textContent=`松手放在第 ${drag.plan.start+1} 格${drag.plan.shifted?' · 自动腾位 '+drag.plan.shifted+' 件物品':''}`;
  }
  function finishDrag(cancelled=false) {
    if(!drag)return;
    const finished=drag;drag=null;
    if(finished.active) {
      suppressClickUntil=performance.now()+450;
      finished.ghost.remove();finished.source.classList.remove('drag-source');document.body.classList.remove('dragging-card');
      resetPlacementPreview();
      if(finished.source.hasPointerCapture?.(finished.pointerId))finished.source.releasePointerCapture(finished.pointerId);
      if(!cancelled){
        if(finished.skillTarget)addCard(finished.id);
        else if(finished.plan){if(finished.entryUid)moveEntry(finished.entryUid,finished.plan.start);else addCard(finished.id,'','',finished.plan.start);}
        else notify('未放入构筑，已保留原来的内容');
      }
      $('placement-status').textContent='';
    }
  }
  function attachDragEvents() {
    document.addEventListener('pointerdown',event=>{
      if(event.button!==0||event.isPrimary===false||drag||document.querySelector('dialog[open]'))return;
      const source=event.target.closest('[data-drag-card],.board-card');
      if(!source||event.target.closest('[data-remove],[data-add]')||(event.pointerType!=='mouse'&&!event.target.closest('[data-drag-handle]')))return;
      const entry=source.dataset.entry&&build.items.find(e=>e.uid===source.dataset.entry),id=entry?.id||source.dataset.dragCard;
      const rect=source.getBoundingClientRect();
      drag={source,id,entryUid:entry?.uid||'',pointerId:event.pointerId,x:event.clientX,y:event.clientY,active:false,grabOffset:entry?(event.clientX-rect.left)/rect.width*unit(cardMap.get(id)):unit(cardMap.get(id))/2};
    });
    document.addEventListener('pointermove',event=>{
      if(!drag||event.pointerId!==drag.pointerId)return;
      if(!drag.active){
        if(Math.hypot(event.clientX-drag.x,event.clientY-drag.y)<7)return;
        drag.active=true;moving=null;setBuilderExpanded(true);
        drag.source.setPointerCapture(event.pointerId);drag.source.classList.add('drag-source');
        document.body.classList.add('dragging-card');
        const ghost=document.createElement('div'),card=cardMap.get(drag.id);
        ghost.className='drag-ghost';ghost.style.setProperty('--units',unit(card));
        ghost.innerHTML=`<img src="${esc(card.img)}" alt=""><span>${esc(card.name)}</span>`;
        document.body.append(ghost);drag.ghost=ghost;
      }
      event.preventDefault();previewDrag(event);
    },{passive:false});
    document.addEventListener('pointerup',event=>{if(drag&&event.pointerId===drag.pointerId){if(drag.active)previewDrag(event);finishDrag();}});
    document.addEventListener('pointercancel',()=>finishDrag(true));
    document.addEventListener('lostpointercapture',()=>finishDrag(true));
    document.addEventListener('dragstart',event=>{if(event.target.closest('[data-drag-card],.board-card'))event.preventDefault();});
    window.addEventListener('blur',()=>finishDrag(true));
    document.addEventListener('click',event=>{if(performance.now()<suppressClickUntil){event.preventDefault();event.stopImmediatePropagation();}},true);
  }

  async function getDetails(id, quality='', enchantment='') {
    const key=[id,quality,enchantment].join('|');
    if (detailsCache.has(key)) return detailsCache.get(key);
    const query = new URLSearchParams({quality,enchantment,lang:'zh-CN'});
    const response = await fetch(base+'/api/card-preview/'+id+'?'+query);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || '卡牌详情暂不可用');
    detailsCache.set(key,data); return data;
  }
  function effectText(text) { return String(text).split(/(-?\d+(?:\.\d+)?%?)/g).map((part,i)=>i%2?`<mark>${esc(part)}</mark>`:esc(part)).join('').replace(/\n/g,'<br>'); }
  async function openDetails(id, entryUid='') {
    const entry = [...build.items,...build.skills].find(e=>e.uid===entryUid);
    detail = {id,entryUid,quality:entry?.quality||'',enchantment:entry?.enchantment||''};
    $('detail-content').innerHTML = '<div class="detail-top"><span>读取卡牌详情…</span><button class="icon-button" data-close="detail-dialog" aria-label="关闭详情">×</button></div><div class="empty-state">正在读取本地效果数据</div>';
    if (!$('detail-dialog').open) $('detail-dialog').showModal();
    await renderDetail();
    if($('detail-quality'))track('details',cardMap.get(id)?.type==='Skill'?'skill':'item');
  }
  function detailTags(tags) {
    return (tags||[]).map(tag=>`<span class="detail-tag">${esc(tag)}</span>`).join('');
  }
  async function renderDetail() {
    const serial = ++detailSerial, selected = {...detail};
    try {
      const data = await getDetails(selected.id,selected.quality,selected.enchantment);
      if (serial !== detailSerial || !detail || !$('detail-dialog').open) return;
      detail.quality = data.quality; detail.enchantment = data.enchantment || '';
      const card=cardMap.get(selected.id), editing=!!selected.entryUid;
      const options=(values,current)=>values.map(o=>`<option value="${esc(o.value)}"${o.value===current?' selected':''}>${esc(o.label)}</option>`).join('');
      const groups=[['主动效果',e=>e.source==='base'&&e.type==='Active'],['被动与条件效果',e=>e.source==='base'&&e.type!=='Active'],['附魔效果',e=>e.source==='enchantment'],['任务与奖励',e=>e.source==='quest']].map(([label,test])=>{
        const effects=(data.effects||[]).filter(test); return effects.length?`<section class="effect-group"><h3>${label}</h3>${effects.map(e=>`<p>${e.condition?`<span class="condition">${esc(e.condition)}</span>`:''}${effectText(e.text)}</p>`).join('')}</section>`:'';
      }).join('');
      $('detail-content').innerHTML = `<div class="detail-top"><span>${editing?'编辑构筑卡牌':'卡牌图鉴'} · ${data.type==='Skill'?'技能':esc(sizeNames[data.size])+'物品'} · ${esc(card.heroes.map(h=>heroNames[h]||(h==='Common'?'通用':h)).join('、'))}</span><button class="icon-button" data-close="detail-dialog" aria-label="关闭详情">×</button></div><div class="detail-body"><div class="detail-left"><div class="detail-art" data-type="${esc(card.type)}" style="--units:${unit(card)}"><img src="${card.img}" alt="${esc(data.name)}"></div><h2>${esc(data.name)}</h2><p class="english">${esc(data.name_en)}</p><div class="tags detail-tags" aria-label="物品标签">${detailTags(data.tags)}</div></div><div class="detail-right"><div class="detail-controls"><label>预览品质<select class="field" id="detail-quality">${options(data.availableQualities,data.quality)}</select></label><label>预览附魔<select class="field" id="detail-enchant"><option value="">无附魔</option>${options(data.availableEnchantments,data.enchantment)}</select></label></div><div class="attribute-list">${data.attributes.map(a=>`<div class="attribute">${esc(a.label)}<strong>${esc(a.value)}</strong></div>`).join('')}</div>${groups||'<p class="subtle">该品质暂无效果说明。</p>'}<details class="hidden-tags-detail"><summary><span class="hidden-tags-label">隐藏标签<span class="detail-tag-count">${data.hiddenTags?.length||0}</span></span></summary><div class="detail-tags" aria-label="隐藏标签">${detailTags(data.hiddenTags)||'<span class="subtle">无</span>'}</div></details>${editing?'':`<details><summary>商人来源 · 静态条件匹配</summary><p>${esc(merchants.filter(m=>card.merchants.includes(m.id)).map(m=>m.name).join('、')||'暂无已核实的商人来源')}</p><p class="source-note">仅表示符合货池条件，不保证当前商店会出售。</p></details>`}${data.hasDynamicValues?'<p class="source-note">部分数值随对局变化，本页仅展示可确定的基础效果。</p>':''}</div></div><div class="detail-footer${editing?' editing':''}">${editing?'':`<span>${esc(version)}<br>不包含对局成长与整套构筑结算</span>`}<div class="detail-footer-actions">${editing?`<button class="button" id="detail-remove">移除</button>`:''}<button class="button primary" id="detail-apply">${editing?'保存修改':'＋ 加入构筑'}</button></div></div>`;
      $('detail-quality').onchange=e=>{detail.quality=e.target.value;renderDetail();};
      $('detail-enchant').onchange=e=>{detail.enchantment=e.target.value;renderDetail();};
      $('detail-apply').onclick=()=>{
        if(editing){const entry=[...build.items,...build.skills].find(e=>e.uid===selected.entryUid);if(entry)commit(()=>{entry.quality=detail.quality;entry.enchantment=detail.enchantment;},'build_update');$('detail-dialog').close();notify('已更新卡牌');}
        else addCard(selected.id,detail.quality,detail.enchantment);
      };
      if(editing)$('detail-remove').onclick=()=>{removeEntry(selected.entryUid);$('detail-dialog').close();};
    } catch(error) {
      if(serial!==detailSerial)return;
      notify(error.message); $('detail-content').innerHTML='<div class="detail-top"><span>详情读取失败</span><button class="icon-button" data-close="detail-dialog" aria-label="关闭详情">×</button></div><div class="empty-state">'+esc(error.message)+'<br><button class="button" id="detail-retry">重试</button></div>';
      $('detail-retry').onclick=renderDetail;
    }
  }

  function draftSignature(value) {
    const entry=e=>({id:e.id,quality:e.quality,enchantment:e.enchantment||''});
    return JSON.stringify({name:value.name,
      items:value.items.map(e=>({...entry(e),start:e.start})).sort((a,b)=>a.start-b.start),
      skills:value.skills.map(entry)});
  }
  function saveDraft({automatic=false}={}) {
    const drafts=storageGet(DRAFT_KEY,[]);
    if(!Array.isArray(drafts)){notify('本机草稿格式异常，已保留当前构筑');return false;}
    const signature=draftSignature(build);
    const exists=drafts.some(draft=>{try{return draftSignature(draft.build)===signature;}catch(_){return false;}});
    if(exists){if(!automatic){renderDrafts();notify('这套构筑已保存，无需重复保存');}return true;}
    drafts.unshift({build:clone(build),savedAt:Date.now()});
    if(!storageSet(DRAFT_KEY,drafts.slice(0,50))){notify('草稿保存失败，已保留当前构筑');return false;}
    track('draft_save',automatic?'auto':'manual');
    if(!automatic){renderDrafts();notify('已保存草稿');}
    return true;
  }
  function renderDrafts() {
    const drafts = storageGet(DRAFT_KEY,[]);
    $('draft-count').textContent=drafts.length;
    $('draft-list').innerHTML=drafts.length?drafts.map((draft,i)=>`<div class="draft-row"><div><strong>${esc(draft.build.name)}</strong><small>${draft.build.items.length} 件物品 · ${draft.build.skills.length} 个技能</small></div><button class="button" data-load-draft="${i}">打开</button><button class="icon-button" data-delete-draft="${i}" aria-label="删除草稿${esc(draft.build.name)}">×</button></div>`).join(''):'<p class="subtle">还没有保存的草稿，当前编辑内容会自动恢复。</p>';
    $('draft-list').querySelectorAll('[data-load-draft]').forEach(button=>button.onclick=()=>{try{const value=validateBuild(drafts[Number(button.dataset.loadDraft)].build);commit(()=>build=value);track('draft_load');$('draft-dialog').close();notify('草稿已打开');}catch(e){notify(e.message);}});
    $('draft-list').querySelectorAll('[data-delete-draft]').forEach(button=>button.onclick=()=>{drafts.splice(Number(button.dataset.deleteDraft),1);if(storageSet(DRAFT_KEY,drafts))renderDrafts();});
  }
  async function loadImage(src) {
    return new Promise(resolve=>{const image=new Image();image.onload=()=>resolve(image);image.onerror=()=>resolve(null);image.src=src;});
  }
  async function exportImage() {
    if(!build.items.length&&!build.skills.length)return notify('先加入物品或技能，再导出图片');
    const started=performance.now();track('export_start');
    const button=$('export');button.disabled=true;button.textContent='正在生成…';
    try{
      const snapshot=clone(build), entries=[...snapshot.items,...snapshot.skills];
      const images=await Promise.all(entries.map(e=>loadImage(cardMap.get(e.id).img)));
      const isLight=document.documentElement.dataset.theme==='light';
      const palette={bg:isLight?'#f5fafc':'#090a2a',surface:isLight?'#ffffff':'#171047',text:isLight?'#1d3040':'#f5f4ff',muted:isLight?'#536879':'#a4a4c5',cyan:isLight?'#027da5':'#26d9f2',border:isLight?'#dce7ed':'#363254'};
      const canvas=document.createElement('canvas'),ctx=canvas.getContext('2d');canvas.width=1440;canvas.height=696+Math.max(0,Math.ceil(snapshot.skills.length/14)-1)*95;
      ctx.fillStyle=palette.bg;ctx.fillRect(0,0,canvas.width,canvas.height);ctx.fillStyle=palette.surface;ctx.fillRect(36,140,1368,318);
      ctx.fillStyle=palette.cyan;ctx.font='600 17px "Microsoft YaHei",sans-serif';ctx.fillText('巴扎丘Bot  /  图鉴构筑',48,52);
      ctx.fillStyle=palette.text;ctx.font='700 34px "Microsoft YaHei",sans-serif';ctx.fillText(snapshot.name.slice(0,32),48,110);
      const qColors=isLight?{Bronze:'#946137',Silver:'#697b8c',Gold:'#927015',Diamond:'#217d77',Legendary:'#8355ac'}:{Bronze:'#c79b71',Silver:'#b8c6da',Gold:'#ebc86d',Diamond:'#66cfc7',Legendary:'#c398e9'};
      for(let i=0;i<10;i++){ctx.strokeStyle=palette.border;ctx.strokeRect(49+i*134,164,128,258);ctx.fillStyle=palette.muted;ctx.font='13px sans-serif';ctx.fillText(String(i+1).padStart(2,'0'),57+i*134,185);}
      for(let i=0;i<snapshot.items.length;i++){
        const entry=snapshot.items[i],card=cardMap.get(entry.id),image=images[i],x=49+entry.start*134,w=unit(card)*134-6;
        ctx.fillStyle=palette.surface;ctx.fillRect(x,164,w,258);ctx.strokeStyle=qColors[entry.quality];ctx.lineWidth=4;ctx.strokeRect(x,164,w,258);ctx.lineWidth=1;
        if(image){const scale=200/image.height;ctx.drawImage(image,x+(w-image.width*scale)/2,181,image.width*scale,image.height*scale);}
        ctx.textAlign='center';ctx.fillStyle=palette.text;ctx.font='15px "Microsoft YaHei",sans-serif';ctx.fillText(card.name,x+w/2,407,w-8);ctx.textAlign='left';
        const enchant=card.availableEnchantments.find(e=>e.value===entry.enchantment)?.label;
        if(enchant){ctx.font='12px "Microsoft YaHei",sans-serif';const label='✦ '+enchant,badgeWidth=Math.min(w-12,ctx.measureText(label).width+14);ctx.fillStyle=palette.surface;ctx.fillRect(x+6,170,badgeWidth,21);ctx.fillStyle=palette.cyan;ctx.fillText(label,x+12,185,badgeWidth-10);}
      }
      ctx.fillStyle=palette.muted;ctx.font='17px "Microsoft YaHei",sans-serif';ctx.fillText('技能  /  '+snapshot.skills.length,48,494);
      for(let i=0;i<snapshot.skills.length;i++){
        const entry=snapshot.skills[i],card=cardMap.get(entry.id),image=images[snapshot.items.length+i],x=48+(i%14)*96,y=516+Math.floor(i/14)*95;
        ctx.strokeStyle=qColors[entry.quality];ctx.lineWidth=3;ctx.strokeRect(x,y,76,67);ctx.lineWidth=1;if(image)ctx.drawImage(image,x+9,y+4,58,58);ctx.fillStyle=palette.text;ctx.font='10px "Microsoft YaHei",sans-serif';ctx.fillText(card.name,x,y+82,88);
      }
      ctx.fillStyle=palette.muted;ctx.font='14px "Microsoft YaHei",sans-serif';ctx.fillText('bazaarqiubot.com · 基础构筑快照，不包含对局成长与战斗结算',48,canvas.height-34);
      const missing=images.filter(image=>!image).length;if(missing){ctx.fillStyle=palette.muted;ctx.fillText(`${missing} 张卡图加载失败，已保留名称`,940,canvas.height-34);}
      const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/png'));if(!blob)throw new Error('图片生成失败');
      const link=document.createElement('a');link.download=snapshot.name.replace(/[<>:"/\\|?*]/g,'_')+'.png';link.href=URL.createObjectURL(blob);link.click();setTimeout(()=>URL.revokeObjectURL(link.href),10000);track('export_success',missing?'missing_art':'complete',performance.now()-started);notify(missing?'图片已导出，部分牌面暂不可用':'构筑图片已导出');
    }catch(error){track('export_error','',performance.now()-started);notify('导出失败：'+error.message);}finally{button.disabled=false;button.textContent='↗ 导出图片';}
  }

  function attachEvents() {
    attachDragEvents();
    document.addEventListener('click',event=>{
      const target=event.target.closest('[data-hero],button[data-type],[data-tag],[data-size],[data-quality],[data-merchant],[data-chip],#reset');
      if(!target)return;
      const key=['hero','type','tag','size','quality','merchant'].find(k=>target.hasAttribute('data-'+k))||'reset';
      track('filter',key);
    });
    document.addEventListener('click',event=>{
      if(event.target.closest('[data-drag-handle]'))return;
      const moveTarget=event.target.closest('.board-card,[data-slot]');
      if(moving&&moveTarget){const start=moveTarget.dataset.slot!==undefined?Number(moveTarget.dataset.slot):build.items.find(e=>e.uid===moveTarget.dataset.entry)?.start;if(start!==undefined)moveEntry(moving,start);return;}
      const close=event.target.closest('[data-close]');if(close)$(close.dataset.close).close();
      const add=event.target.closest('[data-add]');if(add)addCard(add.dataset.add);
      const card=event.target.closest('[data-detail]');if(card)openDetails(card.dataset.detail);
      const remove=event.target.closest('[data-remove]');if(remove){event.stopPropagation();removeEntry(remove.dataset.remove);return;}
      const entry=event.target.closest('[data-entry]');if(entry)openDetails([...build.items,...build.skills].find(e=>e.uid===entry.dataset.entry).id,entry.dataset.entry);
      const slot=event.target.closest('[data-slot]');if(slot){if(moving)moveEntry(moving,Number(slot.dataset.slot));else notify('拖动图鉴卡牌到这里，或点击卡牌详情加入构筑');}
    });
    document.addEventListener('keydown',event=>{
      if(event.key==='Escape'&&$('merchant-picker').open){event.preventDefault();$('merchant-picker').open=false;$('merchant-summary').focus();return;}
      if(event.key==='/'&&!/INPUT|SELECT|TEXTAREA/.test(event.target.tagName)&&!document.querySelector('dialog[open]')){event.preventDefault();$('search').focus();}
      if(event.key==='Escape'){finishDrag(true);moving=null;renderBuilder();closeFilters();}
      const entry=event.target.closest('.board-card');
      if(entry&&event.target===entry&&['ArrowLeft','ArrowRight'].includes(event.key)){event.preventDefault();const uid=entry.dataset.entry,current=build.items.find(e=>e.uid===uid);moveEntry(uid,current.start+(event.key==='ArrowRight'?1:-1));$('board').querySelector(`[data-entry="${uid}"]`)?.focus();return;}
      if(entry&&event.target===entry&&(event.key==='Enter'||event.key===' ')){event.preventDefault();openDetails(build.items.find(e=>e.uid===entry.dataset.entry).id,entry.dataset.entry);}
    });
    document.querySelectorAll('[data-type]').forEach(button=>button.onclick=()=>{filter.type=button.dataset.type;filter.tags=[];refresh();});
    $('hero-options').onclick=event=>{const button=event.target.closest('[data-hero]');if(button){filter.hero=button.dataset.hero;refresh();}};
    $('tag-options').onclick=event=>{const button=event.target.closest('[data-tag]');if(button){toggleValue(filter.tags,button.dataset.tag);refresh();}};
    document.querySelectorAll('[data-size]').forEach(button=>button.onclick=()=>{toggleValue(filter.sizes,button.dataset.size);refresh();});
    document.querySelectorAll('[data-quality]').forEach(button=>button.onclick=()=>{toggleValue(filter.qualities,button.dataset.quality);refresh();});
    $('search').oninput=event=>{filter.q=event.target.value;limit=PAGE_SIZE;renderGrid();clearTimeout(searchTrackTimer);if(filter.q.trim())searchTrackTimer=setTimeout(()=>{if(filter.q.trim())track('search');},700);};
    $('tag-search').oninput=renderTags;
    $('sort').onchange=event=>{filter.sort=event.target.value;limit=PAGE_SIZE;renderGrid();track('filter','sort');};
    $('include-common').onchange=event=>{filter.common=event.target.checked;refresh();track('filter','common');};
    $('hidden-tags').onchange=event=>{filter.hidden=event.target.checked;refresh();track('filter','hidden');};
    $('merchant-search').oninput=renderMerchantOptions;
    $('merchant-options').onclick=event=>{const option=event.target.closest('[data-merchant]');if(!option)return;filter.merchant=option.dataset.merchant;refresh();$('merchant-picker').open=false;$('merchant-summary').focus();};
    $('merchant-picker').addEventListener('toggle',()=>{if($('merchant-picker').open){$('merchant-search').value='';renderMerchantOptions();}});
    document.addEventListener('pointerdown',event=>{if(!$('merchant-picker').contains(event.target))$('merchant-picker').open=false;});
    $('tags-expand').onclick=()=>{allTags=!allTags;$('tags-expand').textContent=allTags?'收起标签':'展开全部';renderTags();};
    $('reset').onclick=()=>{Object.assign(filter,{hero:'',tags:[],sizes:[],qualities:[],merchant:'',q:'',hidden:false,common:false,sort:'hero'});$('search').value='';$('tag-search').value='';$('sort').value='hero';refresh();};
    $('load-more').onclick=()=>{limit+=PAGE_SIZE;renderGrid({append:true});track('load_more');};
    let editingName=false;
    $('build-name').oninput=event=>{
      if(!editingName){past.push(clone(build));future=[];editingName=true;}
      build.name=event.target.value.trim()||'未命名构筑';persist();
      $('undo').disabled=!past.length;$('redo').disabled=!future.length;
    };
    $('build-name').onblur=()=>{editingName=false;$('build-name').value=build.name;};
    $('undo').onclick=()=>{if(!past.length)return;future.push(clone(build));build=past.pop();persist();renderBuilder();track('build_undo');};
    $('redo').onclick=()=>{if(!future.length)return;past.push(clone(build));build=future.pop();persist();renderBuilder();track('build_redo');};
    $('clear').onclick=()=>{if(build.items.length||build.skills.length){commit(()=>{build.items=[];build.skills=[];},'build_clear');notify('已清空，可使用撤销恢复');}};
    $('builder-toggle').onclick=()=>setBuilderExpanded($('builder').classList.contains('collapsed'));
    $('find-skills').onclick=()=>{filter.type='Skill';filter.tags=[];refresh();closeFilters();};
    $('mobile-build').onclick=()=>{setBuilderExpanded(true);$('builder-toggle').focus();};
    $('drafts-open').onclick=()=>{track('draft_open');renderDrafts();$('draft-dialog').showModal();};
    $('save-draft').onclick=()=>saveDraft();
    $('new-build').onclick=()=>{if((build.items.length||build.skills.length)&&!saveDraft({automatic:true}))return;commit(()=>build={schema:1,name:'未命名构筑',hero:build.hero,items:[],skills:[]},'build_new');renderDrafts();$('draft-dialog').close();};
    $('export').onclick=exportImage;
    $('filters-open').onclick=()=>{$('filters').classList.add('open');$('backdrop').hidden=false;};
    $('filters-close').onclick=closeFilters;$('backdrop').onclick=closeFilters;
    window.addEventListener('resize',updateBuilderHeight);
    if(window.ResizeObserver)new ResizeObserver(updateBuilderHeight).observe($('builder'));
    for(const dialog of document.querySelectorAll('dialog'))dialog.addEventListener('click',event=>{if(event.target===dialog){const r=dialog.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)dialog.close();}});
    document.addEventListener('error',event=>{if(event.target.tagName==='IMG')event.target.parentElement.classList.add('image-failed');},true);
  }
  function closeFilters() {$('filters').classList.remove('open');$('backdrop').hidden=true;}
  async function init() {
    try {
      const response=await fetch(base+'/api/library');if(!response.ok)throw new Error('图鉴数据暂不可用');const data=await response.json();
      // Keep historical entries readable while excluding packages from browsing and new builds.
      cardMap=new Map(data.cards.map(card=>[card.cardId,card]));
      catalog=data.cards.filter(card=>!isPackage(card));merchants=data.merchants;version=data.version;fingerprint=data.fingerprint;
      $('data-version').textContent=version;
      $('hero-options').innerHTML=[['','全部','◈'],...Object.entries(heroNames).map(([hero,name])=>[hero,name,heroMarks[hero]]),['Common','通用','✧']].map(([hero,name,mark])=>`<button class="hero-option" data-hero="${hero}" aria-pressed="false"><span class="hero-mark">${mark}</span>${name}</button>`).join('');
      $('quality-options').innerHTML=Object.entries(qualities).map(([q,name])=>`<button class="quality-option" data-quality="${q}" style="--quality:var(--${qualityVar[q]})" aria-pressed="false">${name}</button>`).join('');
      let restored=false;
      if(location.hash.startsWith('#build=')){
        try{const hash=location.hash.slice(7);if(hash.length>65536)throw new Error('分享内容过长');let base=hash.replace(/-/g,'+').replace(/_/g,'/');base+='='.repeat((4-base.length%4)%4);build=validateBuild(JSON.parse(new TextDecoder().decode(Uint8Array.from(atob(base),c=>c.charCodeAt(0)))));restored=true;notify(build.fingerprint!==fingerprint?'已打开分享；数据版本有变化，请核对卡牌效果':'已打开分享快照，可保存到我的草稿');}catch(e){notify('分享链接无法读取：'+e.message);}
      }
      if(!restored){const saved=storageGet(CURRENT_KEY,null);if(saved)try{build=validateBuild(saved);}catch(_){notify('旧草稿与当前数据不兼容，已保留原存储供后续恢复');}}
      attachEvents();refresh();renderBuilder();renderDrafts();track('view');
    }catch(error){$('card-grid').innerHTML='<div class="empty-state"><strong>图鉴暂时无法打开</strong>'+esc(error.message)+'<br>请确认本地预览服务已启动后刷新。</div>';$('data-version').textContent='数据读取失败';}
  }
  init();
})();
