const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync,mkdirSync} = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');
const root = path.resolve(__dirname,'../web_runs/static');
const output = path.resolve(__dirname,'../.tmp/comp-integrated-staging');
const art = '<svg xmlns="http://www.w3.org/2000/svg" width="96" height="96"><rect width="96" height="96" fill="#56a5b9"/><circle cx="48" cy="48" r="24" fill="#efb35e"/></svg>';
function fixture(sparse=false) {
  const card = (cardId,name,type='Item') => ({cardId,name,type,size:'Medium',img:'/art.svg'});
  const anchor = card('anchor','灼烧梭鱼');
  const groups = Array.from({length:2},(_,i)=>({id:`Vanessa:extra-${i}`,hero:'Vanessa',hero_name:'瓦内莎',sample_level:'regular',
    count:40+i,ten_wins:20,rate:20/(40+i),average_wins:7.5,core:[anchor,card(`extra-${i}`,`核心物品${i}`)],
    winning_items:Array.from({length:12},(_,n)=>({...card(`item-${i}-${n}`,`补充物品${n}`),ten_wins:20-n,ten_win_frequency:(20-n)/20})),
    winning_skills:Array.from({length:12},(_,n)=>({...card(`skill-${i}-${n}`,`搭配技能${n}`,'Skill'),ten_wins:20-n,ten_win_frequency:(20-n)/20})),
    examples:Array.from({length:3},(_,j)=>({id:`case-${i}-${j}`,hero:'Vanessa',wins:10,created_at:'2026-10-19T02:00:00Z',
      items:[{...anchor,tier:'Gold',enchantment:'Fiery'},{...card(`actual-${i}-${j}`,`案例物品${i}-${j}`),tier:'Diamond'}],
      skills:Array.from({length:11+j*3},(_,k)=>({...card(`actual-skill-${i}-${j}-${k}`,`案例技能${j}-${k}`,'Skill'),tier:'Silver'}))}))}));
  if(sparse)for(const g of groups)g.examples=[g.examples[0],g.examples[0],{...g.examples[1],wins:8}];
  return {scope:{phase:'19.2',hero:'Vanessa',rank:'legendary',days:7},anchors:[anchor],groups,candidate_count:2,
    baseline:{count:80,rate:.5,average_wins:7.5}};
}
async function setup(t,{width=1280,brokenArt=false,failComponent=false,artDelay=0,sparse=false,trackFailure=false}={}) {
  const browser = await chromium.launch({headless:true,channel:'chrome'});
  t.after(()=>browser.close());
  const page = await browser.newPage({viewport:{width,height:1000},acceptDownloads:true});
  const errors = [],events=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/*',async route=>{
    const url = new URL(route.request().url());
    if(url.origin!=='http://comp.test')return route.abort();
    if(url.pathname==='/api/track/comp'){
      events.push(route.request().postDataJSON());return route.fulfill({status:trackFailure?503:200,json:{ok:!trackFailure}});
    }
    if(url.pathname==='/api/comp/card')return route.fulfill({json:fixture(sparse)});
    if(url.pathname==='/art.svg'){
      if(artDelay)await new Promise(resolve=>setTimeout(resolve,artDelay));
      return route.fulfill({status:brokenArt?404:200,contentType:'image/svg+xml',body:brokenArt?'':art});
    }
    if(url.pathname.startsWith('/api/'))return route.fulfill({json:{}});
    const file=url.pathname==='/runs'?'runs.html':url.pathname.startsWith('/static/')?url.pathname.slice(8):null;
    if(!file)return route.fulfill({status:404,body:''});
    if(failComponent&&file.includes('html2canvas-'))return route.abort();
    return route.fulfill({body:readFileSync(path.join(root,file)),contentType:file.endsWith('.js')?'application/javascript':file.endsWith('.css')?'text/css':'text/html'});
  });
  await page.goto('http://comp.test/runs?mode=comp&cards=灼烧梭鱼&hero=瓦内莎&rank=legendary&days=7&auto=1');
  await page.locator('.comp-analysis-group').first().waitFor();
  return {page,errors,events};
}

test('each group shares its own result scope and link reopens the selected group',async t=>{
  const {page,errors}=await setup(t,{width:390});
  assert.equal(await page.locator('[data-comp-share]').count(),2);
  // Editing controls without querying must not change the already-rendered result's link.
  await page.selectOption('#runs-hero','杜利');
  await page.fill('#runs-days','3');
  await page.locator('[data-comp-share]').nth(1).click();
  const link=await page.locator('.run-share-url').inputValue();
  const params=new URL(link).searchParams;
  assert.equal(params.get('group'),'Vanessa:extra-1');
  assert.equal(params.get('hero'),'瓦内莎');assert.equal(params.get('days'),'7');
  assert.equal(params.get('rank'),'legendary');assert.equal(params.get('mode'),'comp');
  await page.getByRole('button',{name:'复制组合链接',exact:true}).click();
  assert.match(await page.locator('.run-share-status').textContent(),/已复制|已选中/);
  await page.getByRole('button',{name:'关闭分享',exact:true}).click();
  await page.goto(link);
  await page.locator('.comp-shared-group').waitFor();
  assert.equal(await page.locator('.comp-shared-group').getAttribute('data-comp-group'),'Vanessa:extra-1');
  assert.equal(new URL(page.url()).searchParams.get('group'),'Vanessa:extra-1');
  const missing=new URL(link);missing.searchParams.set('group','removed-group');
  await page.goto(missing.href);
  await page.getByRole('status').filter({hasText:'分享的组合暂未进入'}).waitFor();
  assert.equal(await page.locator('.comp-shared-group').count(),0);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  assert.deepEqual(errors,[]);
});

test('PNG includes three complete real cases and reports actual generation stages and art counts',async t=>{
  const {page,errors,events}=await setup(t,{artDelay:100});
  await page.locator('[data-comp-share]').nth(1).click();
  await page.evaluate(()=>{
    window.progressLog=[];
    const panel=document.querySelector('.run-share-progress');
    new MutationObserver(()=>{
      const bar=panel.querySelector('progress');
      window.progressLog.push({stage:panel.querySelector('[data-share-stage]').textContent,state:panel.dataset.state,value:bar.getAttribute('value'),max:bar.max});
    }).observe(panel,{attributes:true,childList:true,subtree:true,characterData:true});
  });
  await page.getByRole('button',{name:'生成分享图',exact:true}).click();
  await page.locator('.comp-share-sheet').waitFor({state:'attached'});
  const sheet=page.locator('.comp-share-sheet');
  assert.match(await sheet.locator('.comp-share-core').textContent(),/核心物品1/);
  assert.equal(await sheet.locator('.comp-share-companions>section').nth(0).locator('.comp-share-card').count(),8);
  assert.equal(await sheet.locator('.comp-share-companions>section').nth(1).locator('.comp-share-card').count(),8);
  assert.doesNotMatch(await sheet.textContent(),/补充物品8|搭配技能8|核心物品0/);
  assert.match(await sheet.locator('.comp-share-metrics').textContent(),/48.8%/);
  assert.match(await sheet.locator('.run-share-qr img').getAttribute('src'),/^data:image\/png;base64,/);
  assert.equal(await sheet.locator('.comp-share-example').count(),3);
  for(let i=0;i<3;i++){
    const example=sheet.locator('.comp-share-example').nth(i);
    assert.equal(await example.getAttribute('data-example-id'),`case-1-${i}`);
    assert.equal(await example.locator('.comp-share-case-items .comp-share-case-card').count(),2);
    assert.equal(await example.locator('.comp-share-case-skills .comp-share-case-card').count(),11+i*3);
    assert.match(await example.textContent(),new RegExp(`案例物品1-${i}`));
    assert.match(await example.textContent(),/炽焰/);
    assert.doesNotMatch(await example.textContent(),/附魔：|品质未知/);
    assert.equal(await example.locator('.tier-diamond').count(),1);
  }
  await page.locator('[data-share-download]:not([hidden])').waitFor({timeout:30000});
  assert.doesNotMatch(await page.locator('.run-share-status').textContent(),/占位/);
  const image=page.locator('.run-share-preview img');await image.evaluate(img=>img.decode());
  assert.equal(await image.evaluate(img=>img.naturalWidth),1600);
  assert.ok(await image.evaluate(img=>img.naturalHeight)>1200);
  const log=await page.evaluate(()=>window.progressLog);
  for(const step of ['1/4','2/4','3/4','4/4'])assert.ok(log.some(p=>p.stage.includes(step)),JSON.stringify(log));
  assert.ok(log.some(p=>p.stage.includes('加载卡图')&&Number(p.value)>0&&Number(p.value)<p.max));
  assert.ok(log.some(p=>p.stage.includes('绘制长图')&&p.value===null));
  assert.equal(await page.locator('.run-share-progress').getAttribute('data-state'),'done');
  assert.match(await page.locator('[data-share-elapsed]').textContent(),/已用时 \d+ 秒/);
  mkdirSync(output,{recursive:true});
  const pending=page.waitForEvent('download');
  const trackedDownload=page.waitForResponse(r=>r.url().endsWith('/api/track/comp')&&r.request().postDataJSON().action==='share_download');
  await page.locator('[data-share-download]').click();
  const download=await pending;await download.saveAs(path.join(output,'comp-share-test.png'));
  await trackedDownload;
  assert.equal(readFileSync(path.join(output,'comp-share-test.png')).subarray(1,4).toString(),'PNG');
  assert.equal(await page.locator('.run-share-render-host').count(),0);
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await page.screenshot({path:path.join(output,'comp-share-test-mobile.png')});
  await page.getByRole('button',{name:'关闭分享',exact:true}).click();
  await page.locator('.run-share-dialog').waitFor({state:'detached'});
  assert.equal(await page.locator('.run-share-dialog').count(),0);
  assert.deepEqual(errors,[]);
  assert.equal(events.filter(e=>e.action==='share_generate').length,1);
  assert.equal(events.filter(e=>e.action==='share_generated').length,1);
  assert.equal(events.find(e=>e.action==='share_generated').detail,'complete');
  assert.ok(events.find(e=>e.action==='share_generated').duration_ms>0);
  assert.ok(events.some(e=>e.action==='share_download'&&e.group==='Vanessa:extra-1'));
  assert.ok(!events.some(e=>e.action==='share_cancel'||e.action==='share_error'));
});

test('art and component failures preserve a usable link and a retry path',async t=>{
  const {page,errors,events}=await setup(t,{brokenArt:true,width:390});
  await page.locator('[data-comp-share]').first().click();
  await page.getByRole('button',{name:'生成分享图',exact:true}).click();
  await page.locator('[data-share-download]:not([hidden])').waitFor({timeout:30000});
  assert.match(await page.locator('.run-share-status').textContent(),/66 张卡图使用占位/);
  assert.ok(new URL(await page.locator('.run-share-url').inputValue()).searchParams.get('group'));
  assert.deepEqual(errors,[]);
  const failed=await setup(t,{failComponent:true});
  await failed.page.locator('[data-comp-share]').first().click();
  await failed.page.getByRole('button',{name:'生成分享图',exact:true}).click();
  await failed.page.getByRole('button',{name:'重新生成分享图',exact:true}).waitFor();
  assert.match(await failed.page.locator('.run-share-status').textContent(),/加载失败/);
  assert.ok(new URL(await failed.page.locator('.run-share-url').inputValue()).searchParams.get('group'));
  assert.equal(await failed.page.locator('.run-share-render-host').count(),0);
  assert.equal(await failed.page.locator('.run-share-progress').getAttribute('data-state'),'failed');
  assert.match(await failed.page.locator('[data-share-stage]').textContent(),/可重试/);
  assert.deepEqual(failed.errors,[]);
  assert.ok(failed.events.some(e=>e.action==='share_error'&&e.detail==='prepare'));
  assert.ok(events.some(e=>e.action==='share_generated'&&e.detail==='missing_art'));
});

test('fewer than three winning cases are shown honestly without duplicates or non-winning fills',async t=>{
  const {page,errors}=await setup(t,{sparse:true});
  await page.locator('[data-comp-share]').first().click();
  await page.getByRole('button',{name:'生成分享图',exact:true}).click();
  await page.locator('.comp-share-sheet').waitFor({state:'attached'});
  assert.equal(await page.locator('.comp-share-example').count(),1);
  assert.equal(await page.locator('.comp-share-example').getAttribute('data-example-id'),'case-0-0');
  assert.match(await page.locator('.comp-share-examples>h3').textContent(),/1 套/);
  await page.locator('[data-share-download]:not([hidden])').waitFor({timeout:30000});
  assert.deepEqual(errors,[]);
});

test('sort, examples, share landing and cancellation report actual actions without adding manual searches',async t=>{
  const {page,events,errors}=await setup(t,{artDelay:500});
  assert.equal(events.filter(e=>e.action==='view').length,1);
  assert.ok(!events.some(e=>e.action==='share_landing'));
  const before=page.waitForRequest(r=>r.url().includes('/api/comp/card'));
  await page.locator('[data-comp-sort=rate]').click();
  assert.equal((await before).headers()['x-search-action'],undefined);
  await page.locator('[data-comp-examples] summary').first().click();
  await page.locator('[data-comp-share]').first().click();
  const link=await page.locator('.run-share-url').inputValue();
  assert.equal(new URL(link).searchParams.get('utm_content'),'comp');
  await page.locator('[data-share-generate]').click();
  await page.locator('.comp-share-sheet').waitFor({state:'attached'});
  const cancelled=page.waitForRequest(r=>r.url().endsWith('/api/track/comp')&&r.postDataJSON().action==='share_cancel');
  await page.locator('[data-share-close]').click();await cancelled;
  assert.ok(events.some(e=>e.action==='sort'&&e.detail==='rate'));
  assert.ok(events.some(e=>e.action==='examples_open'&&e.group==='Vanessa:extra-0'));
  assert.ok(!events.some(e=>e.action==='share_generated'||e.action==='share_error'));
  await page.goto(link);await page.locator('.comp-shared-group').waitFor();
  assert.ok(events.some(e=>e.action==='share_landing'&&e.group==='Vanessa:extra-0'));
  assert.deepEqual(errors,[]);
});

test('tracking outage retries one event with the same id and never interrupts the feature',async t=>{
  const {page,events,errors}=await setup(t,{trackFailure:true});
  await page.waitForFunction(()=>document.querySelectorAll('.comp-analysis-group').length===2);
  await page.waitForTimeout(1300);
  const views=events.filter(e=>e.action==='view');
  assert.equal(views.length,2);assert.equal(views[0].id,views[1].id);
  await page.locator('[data-comp-share]').first().click();
  assert.equal(await page.locator('.run-share-dialog').count(),1);
  assert.deepEqual(errors,[]);
});

test('analysis progress stays through body parsing and closes as soon as results render',async t=>{
  const {page,errors}=await setup(t);
  await page.evaluate(()=>{
    const real=window.fetch;
    window.fetch=async (...args)=>{
      const response=await real(...args);
      if(String(args[0]).includes('/api/comp/card?')){
        const json=response.json.bind(response);
        response.json=async()=>{await new Promise(r=>setTimeout(r,600));return json();};
      }
      return response;
    };
    window.compRenderedAt=0;
    document.getElementById('comp-result').addEventListener('comp-rendered',()=>{window.compRenderedAt=performance.now();},{once:true});
  });
  const response=page.waitForResponse(r=>r.url().includes('/api/comp/card?'));
  await page.evaluate(()=>{void BazaarComp.query('sort');});await response;
  assert.equal(await page.locator('.bz-query-progress').evaluate(e=>e.classList.contains('is-active')),true);
  assert.equal(await page.evaluate(()=>window.compRenderedAt),0);
  await page.waitForFunction(()=>window.compRenderedAt>0&&!document.querySelector('.bz-query-progress').classList.contains('is-active'));
  assert.equal(await page.locator('.bz-query-progress').evaluate(e=>getComputedStyle(e).opacity),'0');
  assert.ok(await page.evaluate(()=>performance.now()-window.compRenderedAt)<200);
  assert.deepEqual(errors,[]);
});

test('an analysis error also clears its manually owned progress overlay',async t=>{
  const {page,errors}=await setup(t);
  await page.route('**/api/comp/card?*',route=>route.fulfill({status:503,json:{error:'测试读取失败'}}));
  await page.evaluate(()=>{void BazaarComp.query('sort');});
  await page.locator('#comp-result .error-msg').waitFor();
  assert.match(await page.locator('#comp-result').textContent(),/测试读取失败/);
  assert.equal(await page.locator('.bz-query-progress').evaluate(e=>getComputedStyle(e).opacity),'0');
  assert.deepEqual(errors,[]);
});
