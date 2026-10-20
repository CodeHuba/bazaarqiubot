// Run with: node --test tests/test_stats_dashboard_browser.cjs (requires playwright).
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const { chromium } = require('playwright');
const staticRoot = path.resolve(__dirname, '../web_runs/static');

const featureRows = ['runs_query','topcard_query','comp_query','comp_card_query','hero_overview','card_tier_query'].map((feature,index) => ({
  feature,uses:[100,80,70,60,50,40][index],uv:7,successes:[95,80,65,50,50,40][index],empty_results:0,errors:index===2?5:0,
}));
const historic = {feature:'routes_query',uses:2000,uv:40,successes:1900,empty_results:100,errors:0};
function overview(days = 7) {
  return { period_days:days,total_calls:100,total_uv:7,
    api_dist:[{endpoint:'/api/runs',cnt:69,avg_ms:14,errors:1},{endpoint:'/api/partner',cnt:31,avg_ms:1000,errors:0}],
    daily:[{day:'2026-10-14',cnt:70,uv:7},{day:'2026-10-15',cnt:30,uv:3}],
    feature_usage:[...featureRows,{feature:'support_page_view',uses:6,uv:2,successes:6,empty_results:0,errors:0},historic],
    feature_daily:[...featureRows.map(row => ({day:'2026-10-15',feature:row.feature,count:row.uses})),{day:'2026-10-15',feature:historic.feature,count:2000}],
    module_usage:[{module:'runs',uses:160,uv:7},{module:'topcard',uses:240,uv:7},{module:'routes',uses:2000,uv:40}],
    tab_pv:[{tab:'runs',cnt:21},{tab:'topcard',cnt:3}],hot_cards:[{card:'火药桶',cnt:10}],fb_actions:{submit:3,like:9,comment:6},
  };
}
function intraday(date = '2026-10-15') {
  const period = () => ({ available:{core:true,pv:true,api:true},all:{summary:{uses:40,uv:7,pv:24,api_calls:100},
    hours:Array.from({length:24},(_,hour) => ({hour,state:'complete',uses:hour===12?40:0,uv:hour===12?7:0,pv:hour===12?24:0,api_calls:hour===12?100:0,successes:hour===12?39:0,empty_results:0,errors:hour===12?1:0}))},modules:{} });
  return {date,today:'2026-10-15',min_date:'2026-07-18',previous_date:'2026-10-14',is_today:date==='2026-10-15',cutoff_time:'15:00:00',generated_at:'2026-10-15T15:00:00+08:00',modules:[],current:period(),previous:period()};
}
async function setup(hash = '') {
  const browser = await chromium.launch({headless:true,channel:'chromium'});
  const page = await browser.newPage({viewport:{width:1280,height:1000}});
  page.setDefaultTimeout(8000);
  const errors = [], requests = [];
  page.on('pageerror',error => errors.push(error.message));
  await page.route('**/*',route => {
    const url = new URL(route.request().url());
    assert.equal(url.origin,'http://stats.test');
    if (url.pathname.startsWith('/api/')) requests.push({path:url.pathname,method:route.request().method()});
    if (url.pathname==='/api/stats/overview') return route.fulfill({json:overview(Number(url.searchParams.get('days')))});
    if (url.pathname==='/api/stats/search') return route.fulfill({json:{version:2,rows:[],total:0,tracking_since:'2026-10-16T00:00:00Z'}});
    if (url.pathname==='/api/stats/comp') return route.fulfill({json:{tracking_since:'2026-10-19T00:00:00Z',queries:{count:0,visitors:0,outcomes:{},item_counts:{},result_rate:null},actions:['share_generate','share_generated','share_error','share_cancel'].map(action=>({action,label:action,count:0,visitors:0,avg_ms:null})),requests:{count:0,errors:0,avg_ms:null},sorts:[],issues:[]}});
    if (url.pathname==='/api/stats/intraday') return route.fulfill({json:intraday(url.searchParams.get('date') || undefined)});
    if (url.pathname.startsWith('/api/admin/')) return route.fulfill({json:{items:[],total:0,page:1,pages:1,supporters:[],trivia:[],announcements:[]}});
    const relative = url.pathname==='/admin/stats'?'stats.html':url.pathname.startsWith('/static/')?url.pathname.slice(8):null;
    if (!relative) return route.fulfill({status:404,body:''});
    return route.fulfill({body:readFileSync(path.join(staticRoot,relative)),contentType:relative.endsWith('.js')?'application/javascript':relative.endsWith('.css')?'text/css':'text/html'});
  });
  await page.goto('http://stats.test/admin/stats'+hash);
  await page.waitForFunction(() => document.querySelector('#overview-kpis .kpi-value')?.textContent==='24');
  return {browser,page,errors,requests};
}

