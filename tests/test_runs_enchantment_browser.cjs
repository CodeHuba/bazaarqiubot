// NODE_PATH must include playwright; optionally set PLAYWRIGHT_CHROMIUM_EXECUTABLE.
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const { chromium } = require('playwright');
const staticRoot = path.resolve(__dirname, '../web_runs/static');

async function setup(t, width = 1280) {
  const browser = await chromium.launch({headless: true,
    ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE
      ? {executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE} : {channel: 'chromium'}),
  });
  t.after(() => browser.close());
  const page = await browser.newPage({viewport: {width, height: 900}});
  const requests = [], errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin !== 'http://runs.test') return route.abort();
    if (url.pathname === '/api/card_search') {
      const card = url.searchParams.get('q').includes('獠')
        ? {zh: '獠牙', en: 'Fang'} : {zh: '悠悠球', en: 'Yo-Yo'};
      return route.fulfill({json: [card]});
    }
    if (url.pathname === '/api/runs') {
      requests.push(url);
      const cards = Array.from({length: 10}, (_, i) => ({cardId: 'card-' + i, name: '其他卡' + i}));
      cards[9] = {cardId: 'yo-yo', name: '悠悠球', enchantment: 'Fiery', enchantment_zh: '炽焰', matched: true};
      return route.fulfill({json: {total: 83, pages: 17, page: Math.min(17, Number(url.searchParams.get('page')) || 1),
        runs: [{id: 'run-1', hero: 'Vanessa', username: 'tester', created_at: '2026-10-15T03:00:00Z',
          wins: 10, losses: 2, cards, url: 'https://bazaardb.gg/run/tracker/run-1'}]}});
    }
    if (url.pathname.startsWith('/api/')) return route.fulfill({json: {}});
    const file = url.pathname === '/runs' ? 'runs.html'
      : url.pathname.startsWith('/static/') ? url.pathname.slice(8) : null;
    if (!file) return route.fulfill({status: 404, body: ''});
    return route.fulfill({body: readFileSync(path.join(staticRoot, file)),
      contentType: file.endsWith('.js') ? 'application/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html'});
  });
  return {page, requests, errors};
}

async function addCard(page, text, enchantment) {
  await page.locator('#runs-card-search').fill(text);
  await page.locator('.runs-card-option').first().waitFor();
  await page.locator('#runs-card-search').press('Enter');
  if (enchantment) await page.locator('.runs-enchantment-select').last().selectOption(enchantment);
}

test('per-card enchantments survive queries, page changes, reloads, and analysis switching', async t => {
  const {page, requests, errors} = await setup(t);
  await page.goto('http://runs.test/runs');
  await addCard(page, '悠悠', 'Fiery');
  // The same card remains available for another enchantment.
  await addCard(page, '悠悠', 'Toxic');
  assert.equal(requests.length, 0);
  assert.equal(await page.locator('.runs-card-tag').count(), 2);
  await page.locator('.runs-enchantment-select').last().selectOption('Fiery');
  assert.match(await page.locator('#runs-error').textContent(), /已存在/);
  assert.equal(await page.locator('.runs-enchantment-select').last().inputValue(), 'Toxic');
  await page.locator('#runs-hero').selectOption('瓦内莎');
  await page.locator('#runs-days').fill('7');
  await page.locator('#runs-rank').selectOption('legendary');
  await page.getByRole('button', {name: '查询', exact: true}).click();
  await page.locator('.run-item').waitFor();
  const expected = [{name: '悠悠球', enchantment: 'Fiery'}, {name: '悠悠球', enchantment: 'Toxic'}];
  assert.deepEqual(JSON.parse(requests[0].searchParams.get('card_filters')), expected);
  assert.equal(requests[0].searchParams.get('hero'), '瓦内莎');
  assert.equal(requests[0].searchParams.get('rank'), 'legendary');
  assert.equal(requests[0].searchParams.get('days'), '7');
  assert.equal(requests[0].searchParams.has('cards'), false);
  assert.equal(await page.locator('.is-filter-match .run-card-enchantment').textContent(), '炽焰');
  assert.match(await page.locator('.run-items').textContent(), /悠悠球/);
  assert.equal(await page.locator('.run-card-chip').count(), 10); // 8 + matching tenth + hidden-count chip.
  await page.evaluate(() => { lastQuery = 0; });
  await page.getByRole('button', {name: '2', exact: true}).click();
  await page.waitForFunction(() => location.search.includes('page=2'));
  assert.deepEqual(JSON.parse(requests[1].searchParams.get('card_filters')), expected);
  await page.reload();
  await page.locator('.run-item').waitFor();
  assert.deepEqual(await page.locator('.runs-enchantment-select').evaluateAll(els => els.map(el => el.value)), ['Fiery', 'Toxic']);
  assert.equal(await page.locator('#runs-days').inputValue(), '7');
  assert.equal(requests.at(-1).searchParams.get('page'), '2');
  // An enchanted filter must not be silently ignored by the base-card analysis.
  await page.locator('.runs-card-tag button').last().click();
  await page.getByRole('button', {name: '🔍 阵容分析', exact: true}).click();
  await page.evaluate(() => { lastQuery = 0; });
  const count = requests.length;
  await page.getByRole('button', {name: '查询', exact: true}).click();
  assert.match(await page.locator('#comp-result').textContent(), /附魔筛选目前仅支持阵容列表/);
  assert.equal(requests.length, count);
  assert.deepEqual(errors, []);
});

