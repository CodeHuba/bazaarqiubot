// node --test tests/test_card_preview_dom.cjs (requires jsdom@26.1.0).
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {JSDOM} = require('jsdom');

const staticRoot=path.resolve(__dirname,'../web_runs/static');
const code=readFileSync(path.join(staticRoot,'card-preview.js'),'utf8');
const tick=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const card=(id='a',quality='Silver',enchantment='')=>({cardId:id,name:'卡牌 '+id,name_en:id,type:'Item',size:'Medium',quality,
  qualityLabel:quality==='Gold'?'黄金':'白银',enchantment,tags:['工具'],attributes:[{label:'伤害',value:quality==='Gold'?'20':'10'}],
  availableQualities:[{value:'Silver',label:'白银'},{value:'Gold',label:'黄金'}],availableEnchantments:[{value:'Fiery',label:'炽焰'}],
  effects:[{type:'Active',source:'base',text:'造成10伤害'}]});
const ok=data=>({ok:true,json:async()=>data});

function setup(fetch=async url=>{const u=new URL(url,'http://preview.test');return ok(card(u.pathname.split('/').pop(),u.searchParams.get('quality')||'Silver',u.searchParams.get('enchantment')||''));}){
  const dom=new JSDOM('<body><button id="outside">外部操作</button></body>',{url:'http://preview.test/runs',runScripts:'outside-only',pretendToBeVisual:true});
  const w=dom.window;w.fetch=fetch;w.AbortController=AbortController;w.eval(code);
  const trigger=(id,extra={})=>{
    const host=w.document.createElement('div');host.innerHTML=`<span ${w.BazaarCardPreview.attrs({cardId:id,name:'卡牌 '+id,...extra})}>卡图</span>`;
    w.document.body.appendChild(host);return host.firstElementChild;
  };
  return {dom,w,d:w.document,trigger,panel:()=>w.document.querySelector('#bazaar-card-preview')};
}

