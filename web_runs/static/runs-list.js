/* Public terminal run display and single-run sharing. */
(() => {
  'use strict';
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const heroes = {Vanessa:'瓦内莎',Dooley:'杜利',Mak:'马克',Pygmalien:'皮格马利翁',Stelle:'斯黛尔',Jules:'朱尔斯',Karnok:'卡诺克','The Dragons':'双龙'};
  const tiers = {Bronze:'青铜',Silver:'白银',Gold:'黄金',Diamond:'钻石',Legendary:'传奇',Unknown:'品质未知'};
  const runs = new Map(), scripts = new Map();
  let detailController, detailSerial = 0;
  const safeImage = value => {
    if (!value) return '';
    try { const u = new URL(value, location.origin); return ['http:','https:'].includes(u.protocol) ? u.href : ''; }
    catch (_) { return ''; }
  };
  const heroName = run => heroes[run.hero] || run.hero || '未知英雄';
  const dateText = value => {
    const date = value ? new Date(value) : null;
    return date && !isNaN(date) ? date.toLocaleString('zh-CN', {timeZone:'Asia/Shanghai',year:'numeric',month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit',hour12:false}) : '时间未知';
  };
  const runScope = run => [run.season != null ? `第 ${run.season} 赛季` : '', run.phase || ''].filter(Boolean).join(' · ');
  const runLink = run => { const u = new URL('/runs', location.origin); u.searchParams.set('run', run.id); return window.BazaarAcquisition?.shareUrl(u.href, 'run') || u.href; };
  const quality = c => c.isLegendary === true ? 'Legendary' : (Object.hasOwn(tiers,c.tier) ? c.tier : 'Unknown');

  function cardMarkup(card, skill = false, share = false) {
    const tier = quality(card), size = Object.hasOwn({Small:1,Medium:2,Large:3},card.size) ? card.size : 'Small';
    const units = {Small:1,Medium:2,Large:3}[size];
    const title = [card.name || card.name_en || '未知卡牌', tiers[tier], card.enchantment_zh || card.enchantment].filter(Boolean).join(' · ');
    const source = share ? shareImage(card.img) : safeImage(card.img);
    const attrs = share ? '' : (window.BazaarCardPreview?.attrs({...card,tier:tier === 'Unknown' ? null : tier}) || `tabindex="0" aria-label="${esc(title)}"`);
    const image = source ? `<img ${share ? 'data-share-src' : 'src'}="${esc(source)}" alt="" ${share ? '' : 'loading="lazy"'}>` : '';
    const enchantment = card.enchantment ? `<span class="run-card-enchantment">${esc(card.enchantment_zh || card.enchantment)}</span>` : '';
    return `<span class="run-card-chip bcp-trigger${!share && card.matched ? ' is-filter-match' : ''}" title="${esc(title)}" ${attrs}>
      <span class="run-art tier-${tier.toLowerCase()}${skill ? ' is-skill' : ''}" style="--units:${units}">${image}<span class="run-art-missing">暂无<br>卡图</span></span>${enchantment}</span>`;
  }

  function renderRows(records) {
    runs.clear();
    const html = records.map(run => {
      runs.set(String(run.id),run);
      const cards = run.cards || [], skills = [...(run.skills || [])];
      skills.sort((a,b) => Number(Boolean(b.matched)) - Number(Boolean(a.matched)));
      const image = safeImage(run.screenshot);
      const day = Number.isInteger(run.day) && run.day > 0 ? `Day ${run.day} · ` : '';
      return `<article class="run-item run-result" data-run-id="${esc(run.id)}">
        <div class="run-result-head"><span class="run-hero">${esc(heroName(run))}</span>
          <div class="run-meta"><div class="run-title">${day}${esc(run.username || '匿名')}</div><div class="run-result-time">${esc(dateText(run.created_at))}</div></div>
          <span class="win-badge">${esc(run.wins)}胜${esc(run.losses)}负</span></div>
        <section class="run-section"><div class="run-section-label">物品 <span class="run-section-count">${cards.length} 张</span></div>
          <div class="run-items run-art-list">${cards.map(c => cardMarkup(c)).join('') || '<span class="run-data-note">暂无物品数据</span>'}</div></section>
        <section class="run-section"><div class="run-section-label">技能 <span class="run-section-count">${skills.length} 个</span></div>
          <div class="run-art-list run-skills-list" id="run-skills-${esc(run.id)}">${skills.map(c => cardMarkup(c,true)).join('')}</div>
          ${skills.length ? `<button type="button" class="run-action run-skills-toggle" aria-controls="run-skills-${esc(run.id)}" aria-expanded="false" hidden>展开全部技能（共 ${skills.length} 个）</button>` : '<span class="run-data-note">暂无技能数据</span>'}</section>
        <div class="run-actions"><button type="button" class="run-action" data-run-share="${esc(run.id)}">分享阵容</button>
          <a class="run-action" href="https://bazaardb.gg/run/tracker/${encodeURIComponent(run.id)}" target="_blank" rel="noopener noreferrer">查看原始对局 ↗</a></div>
        ${image ? `<div class="run-screenshot"><img src="${esc(image)}" alt="原始对局截图" loading="lazy"><p class="run-screenshot-status" hidden>截图加载失败，可查看原始对局。</p></div>` : ''}
      </article>`;
    }).join('');
    requestAnimationFrame(updateSkills);
    return html;
  }

  function updateSkills() {
    document.querySelectorAll('.run-result .run-skills-list').forEach(list => {
      const button = list.parentElement.querySelector('.run-skills-toggle');
      if (!button) return;
      const columns = Math.max(1,Math.floor((list.clientWidth - 8 + 12) / 48));
      const matched = list.querySelectorAll('.is-filter-match').length;
      const rows = Math.max(2,Math.ceil(matched / columns));
      const expandable = list.children.length > columns * rows;
      const expanded = list.classList.contains('is-expanded');
      [...list.children].forEach((card,index) => { card.hidden = !expanded && index >= columns * rows; });
      button.hidden = !expandable;
      list.style.maxHeight = expanded || !expandable ? 'none' : `${rows * 48}px`;
    });
  }

  function querySummary(data, params = {}) {
    const parts = [data.scope ? runScope(data.scope) : '', params.hero || '全部英雄', params.days ? `近 ${params.days} 天` : '全部时间', params.rank === 'legendary' ? '传奇段位' : '全部段位', params.cards || ''];
    return `<div class="run-query-summary">${esc(parts.filter(Boolean).join(' · '))}</div>`;
  }

  function cancelDetail() { ++detailSerial; detailController?.abort(); }
  async function loadRun(id) {
    cancelDetail();
    const serial = detailSerial, target = document.getElementById('runs-result');
    target.innerHTML = '<div class="result-card"><div class="empty">正在读取分享的阵容…</div></div>';
    if (!/^[A-Za-z0-9_-]{1,128}$/.test(id || '')) {
      target.innerHTML = '<div class="result-card"><div class="empty">无效的对局链接 · <a href="/runs">返回阵容查询</a></div></div>';
      return;
    }
    detailController = new AbortController();
    const controller = detailController, timeout = setTimeout(() => controller.abort(),30000);
    try {
      const response = await fetch('/api/runs/' + encodeURIComponent(id),{signal:controller.signal});
      const data = await response.json();
      if (!response.ok) throw Error(data.error || '对局读取失败');
      if (serial !== detailSerial) return;
      const run = data.run;
      const more = new URLSearchParams({hero:heroName(run),auto:'1'});
      target.innerHTML = `<div class="result-card"><div class="result-header"><span>分享的阵容</span><a class="run-action" href="/runs?${esc(more)}">查询更多同英雄阵容</a></div>
        <div class="run-query-summary">${esc(runScope(run))}</div>${renderRows([run])}</div>`;
    } catch (error) {
      if (serial !== detailSerial) return;
      target.innerHTML = `<div class="result-card"><div class="empty">${esc(error.name === 'AbortError' ? '对局读取超时，请刷新重试' : error.message)} · <a href="/runs">返回阵容查询</a></div></div>`;
    } finally { clearTimeout(timeout); }
  }

  function shareImage(value) {
    const source = safeImage(value);
    if (!source) return '';
    const u = new URL(source);
    if (u.origin === location.origin) return u.href;
    if (u.protocol === 'https:' && u.hostname === 's.bazaardb.gg' && u.pathname.startsWith('/v1/')) {
      return '/api/card_art_proxy?url=' + encodeURIComponent(source);
    }
    return '';
  }

  function loadScript(src, globalName) {
    if (window[globalName]) return Promise.resolve();
    if (scripts.has(src)) return scripts.get(src);
    const promise = new Promise((resolve,reject) => {
      const script = document.createElement('script');
      const timeout = setTimeout(() => { script.remove(); reject(Error('分享组件加载超时，请重试')); },15000);
      script.src = src;
      script.onload = () => { clearTimeout(timeout); window[globalName] ? resolve() : reject(Error('分享组件加载失败')); };
      script.onerror = () => { clearTimeout(timeout); script.remove(); reject(Error('分享组件加载失败，请重试')); };
      document.head.appendChild(script);
    }).catch(error => { scripts.delete(src); throw error; });
    scripts.set(src,promise);
    return promise;
  }

  async function preloadImages(sheet, progress) {
    const images = [...sheet.querySelectorAll('img[data-share-src]')];
    let cursor = 0, complete = 0, failed = 0;
    async function worker() {
      while (cursor < images.length) {
        const img = images[cursor++];
        const ok = await new Promise(resolve => {
          const done = value => { clearTimeout(timer); img.onload = img.onerror = null; resolve(value); };
          const timer = setTimeout(() => done(false),12000);
          img.onload = () => done(true); img.onerror = () => done(false);
          img.src = img.dataset.shareSrc;
        });
        if (ok) { try { await img.decode(); } catch (_) {} }
        else { failed++; img.removeAttribute('src'); img.parentElement.classList.add('is-missing'); }
        progress(++complete,images.length,failed);
      }
    }
    await Promise.all(Array.from({length:Math.min(6,images.length)},worker));
    return failed;
  }

  function shareQrImage(qr) {
    const modules = qr.getModuleCount(), cell = 8, quiet = 4, frame = 10, ears = 24;
    const canvas = document.createElement('canvas');
    canvas.width = (modules + quiet * 2) * cell + frame * 2;
    canvas.height = canvas.width + ears;
    const context = canvas.getContext('2d');
    const paper = '#faf4ed', trim = '#baa7d8', ink = '#403653';
    const roundRect = (x,y,width,height,radius,color) => {
      context.fillStyle = color;
      context.beginPath(); context.roundRect(x,y,width,height,radius); context.fill();
    };
    // Tiny cat ears stay outside the four-module quiet zone.
    for (const center of [canvas.width * .21,canvas.width * .79]) {
      context.beginPath();
      context.moveTo(center - 24,ears + 12); context.lineTo(center - 14,3);
      context.quadraticCurveTo(center - 11,-1,center - 7,3);
      context.lineTo(center + 23,ears + 12); context.closePath();
      context.fillStyle = paper; context.fill();
      context.strokeStyle = trim; context.lineWidth = 3; context.stroke();
      context.beginPath();
      context.moveTo(center - 14,ears + 6); context.lineTo(center - 10,10);
      context.lineTo(center + 9,ears + 6); context.closePath();
      context.fillStyle = '#e7b8c4'; context.fill();
    }
    roundRect(0,ears,canvas.width,canvas.width,24,trim);
    roundRect(3,ears + 3,canvas.width - 6,canvas.width - 6,21,paper);
    const left = frame + quiet * cell, top = ears + frame + quiet * cell;
    const inFinder = (row,column) => (row < 7 && (column < 7 || column >= modules - 7)) || (row >= modules - 7 && column < 7);
    context.fillStyle = ink;
    for (let row = 0; row < modules; row++) {
      for (let column = 0; column < modules; column++) {
        if (!qr.isDark(row,column) || inFinder(row,column)) continue;
        const x = left + column * cell, y = top + row * cell;
        if (row === 6 || column === 6) context.fillRect(x,y,cell,cell);
        else { context.beginPath(); context.arc(x + cell / 2,y + cell / 2,cell * .46,0,Math.PI * 2); context.fill(); }
      }
    }
    for (const [row,column] of [[0,0],[0,modules - 7],[modules - 7,0]]) {
      const x = left + column * cell, y = top + row * cell;
      roundRect(x,y,cell * 7,cell * 7,cell * 1.5,'#70588d');
      roundRect(x + cell,y + cell,cell * 5,cell * 5,cell,paper);
      roundRect(x + cell * 2,y + cell * 2,cell * 3,cell * 3,cell * .8,ink);
    }
    return canvas.toDataURL('image/png');
  }

  function shareSheetMarkup(run, qr) {
    const cards = run.cards || [], skills = run.skills || [];
    return `<header class="run-share-header"><div class="run-share-info">
      <h2>${esc(heroName(run))} · ${esc(run.username || '匿名')}</h2>
      <div class="run-share-record"><span class="win-badge">${esc(run.wins)}胜${esc(run.losses)}负</span>${Number.isInteger(run.day) && run.day > 0 ? `<span class="run-data-note">Day ${run.day}</span>` : ''}</div>
      <div class="run-data-note">${esc(runScope(run))} · ${esc(dateText(run.created_at))}</div></div>
      <div class="run-share-qr"><img src="${esc(qr)}" alt="阵容直达二维码"></div></header>
      <section class="run-section" aria-label="物品"><div class="run-items run-art-list">${cards.map(c => cardMarkup(c,false,true)).join('') || '<span>暂无物品数据</span>'}</div></section>
      <section class="run-section" aria-label="技能"><div class="run-art-list run-share-skill-grid">${skills.map(c => cardMarkup(c,true,true)).join('') || '<span>暂无技能数据</span>'}</div></section>
      <div class="run-share-watermark">bazaarqiubot.com</div>`;
  }

  function openShare(run) {
    openShareCard({title:'分享这条阵容',link:runLink(run),filename:`bazaarqiubot-${run.id}.png`,
      description:'分享图包含全部物品和技能，扫码可查看详情。',
      renderSheet:qr=>shareSheetMarkup(run,qr)});
  }

  function openShareCard({title,link,filename,description,renderSheet,sheetClass='',linkLabel='复制阵容链接',onEvent}) {
    const report = (action,detail='',duration) => {try {onEvent?.(action,detail,duration);} catch (_) {}};
    document.querySelector('.run-share-dialog')?.close();
    const dialog = document.createElement('dialog'); dialog.className = 'run-share-dialog';
    dialog.setAttribute('aria-label',title);
    dialog.innerHTML = `<div class="run-share-heading"><h2>${esc(title)}</h2><button class="run-action" data-share-close aria-label="关闭分享">关闭</button></div>
      <input class="run-share-url" aria-label="阵容直达链接" readonly value="${esc(link)}">
      <div class="run-actions"><button type="button" class="run-action" data-share-link>${esc(linkLabel)}</button><button type="button" class="run-action" data-share-generate>生成分享图</button>
      <a class="run-action" data-share-download hidden download="${esc(filename)}">下载图片</a><button type="button" class="run-action" data-share-copy hidden>复制图片</button></div>
      <div class="run-share-progress" hidden aria-label="分享图生成进度">
        <ol class="run-share-steps"><li>准备组件</li><li>加载卡图</li><li>绘制长图</li><li>导出图片</li></ol>
        <div class="run-share-progress-meta"><span data-share-stage></span><span data-share-elapsed></span></div>
        <progress max="1" aria-label="当前阶段进度"></progress>
      </div><p class="run-share-status" role="status">${esc(description)}</p><div class="run-share-preview"></div>`;
    document.body.appendChild(dialog); dialog.showModal();
    report('share_open');
    const status = dialog.querySelector('.run-share-status'), generate = dialog.querySelector('[data-share-generate]');
    const download = dialog.querySelector('[data-share-download]'), copy = dialog.querySelector('[data-share-copy]');
    const progress = dialog.querySelector('.run-share-progress'), bar = progress.querySelector('progress');
    const steps = [...progress.querySelectorAll('li')], stage = progress.querySelector('[data-share-stage]');
    const elapsed = progress.querySelector('[data-share-elapsed]');
    const paint = () => new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
    let imageUrl, imageBlob, timer, activeStage=0, generationStarted;
    function finish(action,detail='') {
      if(generationStarted==null)return;
      report(action,detail,performance.now()-generationStarted);generationStarted=null;
    }
    function setStage(index, message, complete, total) {
      activeStage=index;
      steps.forEach((step,i)=>{step.dataset.state=i<index?'done':i===index?'active':'pending';});
      stage.textContent=`第 ${index+1}/4 步 · ${steps[index].textContent}`;
      status.textContent=message;bar.hidden=false;
      if (total != null) {bar.max=Math.max(1,total);bar.value=complete;}
      else bar.removeAttribute('value');
    }
    dialog.addEventListener('close',() => { finish('share_cancel');clearInterval(timer);if (imageUrl) URL.revokeObjectURL(imageUrl);dialog.remove(); },{once:true});
    dialog.querySelector('[data-share-close]').onclick = () => dialog.close();
    dialog.addEventListener('click',event => {
      if (event.target !== dialog) return;
      const r = dialog.getBoundingClientRect();
      if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) dialog.close();
    });
    dialog.querySelector('[data-share-link]').onclick = async () => {
      try { await navigator.clipboard.writeText(link);report('share_link_copy');status.textContent = '阵容链接已复制'; }
      catch (_) { dialog.querySelector('input').select(); status.textContent = '请复制上方已选中的阵容链接'; }
    };
    copy.onclick = async () => {
      try { await navigator.clipboard.write([new ClipboardItem({'image/png':imageBlob})]);report('share_image_copy');status.textContent = '分享图片已复制'; }
      catch (_) { status.textContent = '此浏览器无法复制图片，请下载图片或长按保存'; }
    };
    download.addEventListener('click',()=>{if(imageBlob)report('share_download');});
    generate.onclick = async () => {
      generate.disabled = true; generate.textContent = '生成中…';
      progress.hidden=false;progress.dataset.state='running';
      const started=performance.now();elapsed.textContent='已用时 0 秒';
      generationStarted=started;report('share_generate');
      timer=setInterval(()=>{elapsed.textContent=`已用时 ${Math.floor((performance.now()-started)/1000)} 秒`;},1000);
      setStage(0,'正在准备分享组件…');
      let host;
      try {
        await paint();
        await Promise.all([loadScript('/static/vendor/html2canvas-1.4.1.min.js','html2canvas'),loadScript('/static/vendor/qrcode-1.4.4.js','qrcode')]);
        if (!dialog.open) return;
        const qr = window.qrcode(0,'M'); qr.addData(link); qr.make();
        host = document.createElement('div'); host.className = 'run-share-render-host';
        const sheet = document.createElement('div'); sheet.className = 'run-share-sheet';
        if (sheetClass) sheet.classList.add(sheetClass);
        sheet.innerHTML = renderSheet(shareQrImage(qr),shareImage); host.appendChild(sheet); document.body.appendChild(host);
        const totalImages=sheet.querySelectorAll('img[data-share-src]').length;
        setStage(1,`正在加载卡图 0/${totalImages}`,0,totalImages);
        await paint();
        const failed = await preloadImages(sheet,(n,total,bad) => { setStage(1,`正在加载卡图 ${n}/${total}${bad ? `（${bad} 张暂不可用）` : ''}`,n,total); });
        await document.fonts.ready;
        if (!dialog.open) return;
        setStage(2,'正在绘制高清长图，案例和技能较多时需要稍候…');
        await paint();
        // Wait for the QR image as well as the card art before taking the canvas.
        await Promise.all([...sheet.querySelectorAll('img[src]')].map(img => img.decode().catch(() => {})));
        const scale = sheet.scrollHeight > 6000 ? 1 : 2;
        let renderTimeout;
        const canvas = await Promise.race([
          window.html2canvas(sheet,{backgroundColor:'#090a2a',scale,useCORS:true,allowTaint:false,logging:false,imageTimeout:12000,
            // html2canvas clones the owner document before rendering its target.
            // Keep the sheet and its ancestors; unrelated lazy images can otherwise
            // block that clone even though none of them belongs in the share image.
            ignoreElements:element => document.body.contains(element) && !element.contains(sheet) && !sheet.contains(element),
          }),
          new Promise((_,reject) => { renderTimeout = setTimeout(() => reject(Error('分享图生成超时，请重试')),30000); }),
        ]).finally(() => clearTimeout(renderTimeout));
        setStage(3,'长图绘制完成，正在导出 PNG 图片…');
        await paint();
        const blob = await new Promise(resolve => canvas.toBlob(resolve,'image/png'));
        if (!blob) throw Error('图片生成失败，请重试');
        if (!dialog.open) return;
        if (imageUrl) URL.revokeObjectURL(imageUrl);
        imageBlob = blob; imageUrl = URL.createObjectURL(blob);
        const preview = document.createElement('img'); preview.src = imageUrl; preview.alt = '阵容分享图';
        dialog.querySelector('.run-share-preview').replaceChildren(preview);
        download.href = imageUrl; download.hidden = false;
        copy.hidden = !(navigator.clipboard?.write && window.ClipboardItem);
        steps.forEach(step=>{step.dataset.state='done';});stage.textContent='4/4 步已完成';
        bar.max=1;bar.value=1;progress.dataset.state='done';
        finish('share_generated',failed?'missing_art':'complete');
        status.textContent = failed ? `分享图已生成，${failed} 张卡图使用占位，可重新生成重试。` : '分享图已生成，可下载或长按保存。';
      } catch (error) {
        finish('share_error',['prepare','art','render','encode'][activeStage]);
        if (dialog.open) {
          progress.dataset.state='failed';bar.hidden=true;
          steps.find(step=>step.dataset.state==='active')?.setAttribute('data-state','failed');
          stage.textContent='生成未完成，可重试';status.textContent=error.message || '生成失败，请重试';
        }
      } finally {
        clearInterval(timer);elapsed.textContent=`已用时 ${Math.floor((performance.now()-started)/1000)} 秒`;
        host?.remove();generate.disabled=false;generate.textContent='重新生成分享图';
      }
    };
  }

  document.addEventListener('click',event => {
    const toggle = event.target.closest('.run-skills-toggle');
    if (toggle) {
      const list = toggle.parentElement.querySelector('.run-skills-list');
      const expanded = list.classList.toggle('is-expanded');
      toggle.setAttribute('aria-expanded',String(expanded));
      toggle.textContent = expanded ? '收起技能' : `展开全部技能（共 ${list.children.length} 个）`;
      updateSkills();
    }
    const button = event.target.closest('[data-run-share]');
    if (button && runs.has(button.dataset.runShare)) openShare(runs.get(button.dataset.runShare));
  });
  document.addEventListener('error',event => {
    if (event.target.matches?.('.run-art img')) event.target.parentElement.classList.add('is-missing');
    if (event.target.matches?.('.run-screenshot img')) {
      event.target.hidden = true; event.target.nextElementSibling.hidden = false;
    }
  },true);
  document.addEventListener('DOMContentLoaded',() => {
    const target = document.getElementById('runs-result');
    if (target && window.ResizeObserver) new ResizeObserver(updateSkills).observe(target);
  });
  window.BazaarRuns = {renderRows,querySummary,loadRun,cancelDetail,openShareCard};
})();