test('legacy plus-separated links restore unconstrained cards and use the new query', async t => {
  const {page, requests, errors} = await setup(t);
  await page.goto('http://runs.test/runs?cards=悠悠球+獠牙&auto=1');
  await page.locator('.run-item').waitFor();
  assert.deepEqual(JSON.parse(requests[0].searchParams.get('card_filters')),
    [{name: '悠悠球', enchantment: null}, {name: '獠牙', enchantment: null}]);
  assert.deepEqual(await page.locator('.runs-enchantment-select').evaluateAll(els => els.map(el => el.value)), ['', '']);
  assert.deepEqual(errors, []);
});

test('mobile tags scroll within the picker and all result pages remain reachable', async t => {
  const {page, errors} = await setup(t, 375);
  await page.goto('http://runs.test/runs');
  await addCard(page, '悠悠', 'Fiery');
  await addCard(page, '獠', 'Shiny');
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await page.getByRole('button', {name: '查询', exact: true}).click();
  await page.locator('.run-item').waitFor();
  await page.evaluate(() => { lastQuery = 0; });
  await page.getByRole('button', {name: '末页', exact: true}).click();
  await page.waitForFunction(() => location.search.includes('page=17'));
  assert.equal(await page.locator('.page-btn.active').textContent(), '17');
  assert.equal(await page.getByRole('button', {name: '首页', exact: true}).count(), 1);
  if (process.env.RUNS_ENCHANTMENT_SCREENSHOT) {
    await page.evaluate(() => scrollTo(0, 0));
    await page.screenshot({path: process.env.RUNS_ENCHANTMENT_SCREENSHOT, fullPage: true});
  }
  assert.deepEqual(errors, []);
});

test('invalid structured links show an error without silently widening the query', async t => {
  const {page, requests, errors} = await setup(t);
  await page.goto('http://runs.test/runs?card_filters=%7B%7D&auto=1');
  assert.match(await page.locator('#runs-error').textContent(), /无效的卡牌附魔筛选链接/);
  assert.equal(requests.length, 0);
  assert.deepEqual(errors, []);
});

test('opening the HTML directly explains how to reach the working HTTP preview', async t => {
  const browser = await chromium.launch({headless: true,
    ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE
      ? {executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE} : {channel: 'chromium'}),
  });
  t.after(() => browser.close());
  const page = await browser.newPage();
  const {pathToFileURL} = require('node:url');
  await page.goto(pathToFileURL(path.join(staticRoot, 'runs.html')).href);
  assert.match(await page.locator('#runs-error').textContent(), /直接打开的本地文件/);
  assert.equal(await page.locator('#runs-error a').getAttribute('href'), 'http://127.0.0.1:1027/runs');
  await page.getByRole('button', {name: '查询', exact: true}).click();
  assert.match(await page.locator('#runs-error').textContent(), /本地 HTTP 预览/);
});