test('hover delay, moving into the panel, panel scrolling and Escape preserve readable content',async()=>{
  const s=setup();try{
    const target=s.trigger('a');
    target.dispatchEvent(new s.w.MouseEvent('pointerover',{bubbles:true}));
    assert.equal(s.panel(),null);
    await tick(230);
    assert.equal(s.panel().hidden,false);
    assert.equal(s.panel().querySelector('[data-bcp-quality]').disabled,false);
    assert.equal(s.panel().querySelector('[data-bcp-enchantment]').disabled,false);
    target.dispatchEvent(new s.w.MouseEvent('pointerout',{bubbles:true,relatedTarget:s.panel()}));
    s.panel().dispatchEvent(new s.w.MouseEvent('pointerenter'));
    await tick(190);
    assert.equal(s.panel().hidden,false);
    s.panel().dispatchEvent(new s.w.Event('scroll'));
    assert.equal(s.panel().hidden,false);
    target.dispatchEvent(new s.w.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
    assert.equal(s.panel().hidden,true);
  }finally{s.dom.window.close();}
});

test('hover previews switch quality and enchantment directly without using the pin button',async()=>{
  const s=setup();try{
    const target=s.trigger('a');
    target.dispatchEvent(new s.w.MouseEvent('pointerover',{bubbles:true}));await tick(230);
    assert.equal(s.panel().querySelector('[data-bcp-pin]'),null);
    assert.doesNotMatch(s.panel().textContent,/固定查看|当前游戏数据|不包含本局成长|此处未记录品质/);
    const quality=s.panel().querySelector('[data-bcp-quality]');quality.focus();quality.value='Gold';
    quality.dispatchEvent(new s.w.Event('change',{bubbles:true}));await tick(0);
    assert.equal(s.panel().querySelector('[data-bcp-quality]').value,'Gold');
    const enchantment=s.panel().querySelector('[data-bcp-enchantment]');enchantment.focus();enchantment.value='Fiery';
    enchantment.dispatchEvent(new s.w.Event('change',{bubbles:true}));await tick(0);
    assert.equal(s.panel().querySelector('[data-bcp-quality]').value,'Gold');
    assert.equal(s.panel().querySelector('[data-bcp-enchantment]').value,'Fiery');
    s.panel().dispatchEvent(new s.w.MouseEvent('pointerleave'));await tick(190);
    assert.equal(s.panel().hidden,false);
  }finally{s.dom.window.close();}
});

test('late A response cannot overwrite B, and concurrent requests for the same card are shared',async()=>{
  const requests=[];const s=setup(url=>new Promise(resolve=>requests.push({url,resolve})));
  try{
    const a=s.trigger('a'),b=s.trigger('b'),anotherB=s.trigger('b');
    a.focus();b.focus();anotherB.focus();
    assert.equal(requests.length,2);
    requests[1].resolve(ok(card('b')));await tick(0);
    assert.equal(s.panel().querySelector('h2').textContent,'卡牌 b');
    requests[0].resolve(ok(card('a')));await tick(0);
    assert.equal(s.panel().querySelector('h2').textContent,'卡牌 b');
  }finally{s.dom.window.close();}
});

test('known quality/enchantment is requested, selectors refresh and keep keyboard focus, close restores focus once',async()=>{
  const requests=[];const s=setup(async url=>{requests.push(url);const u=new URL(url,'http://preview.test');return ok(card('a',u.searchParams.get('quality')||'Silver',u.searchParams.get('enchantment')||''));});
  try{
    const a=s.trigger('a',{quality:'Gold',enchantment:'Fiery'});a.click();await tick(0);
    assert.match(requests[0],/quality=Gold/);assert.match(requests[0],/enchantment=Fiery/);
    let quality=s.panel().querySelector('[data-bcp-quality]');assert.equal(quality.disabled,false);
    quality.focus();quality.value='Silver';quality.dispatchEvent(new s.w.Event('change',{bubbles:true}));await tick(0);
    assert.equal(s.d.activeElement,s.panel().querySelector('[data-bcp-quality]'));
    assert.equal(s.panel().querySelector('[data-bcp-quality]').value,'Silver');
    s.panel().querySelector('[data-bcp-close]').click();
    assert.equal(s.panel().hidden,true);assert.equal(s.d.activeElement,a);
    assert.equal(a.hasAttribute('aria-expanded'),false);
  }finally{s.dom.window.close();}
});

test('candidate click keeps existing selection and T-table statistics remain alongside effects',async()=>{
  const s=setup();try{
    let selected=0;
    const attributes=s.w.BazaarCardPreview.attrs({cardId:'a',name:'卡牌 a'},true);
    const holder=s.d.createElement('div');holder.innerHTML=`<button ${attributes}>选择卡牌</button>`;s.d.body.appendChild(holder);
    const candidate=holder.firstElementChild;candidate.addEventListener('click',()=>selected++);candidate.focus();candidate.click();await tick(0);
    assert.equal(selected,1);assert.equal(s.panel().hidden,true);
    const modal=s.d.createElement('div');modal.className='tier-modal';modal.innerHTML='<div class="tier-modal-box"><button class="tier-modal-close">关闭</button><p id="stats">10胜率60%</p></div>';s.d.body.appendChild(modal);
    s.w.BazaarCardPreview.mount(modal.firstElementChild,{cardId:'a',name:'卡牌 a'});await tick(0);
    assert.equal(s.d.querySelector('#stats').textContent,'10胜率60%');
    assert.equal(modal.querySelector('.bcp-inline h2').textContent,'卡牌 a');
    modal.querySelector('button').dispatchEvent(new s.w.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
    assert.equal(modal.isConnected,false);assert.equal(s.d.activeElement,candidate);assert.equal(s.panel().hidden,true);
  }finally{s.dom.window.close();}
});

test('failed requests can retry, markup is escaped and removing a trigger closes a stale preview',async()=>{
  let calls=0;const s=setup(async()=>{if(!calls++)throw Error('offline');return ok({...card('a'),name:'<img src=x onerror=alert(1)>',effects:[{type:'Active',source:'base',text:"<script>bad()</script>This item's 10 Damage"}]});});
  try{
    const a=s.trigger('a');a.click();await tick(0);
    s.panel().querySelector('[data-bcp-retry]').click();await tick(0);
    assert.equal(s.panel().querySelector('script'),null);assert.equal(s.panel().querySelector('h2 img'),null);
    assert.match(s.panel().querySelector('h2').textContent,/<img/);
    assert.equal(s.panel().querySelector('mark').textContent,'10');
    assert.match(s.panel().querySelector('.bcp-effect').textContent,/item's 10 Damage/);
    a.remove();await tick(0);assert.equal(s.panel().hidden,true);
  }finally{s.dom.window.close();}
});

test('all four actual page scripts compile and load the shared module before page rendering',()=>{
  for(const page of ['runs','topcard','winrate','partner']){
    const html=readFileSync(path.join(staticRoot,page+'.html'),'utf8');
    const dom=new JSDOM(html);
    assert.equal(dom.window.document.querySelectorAll('script[src^="/static/card-preview.js"]').length,1);
    assert.equal(dom.window.document.querySelectorAll('link[href^="/static/card-preview.css"]').length,1);
    for(const script of dom.window.document.querySelectorAll('script:not([src])'))new vm.Script(script.textContent,{filename:page+'.html'});
    dom.window.close();
  }
});