test('compact overview preserves metrics, all drilldowns, state and management tools',async () => {
  const {browser,page,errors,requests} = await setup();
  try {
    assert.deepEqual(await page.locator('#overview-kpis .kpi-value').allTextContents(),['24','7','400','1.0%']);
    assert.equal(await page.locator('#overview-top-body tr').count(),5);
    assert.equal(await page.locator('#overview-top-body').textContent().then(s => s.includes('阵容路线')),false);
    assert.equal(await page.locator('[data-admin-panel]:visible').count(),1);
    const readCount=requests.length;
    await page.locator('[data-admin-tab="features"]').click();
    assert.equal(await page.locator('#feature-usage-body tr').count(),6);
    assert.equal(await page.locator('#feature-usage-body .feature-description').first().isVisible(),false);
    await page.locator('#feature-details-toggle').click();
    await page.locator('#feature-retired-section > summary').click();
    assert.equal(await page.locator('#feature-retired-body [data-feature="routes_query"] td').nth(1).textContent(),'2,000');
    assert.equal(await page.locator('#module-usage-list .tab-bar-count').allTextContents().then(v => v.join(',')),'160,240,2,000');
    await page.locator('[data-admin-tab="monitor"]').click();
    assert.deepEqual(await page.locator('#monitor-kpis .kpi-value').allTextContents(),['100','7','507 ms','1']);
    assert.equal(await page.locator('#api-detail-body tr').count(),2);
    await page.locator('[data-admin-tab="features"]').click();
    assert.equal(await page.locator('#feature-details-toggle').getAttribute('aria-pressed'),'true');
    assert.equal(await page.locator('#feature-retired-section').getAttribute('open'),'');
    assert.equal(requests.filter(row=>!['/api/stats/search','/api/stats/comp'].includes(row.path)).length,readCount,'switching panels only lazily reads search and composition statistics; it does not reload other metrics or create telemetry');
    assert.equal(requests.filter(row=>row.path==='/api/stats/comp').length,1);
    await page.locator('[data-days="30"]').click();
    await page.waitForFunction(()=>document.querySelector('#update-time').textContent.includes('近 30 天'));
    assert.equal(await page.locator('#feature-details-toggle').getAttribute('aria-pressed'),'true');
    assert.equal(await page.locator('#feature-retired-section').getAttribute('open'),'');
    assert.equal(await page.locator('#panel-features').isVisible(),true);
    await page.locator('[data-days="7"]').click();
    await page.waitForFunction(()=>document.querySelector('#update-time').textContent.includes('近 7 天'));
    await page.locator('[data-admin-tab="manage"]').click();
    assert.deepEqual(await page.locator('.feedback-counts span').allTextContents(),['3','9','6']);
    assert.equal(await page.locator('a[href="/admin/zaza"]').count(),1);
    for (const [label,id] of [['管理意见反馈','feedback-admin-modal'],['管理冷知识','trivia-modal'],['管理公告','announcement-modal'],['管理赞赏者','supporters-modal']]) {
      await page.getByRole('button',{name:label,exact:true}).click();
      assert.equal(await page.locator('#'+id).isVisible(),true);
      await page.locator('#'+id+' .modal-close').click();
      assert.equal(await page.locator('#'+id).isVisible(),false);
    }
    assert.equal(requests.some(row=>row.method!=='GET'),false,'navigation and previews must not mutate data or add analytics');
    await page.locator('[data-admin-tab="overview"]').click();
    await page.locator('.bz-query-progress').waitFor({state:'hidden'});
    await page.evaluate(()=>Object.values(Chart.instances).forEach(chart=>{chart.stop();chart.update('none');}));
    if (process.env.STATS_DASHBOARD_PREVIEW_DIR) await page.locator('.main').screenshot({path:path.join(process.env.STATS_DASHBOARD_PREVIEW_DIR,'overview-dark.png')});
    await page.getByRole('button',{name:'明亮主题',exact:true}).click();
    await page.evaluate(()=>Object.values(Chart.instances).forEach(chart=>{chart.stop();chart.update('none');}));
    if (process.env.STATS_DASHBOARD_PREVIEW_DIR) await page.locator('.main').screenshot({path:path.join(process.env.STATS_DASHBOARD_PREVIEW_DIR,'overview-light.png')});
    await page.setViewportSize({width:390,height:844});
    await page.waitForFunction(() => Chart.getChart('trend-chart').width<390);
    await page.evaluate(()=>Object.values(Chart.instances).forEach(chart=>{chart.stop();chart.update('none');}));
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth<=innerWidth),true);
    if (process.env.STATS_DASHBOARD_PREVIEW_DIR) await page.locator('.main').screenshot({path:path.join(process.env.STATS_DASHBOARD_PREVIEW_DIR,'overview-mobile.png')});
    for (const panel of ['features','monitor','manage']) {
      await page.locator(`[data-admin-tab="${panel}"]`).click();
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth<=innerWidth),true,panel+' mobile overflow');
    }
    assert.deepEqual(errors,[]);
  } finally { await browser.close(); }
});

