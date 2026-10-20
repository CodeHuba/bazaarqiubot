const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync,mkdirSync}=require('node:fs');
const path=require('node:path');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'../web_runs/static');
const output=path.resolve(__dirname,'../.tmp/comp-tracking-release');
const report={tracking_since:'2026-10-19T13:00:00Z',queries:{count:12,visitors:7,result_rate:.5,item_counts:{1:4,2:8},outcomes:{success:6,insufficient:6}},
  actions:['view','sort','examples_open','share_generate','share_generated','share_error','share_cancel','share_download'].map(action=>({action,label:action==='view'?'<img src=x onerror="window.attacked=true">':action,count:action==='share_generate'?4:2,visitors:1,avg_ms:2200})),
  requests:{count:16,avg_ms:120,errors:1},sorts:[{detail:'rate',count:3}],issues:[{action:'share_error',detail:'render',count:1},{action:'share_generated',detail:'missing_art',count:1}]};
async function setup(t){
  const browser=await chromium.launch({headless:true,channel:'chrome'});t.after(()=>browser.close());
  const page=await browser.newPage({viewport:{width:1280,height:1000}}),requests=[],errors=[];
  let fail=false,delay=0;
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/*',async route=>{
    const url=new URL(route.request().url());
    if(url.pathname==='/api/stats/comp'){
      requests.push(url);const currentDelay=delay;if(currentDelay)await new Promise(r=>setTimeout(r,currentDelay));
      return route.fulfill({status:fail?503:200,json:report});
    }
    if(url.pathname==='/api/stats/overview')return route.fulfill({json:{period_days:Number(url.searchParams.get('days')),total_calls:0,total_uv:0,api_dist:[],daily:[],feature_usage:[],feature_daily:[],module_usage:[],tab_pv:[],hot_cards:[],fb_actions:{}}});
    if(url.pathname.startsWith('/api/'))return route.fulfill({status:503,json:{error:'fixture'}});
    if(url.origin!=='http://stats.test')return route.abort();
    const name=url.pathname==='/stats'?'stats.html':url.pathname.slice(8);
    return route.fulfill({body:readFileSync(path.join(root,name)),contentType:name.endsWith('.js')?'application/javascript':name.endsWith('.css')?'text/css':'text/html'});
  });
  await page.goto('http://stats.test/stats');
  return {page,requests,errors,setFail:v=>fail=v,setDelay:v=>delay=v};
}
test('comp report loads lazily, follows period changes and recovers from errors on desktop and mobile',async t=>{
  const {page,requests,errors,setFail}=await setup(t);
  assert.equal(requests.length,0);
  await page.locator('[data-admin-tab=features]').click();
  await page.locator('#comp-stats-content .kpi-value').first().waitFor();
  assert.equal(await page.locator('#comp-stats-content .kpi-value').first().textContent(),'12');
  assert.match(await page.locator('#comp-stats-content').textContent(),/0.12 秒|2.20 秒/);
  assert.equal(await page.locator('#comp-stats-content img').count(),0);
  assert.equal(await page.evaluate(()=>window.attacked),undefined);
  await page.locator('[data-days="30"]').click();
  await page.waitForFunction(()=>document.querySelector('#comp-stats-status').textContent.includes('采集起点'));
  assert.equal(requests.at(-1).searchParams.get('days'),'30');
  await page.locator('.bz-query-progress.is-active').waitFor({state:'hidden'});
  mkdirSync(output,{recursive:true});await page.screenshot({path:path.join(output,'admin-comp-desktop.png')});
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await page.screenshot({path:path.join(output,'admin-comp-mobile.png')});
  setFail(true);await page.locator('#comp-stats-refresh').click();
  await page.waitForFunction(()=>document.querySelector('#comp-stats-status').textContent.startsWith('加载失败'));
  assert.equal(await page.locator('#comp-stats-content .kpi-value').count(),0);
  setFail(false);await page.locator('#comp-stats-refresh').click();
  await page.locator('#comp-stats-content .kpi-value').first().waitFor();
  assert.deepEqual(errors,[]);
});

test('a late request cannot mark an old period loaded while the feature panel is hidden',async t=>{
  const {page,requests,errors,setDelay}=await setup(t);
  setDelay(450);
  await page.locator('[data-admin-tab=features]').click();
  await page.locator('[data-admin-tab=monitor]').click();
  await page.locator('[data-days="30"]').click();
  await page.waitForTimeout(700);
  setDelay(0);await page.locator('[data-admin-tab=features]').click();
  await page.locator('#comp-stats-content .kpi-value').first().waitFor();
  assert.equal(requests.at(-1).searchParams.get('days'),'30');
  assert.deepEqual(errors,[]);
});
