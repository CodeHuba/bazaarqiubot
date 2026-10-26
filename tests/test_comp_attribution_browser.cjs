const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');
const root = path.resolve(__dirname, '../web_runs/static');

async function setup(t) {
  const browser = await chromium.launch({headless:true, executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE});
  t.after(() => browser.close());
  const page = await browser.newPage(), requests = [], errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.pathname.startsWith('/api/')) {
      requests.push({path:url.pathname, headers:route.request().headers(), body:route.request().postDataJSON()});
      return route.fulfill({json:{ok:true}});
    }
    if (url.pathname.startsWith('/static/')) return route.fulfill({body:readFileSync(path.join(root,url.pathname.slice(8))),contentType:'application/javascript'});
    return route.fulfill({contentType:'text/html',body:'<!doctype html><script src="/static/acquisition.js"></script><a href="/runs">Runs</a>'});
  });
  return {page, requests, errors};
}

test('campaign survives internal navigation, new campaign starts new visit, API headers remain local', async t => {
  const {page,requests,errors} = await setup(t);
  await page.goto('https://bazaar.test/builds?utm_source=bilibili&utm_medium=video&utm_campaign=weekly&utm_content=intro');
  await page.waitForFunction(() => window.BazaarAcquisition);
  const first = requests.find(r => r.path === '/api/track/visit');
  assert.equal(first.body.source,'bilibili');
  await page.locator('a').click();
  await page.waitForURL('**/runs');
  await page.evaluate(() => fetch('/api/track/search',{method:'POST',body:JSON.stringify({id:'user_action_000001'}),headers:{'Content-Type':'application/json'}}));
  const views = requests.filter(r => r.path === '/api/track/visit');
  assert.equal(views.length,2);
  assert.equal(views[1].body.visit_id,first.body.visit_id);
  assert.equal(views[1].body.source,'bilibili');
  const query = requests.find(r => r.path === '/api/track/search');
  assert.equal(query.headers['x-visit-id'],first.body.visit_id);
  assert.equal(query.headers['x-fingerprint'],first.headers['x-fingerprint']);
  await page.evaluate(() => fetch('https://other.test/api/external',{method:'POST',body:'{}'}).catch(() => {}));
  const external = requests.find(r => r.path === '/api/external');
  assert.equal(external.headers['x-visit-id'],undefined);
  assert.equal(external.headers['x-fingerprint'],undefined);
  await page.goto('https://bazaar.test/builds?utm_source=qq&utm_medium=social');
  await page.evaluate(() => fetch('/api/check',{method:'POST',body:'{}'}));
  const third = requests.filter(r => r.path === '/api/track/visit').at(-1);
  assert.equal(third.body.source,'qq');
  assert.notEqual(third.body.visit_id,first.body.visit_id);
  assert.equal(errors.length,0);
  const shared = await page.evaluate(() => BazaarAcquisition.shareUrl('/runs?run=a%2Bb','run'));
  const url = new URL(shared);
  assert.equal(url.searchParams.get('run'),'a+b');
  assert.equal(url.searchParams.get('utm_source'),'player_share');
  await page.evaluate(() => {
    const value = JSON.parse(sessionStorage.getItem('_bz_visit_v1'));
    value.seen -= 31*60*1000; sessionStorage.setItem('_bz_visit_v1',JSON.stringify(value));
  });
  await page.goto('https://bazaar.test/runs');
  await page.evaluate(() => fetch('/api/check',{method:'POST',body:'{}'}));
  const expired = requests.filter(r => r.path === '/api/track/visit').at(-1);
  assert.notEqual(expired.body.visit_id,third.body.visit_id);
  assert.equal(expired.body.source,'');
});

test('blocked browser storage does not prevent queries', async t => {
  const {page,requests,errors} = await setup(t);
  await page.addInitScript(() => {
    for (const property of ['localStorage','sessionStorage']) Object.defineProperty(window,property,{get(){throw new DOMException('Blocked','SecurityError');}});
  });
  await page.goto('https://bazaar.test/builds');
  await page.evaluate(() => fetch('/api/track/search',{method:'POST',body:'{}'}));
  assert.ok(requests.some(r => r.path === '/api/track/search'));
  assert.equal(errors.length,0);
});

test('admin source report shows query UV and produces distinct campaign links', async t => {
  const {page,errors} = await setup(t);
  await page.route('**/stats', route => route.fulfill({contentType:'text/html',body:readFileSync(path.join(root,'stats.html'))}));
  await page.route('**/api/stats/acquisition?*', route => route.fulfill({json:{
    tracking_since:'2026-10-19T01:00:00',summary:{uv:10,pv:20,query_uv:3,queries:7},
    rows:[{source:'bilibili',medium:'video',campaign:'weekly',content:'intro',landing:'/builds/vanessa',uv:10,pv:20,query_uv:3,query_rate:.3}],
  }}));
  await page.goto('https://bazaar.test/stats');
  await page.locator('#acquisition-body tr').waitFor();
  assert.match(await page.locator('#acquisition-body').textContent(),/B站/);
  assert.match(await page.locator('#acquisition-body').textContent(),/30.0%/);
  await page.locator('#acquisition-panel summary').click();
  await page.locator('[name="landing"]').fill('/builds/vanessa');
  await page.locator('[name="campaign"]').fill('weekly_20261019');
  await page.locator('#campaign-link-form').evaluate(form => form.requestSubmit());
  const generated = new URL(await page.locator('#campaign-link-output').inputValue());
  assert.equal(generated.pathname,'/builds/vanessa');
  assert.equal(generated.searchParams.get('utm_campaign'),'weekly_20261019');
  await page.locator('[name="landing"]').fill('https://evil.test');
  await page.locator('#campaign-link-form').evaluate(form => form.requestSubmit());
  assert.equal(await page.locator('#campaign-link-output').inputValue(),'');
  assert.equal(errors.length,0);
});