test('trend metric switching, readable data fallback and retained data on API failure',async () => {
  const {browser,page,errors} = await setup();
  try {
    await page.locator('#daily-metric').selectOption('uses');
    assert.deepEqual(await page.evaluate(() => Chart.getChart('trend-chart').data.datasets[0].data),[0,400]);
    await page.locator('#daily-metric').selectOption('uv');
    assert.deepEqual(await page.evaluate(() => Chart.getChart('trend-chart').data.datasets[0].data),[7,3]);
    await page.locator('[data-trend-view="hourly"]').click();
    assert.equal(await page.locator('#trend-scope').textContent(),'独立日期 · 北京时间');
    await page.locator('#intraday-date').fill('2026-10-14');
    await page.locator('#intraday-date').dispatchEvent('change');
    await page.waitForFunction(() => document.querySelector('#intraday-total-note').textContent.includes('2026-10-14'));
    await page.locator('[data-trend-view="daily"]').click();
    await page.locator('[data-trend-view="hourly"]').click();
    assert.equal(await page.locator('#intraday-date').inputValue(),'2026-10-14');
    await page.route('**/api/stats/overview?*',route=>route.fulfill({status:500,json:{error:'unavailable'}}));
    await page.evaluate(()=>loadStats());
    assert.match(await page.locator('#main-content').textContent(),/保留上次近 7 天的数据/);
    assert.deepEqual(await page.locator('#overview-kpis .kpi-value').allTextContents(),['24','7','400','1.0%']);
    await page.locator('[data-admin-tab="monitor"]').click();
    assert.equal(await page.locator('#api-detail-body tr').count(),2);
    assert.deepEqual(errors,[]);
  } finally { await browser.close(); }
});

test('late period response cannot overwrite a newer selection; hash and keyboard navigation work',async () => {
  const {browser,page,errors}=await setup('#features');
  let release;
  try {
    assert.equal(await page.locator('#panel-features').isVisible(),true);
    await page.locator('#tab-features').focus();
    await page.keyboard.press('ArrowRight');
    assert.equal(await page.locator('#panel-monitor').isVisible(),true);
    assert.match(page.url(),/#monitor$/);
    let started;
    const received=new Promise(resolve=>{started=resolve;});
    const held=new Promise(resolve=>{release=resolve;});
    await page.route('**/api/stats/overview?days=30',async route=>{started();await held;await route.fulfill({json:{...overview(30),total_uv:999}}).catch(()=>{});});
    await page.locator('[data-days="30"]').click();
    await received;
    await page.locator('[data-days="90"]').click();
    await page.waitForFunction(()=>document.querySelector('#update-time').textContent.includes('近 90 天'));
    release();
    await page.evaluate(()=>loadStats());
    assert.deepEqual(await page.locator('#overview-kpis .kpi-value').allTextContents(),['24','7','400','1.0%']);
    assert.equal(await page.locator('[data-days="90"]').getAttribute('class'),'day-btn active');
    assert.equal(await page.locator('#panel-monitor').isVisible(),true);
    assert.deepEqual(errors,[]);
  } finally { release?.();await browser.close(); }
});
