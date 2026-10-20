const assert=require('node:assert/strict');
const {readFileSync,mkdirSync}=require('node:fs');
const path=require('node:path');
const {test}=require('node:test');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'../web_runs/static');
const malicious='<img src=x onerror="window.attacked=true">';

async function setup(t,hash='') {
  const browser=await chromium.launch({headless:true,channel:'chromium'});
  t.after(()=>browser.close());
  const page=await browser.newPage({viewport:{width:1280,height:1000}});
  page.setDefaultTimeout(8000);
  const requests=[],errors=[];
  let fail=false,delay=0;
  page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/*',async route=>{
    const url=new URL(route.request().url());
    assert.equal(url.origin,'http://stats.test');
    if(url.pathname==='/api/stats/search') {
      requests.push(url);
      const oldDelay=delay;
      if(oldDelay) await new Promise(resolve=>setTimeout(resolve,oldDelay));
      if(fail) return route.fulfill({status:503,json:{error:'unavailable'}});
      const offset=Number(url.searchParams.get('offset')||0);
      const rows=Array.from({length:offset?1:20},(_,i)=>({key:'key_'+(offset+i),label:i===0&&!offset?malicious:`条件 ${offset+i}`,searches:12,visitors:7,empty_rate:25,outcomes:{success:9,empty:3},features:{runs:9,partner:3}}));
      const detail=url.searchParams.has('item')?{...rows[0],sources:{network:8,browser_cache:4},daily:[{date:'2026-10-16',count:12}],conditions:[{label:malicious,count:12}],result_counts:{min:0,max:80}}:null;
      return route.fulfill({json:{version:2,tracking_since:'2026-10-16T01:00:00Z',rows,total:21,detail,summary:{searches:252,visitors:18}}});
    }
    if(url.pathname==='/api/stats/overview') return route.fulfill({json:{period_days:Number(url.searchParams.get('days')),total_calls:0,total_uv:0,api_dist:[],daily:[],feature_usage:[],feature_daily:[],module_usage:[],tab_pv:[],hot_cards:[{card:'旧数据',cnt:20}],fb_actions:{}}});
    if(url.pathname.startsWith('/api/')) return route.fulfill({status:503,json:{error:'not in fixture'}});
    const file=url.pathname==='/admin/stats'?'stats.html':url.pathname.startsWith('/static/')?url.pathname.slice(8):null;
    if(!file) return route.fulfill({status:404,body:''});
    return route.fulfill({body:readFileSync(path.join(root,file)),contentType:file.endsWith('.js')?'application/javascript':file.endsWith('.css')?'text/css':'text/html'});
  });
  await page.goto('http://stats.test/admin/stats'+hash);
  await page.locator('#overview-kpis .kpi-value').first().waitFor({state:'attached'});
  return {page,requests,errors,setFail:value=>{fail=value;},setDelay:value=>{delay=value;}};
}

test('search rankings load lazily, paginate all items, share periods and escape detail content',async t=>{
  const {page,requests,errors}=await setup(t);
  assert.equal(requests.length,0);
  await page.locator('[data-admin-tab=features]').click();
  await page.locator('[data-search-item=key_0]').waitFor();
  assert.equal(await page.locator('#search-ranking-body tr').count(),20);
  assert.equal(await page.locator('#search-ranking-body img').count(),0);
  assert.equal(await page.locator('[data-search-item=key_0]').textContent(),malicious);
  assert.match(await page.locator('#search-coverage').textContent(),/252 次主动搜索、18 位独立访客/);
  await page.locator('[data-search-item=key_0]').click();
  await page.locator('#search-detail-close').waitFor();
  assert.match(await page.locator('#search-detail').textContent(),/浏览器缓存4/);
  assert.match(await page.locator('#search-detail').textContent(),/返回样本数范围：0–80/);
  assert.equal(await page.locator('#search-detail img').count(),0);
  assert.equal(await page.evaluate(()=>window.attacked),undefined);
  await page.locator('#search-next').click();
  await page.locator('[data-search-item=key_20]').waitFor();
  assert.match(await page.locator('#search-page-info').textContent(),/21–21 项，共 21 项/);
  assert.equal(await page.locator('#search-next').isDisabled(),true);
  await page.locator('#search-prev').click();
  await page.locator('[data-search-item=key_0]').waitFor();
  await page.locator('[data-days="30"]').click();
  await page.waitForFunction(()=>document.querySelector('#update-time').textContent.includes('近 30 天'));
  await page.locator('[data-search-item=key_0]').waitFor();
  assert.equal(requests.at(-1).searchParams.get('days'),'30');
  assert.equal(requests.at(-1).searchParams.get('offset'),'0');
  assert.match(await page.locator('#hot-cards-body').textContent(),/旧数据/);
  assert.deepEqual(errors,[]);
});

test('all four entry filters, three boards and advanced conditions survive resets',async t=>{
  const {page,requests,errors}=await setup(t,'#features');
  await page.locator('[data-search-item=key_0]').waitFor();
  for(const feature of ['runs','comp_card','winrate','partner']) {
    await page.locator('#search-feature').selectOption(feature);
    await page.locator('[data-search-item=key_0]').waitFor();
    assert.equal(requests.at(-1).searchParams.get('feature'),feature);
  }
  await page.locator('#search-board').selectOption('conditions');
  await page.waitForFunction(()=>document.querySelector('#search-item-heading').textContent.startsWith('完整条件'));
  await page.locator('#search-board').selectOption('empty');
  await page.waitForFunction(()=>document.querySelector('#search-status').textContent.includes('至少 5 次'));
  await page.locator('#search-advanced>summary').click();
  await page.locator('#search-hero').selectOption('Vanessa');
  await page.locator('#search-rank').selectOption('legendary');
  await page.locator('#search-day').fill('3');
  await page.locator('#search-day').dispatchEvent('change');
  await page.locator('#search-enchantment').selectOption('Fiery');
  await page.locator('#search-combos').check();
  await page.locator('[data-search-item=key_0]').waitFor();
  const params=requests.at(-1).searchParams;
  for(const [key,value] of Object.entries({hero:'Vanessa',rank:'legendary',day:'3',enchantment:'Fiery',combos:'1',board:'empty',feature:'partner'})) assert.equal(params.get(key),value);
  assert.deepEqual(errors,[]);
});

test('ranking failures are recoverable; mobile and both themes keep the page within the viewport',async t=>{
  const {page,errors,setFail}=await setup(t,'#features');
  await page.locator('[data-search-item=key_0]').waitFor();
  setFail(true);
  await page.locator('#search-refresh').click();
  await page.waitForFunction(()=>document.querySelector('#search-status').textContent.includes('加载失败'));
  assert.equal(await page.locator('#search-next').isDisabled(),true);
  assert.equal(await page.locator('#search-ranking-body tr').count(),0);
  setFail(false);
  await page.locator('#search-refresh').click();
  await page.locator('[data-search-item=key_0]').waitFor();
  await page.locator('[data-search-item=key_0]').click();
  await page.locator('#search-detail-close').waitFor();
  if(process.env.STATS_SEARCH_PREVIEW_DIR) await page.evaluate(()=>{
    const names=['悠悠球','獠牙','火药桶','灯塔','火炮阵列','望远镜','皮皮虾','装甲核心','钻石','棱镜','回旋镖','藏宝图','风洞装置','食人鱼','赛博铁尺','牌桌','双龙','护盾核心','红色小圆猪','匕首'];
    document.querySelectorAll('.search-item-button').forEach((button,index)=>{button.textContent=names[index];});
    document.querySelector('#search-detail-title').textContent='悠悠球';
    document.querySelector('.search-detail-grid>div:last-child li span').textContent='阵容列表 · 悠悠球（炽焰） · 瓦内莎 · 传奇 · 查询最近 7 天 · 至少 10 胜 · 赛段 18.2';
  });
  for(const theme of ['dark','light']) {
    await page.getByRole('button',{name:theme==='light'?'明亮主题':'暗色主题',exact:true}).click();
    if(process.env.STATS_SEARCH_PREVIEW_DIR) {
      await page.locator('.bz-query-progress').waitFor({state:'hidden'});
      mkdirSync(process.env.STATS_SEARCH_PREVIEW_DIR,{recursive:true});
      await page.locator('.main').screenshot({path:path.join(process.env.STATS_SEARCH_PREVIEW_DIR,`search-${theme}.png`)});
    }
  }
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  assert.equal(await page.locator('#search-advanced').getAttribute('open'),null);
  if(process.env.STATS_SEARCH_PREVIEW_DIR) await page.locator('.main').screenshot({path:path.join(process.env.STATS_SEARCH_PREVIEW_DIR,'search-mobile.png')});
  assert.deepEqual(errors,[]);
});

test('a late detail or old filter response cannot overwrite the new selection',async t=>{
  const {page,requests,errors,setDelay}=await setup(t,'#features');
  await page.locator('[data-search-item=key_0]').waitFor();
  setDelay(250);
  await page.locator('[data-search-item=key_0]').click();
  await page.locator('#search-feature').selectOption('winrate');
  setDelay(0);
  await page.locator('#search-feature').selectOption('partner');
  await page.locator('[data-search-item=key_0]').waitFor();
  await page.waitForTimeout(300);
  assert.equal(await page.locator('#search-detail').isVisible(),false);
  assert.equal(requests.at(-1).searchParams.get('feature'),'partner');
  assert.deepEqual(errors,[]);
});
