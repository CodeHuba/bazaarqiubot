// Run with: node --test tests/test_stats_intraday_browser.cjs (requires playwright).
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const { chromium } = require('playwright');

const staticRoot = path.resolve(__dirname, '../web_runs/static');

function fixture(date = '2026-10-14') {
  const isToday = date === '2026-10-14';
  const previousDate = new Date(new Date(date + 'T00:00:00Z').getTime() - 86400000).toISOString().slice(0, 10);
  function period(day, previous) {
    const hours = Array.from({ length: 24 }, (_, hour) => ({
      hour, state: isToday ? hour > 14 ? 'future' : hour === 14 ? 'partial' : 'complete' : 'complete',
      uses: 0, uv: 0, pv: 0, api_calls: 0, successes: 0, empty_results: 0, errors: 0,
    }));
    for (const [hour, uses] of previous ? [[0, 2], [1, 2], [14, 1]] : [[0, 1], [1, 2], [14, 3]]) {
      Object.assign(hours[hour], { uses, uv: hour === 14 ? 2 : 1, successes: uses });
    }
    Object.assign(hours[0], { pv: 5, api_calls: 2 });
    Object.assign(hours[1], { pv: 7, api_calls: 3 });
    Object.assign(hours[14], { pv: 8, api_calls: 5 });
    if (!isToday) Object.assign(hours[23], { uses: 4, uv: 1, successes: 4 });
    const summary = { uses: previous ? 5 : 6, uv: 3, pv: 20, api_calls: 10, successes: previous ? 5 : 6, empty_results: 0, errors: 0 };
    if (!isToday) { summary.uses += 4; summary.successes += 4; }
    const runsHours = hours.map(hour => ({ ...hour, uses: hour.hour === 0 || hour.hour === 1 ? 1 : 0,
      uv: hour.hour === 0 || hour.hour === 1 ? 1 : 0 }));
    return { date: day, available: { core: day >= '2026-10-11', pv: day >= '2026-10-11', api: day >= '2026-10-11' },
      all: { hours, summary }, modules: { runs: { hours: runsHours, summary: { ...summary, uses: 2, uv: 1 } } } };
  }
  return { date, previous_date: previousDate, today: '2026-10-14', min_date: '2026-07-17',
    timezone: 'Asia/Shanghai', is_today: isToday, generated_at: '2026-10-14T14:35:00+08:00',
    cutoff_time: isToday ? '14:35:00' : '24:00:00', modules: ['runs'],
    current: period(date, false), previous: period(previousDate, true) };
}

async function setup(mode = 'normal') {
  const browser = await chromium.launch({ headless: true, channel: 'chromium' });
  const page = await browser.newPage({ viewport: { width: 1280, height: 950 } });
  await page.clock.install({ time: new Date('2026-10-14T06:35:00Z') });
  const errors = [], requests = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    assert.equal(url.origin, 'http://stats.test');
    if (url.pathname === '/api/stats/overview') return route.fulfill({ json: {
      total_calls: 70, total_uv: 7, daily: [{ day: '2026-10-14', cnt: 70, uv: 7 }],
      module_usage: [{ module: 'runs', uses: 3 }], hot_cards: [], tab_pv: [], api_dist: [],
    } });
    if (url.pathname === '/api/stats/intraday') {
      requests.push(url.searchParams.get('date'));
      if (mode === 'api-error') return route.fulfill({ status: 500, json: { error: '日内统计暂不可用' } });
      return route.fulfill({ json: fixture(url.searchParams.get('date')) });
    }
    const relative = url.pathname === '/admin/stats' ? 'stats.html'
      : url.pathname.startsWith('/static/') ? url.pathname.slice(8) : null;
    if (!relative) return route.fulfill({ status: 404, body: '' });
    if (relative.startsWith('vendor/chart.umd') && mode === 'missing-library') return route.abort();
    if (relative.startsWith('vendor/chart.umd') && mode === 'chart-error') return route.fulfill({
      contentType: 'application/javascript', body: 'window.Chart = function () { throw new Error("canvas unavailable"); };',
    });
    return route.fulfill({ body: readFileSync(path.join(staticRoot, relative)),
      contentType: relative.endsWith('.js') ? 'application/javascript' : relative.endsWith('.css') ? 'text/css' : 'text/html' });
  });
  await page.goto('http://stats.test/admin/stats');
  return { browser, page, requests, errors };
}

test('hourly chart, local filters, prior-day comparison and historical dates', async () => {
  const { browser, page, requests, errors } = await setup();
  try {
    await page.waitForFunction(() => document.querySelector('#intraday-total').textContent === '6');
    assert.equal(await page.locator('#intraday-change').textContent(), '+20.0%');
    assert.equal(await page.locator('#intraday-peak').textContent(), '14:00–15:00');
    assert.match(await page.locator('#intraday-peak-note').textContent(), /进行中/);
    assert.equal(await page.locator('#intraday-hourly-body tr').count(), 24);
    assert.equal(await page.locator('#intraday-hourly-body tr').nth(15).locator('td').nth(1).textContent(), '—');
    if (process.env.STATS_PREVIEW_DIR) {
      await page.waitForFunction(() => document.querySelector('#overview-kpis .kpi-value')?.textContent === '70');
      await page.clock.runFor(1200);
      await page.locator('#intraday-panel').screenshot({ path: path.join(process.env.STATS_PREVIEW_DIR, 'desktop.png') });
    }
    assert.deepEqual(await page.evaluate(() => Chart.getChart('intraday-chart').data.datasets[0].data.slice(14, 17)), [3, null, null]);
    await page.locator('#intraday-metric').selectOption('uv');
    assert.equal(await page.locator('#intraday-total').textContent(), '3');
    await page.locator('#intraday-module').selectOption('runs');
    assert.equal(await page.locator('#intraday-total').textContent(), '1');
    await page.locator('#intraday-metric').selectOption('uses');
    assert.equal(await page.locator('#intraday-total').textContent(), '2');
    await page.locator('#intraday-metric').selectOption('pv');
    assert.equal(await page.locator('#intraday-total').textContent(), '20');
    assert.equal(await page.locator('#intraday-module').isDisabled(), true);
    await page.locator('#intraday-metric').selectOption('api_calls');
    assert.equal(await page.locator('#intraday-total').textContent(), '10');
    assert.equal(requests.length, 1, 'metric/module filters do not fetch again');
    await page.locator('#intraday-compare').uncheck();
    assert.equal(await page.evaluate(() => Chart.getChart('intraday-chart').data.datasets.length), 1);
    await page.locator('#intraday-compare').check();
    await page.locator('#intraday-metric').selectOption('uses');
    await page.locator('#intraday-module').selectOption('all');
    await page.locator('#intraday-yesterday').click();
    await page.waitForFunction(() => document.querySelector('#intraday-total-note').textContent.includes('2026-10-13 全天'));
    assert.equal(await page.locator('#intraday-total').textContent(), '10');
    assert.equal(await page.evaluate(() => Chart.getChart('intraday-chart').data.datasets[0].data[23]), 4);
    await page.locator('[data-days="30"]').click();
    assert.equal(await page.locator('#intraday-date').inputValue(), '2026-10-13');
    await page.locator('#intraday-date').fill('2026-10-10');
    await page.locator('#intraday-date').dispatchEvent('change');
    await page.waitForFunction(() => document.querySelector('#intraday-status').textContent.includes('此日期无采集记录'));
    assert.equal(await page.locator('#intraday-total').textContent(), '—');
    assert.equal(await page.evaluate(() => Chart.getChart('intraday-chart').data.datasets[0].data.every(value => value === null)), true);
    assert.deepEqual(errors, []);
    await page.locator('#intraday-today').click();
    await page.waitForFunction(() => document.querySelector('#intraday-total').textContent === '6');
    await page.setViewportSize({ width: 390, height: 844 });
    await page.clock.runFor(1200);
    if (process.env.STATS_PREVIEW_DIR) await page.locator('#intraday-panel').screenshot({ path: path.join(process.env.STATS_PREVIEW_DIR, 'mobile.png') });
    const overflow = await page.evaluate(() => [...document.querySelectorAll('body *')].filter(element =>
      element.getBoundingClientRect().right > innerWidth + 1 && element.getBoundingClientRect().width > 0
      && getComputedStyle(element).visibility !== 'hidden').map(element => ({ tag: element.tagName, id: element.id,
        className: element.className, width: element.getBoundingClientRect().width })));
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, JSON.stringify(overflow));
  } finally { await browser.close(); }
});

for (const mode of ['missing-library', 'chart-error', 'api-error']) {
  test(`hourly stats graceful failure: ${mode}`, async () => {
    const { browser, page, errors } = await setup(mode);
    try {
      if (mode === 'api-error') {
        await page.waitForFunction(() => document.querySelector('#intraday-status').textContent.includes('加载失败'));
        assert.equal(await page.locator('#intraday-total').textContent(), '—');
      } else {
        await page.waitForFunction(() => document.querySelector('#intraday-total').textContent === '6');
        assert.equal(await page.locator('#intraday-details').getAttribute('open'), '');
        assert.equal(await page.locator('#intraday-hourly-body tr').count(), 24);
        assert.match(await page.locator('#intraday-chart-wrap').textContent(), /逐小时明细/);
      }
      assert.equal(await page.locator('#overview-kpis .kpi-value').first().textContent(), '70');
      assert.equal(await page.locator('#main-content .error-msg').count(), 0);
      assert.deepEqual(errors, []);
    } finally { await browser.close(); }
  });
}

test('late old date response cannot overwrite a newer date', async () => {
  const { browser, page, errors } = await setup();
  try {
    await page.waitForFunction(() => document.querySelector('#intraday-total').textContent === '6');
    let release;
    const held = new Promise(resolve => { release = resolve; });
    let started;
    const received = new Promise(resolve => { started = resolve; });
    await page.route('**/api/stats/intraday?date=2026-10-12', async route => {
      started();
      await held;
      await route.fulfill({ json: fixture('2026-10-12') }).catch(() => {});
    });
    await page.locator('#intraday-date').fill('2026-10-12');
    await page.locator('#intraday-date').dispatchEvent('change');
    await received;
    await page.locator('#intraday-today').click();
    await page.waitForFunction(() => document.querySelector('#intraday-total-note').textContent.includes('2026-10-14'));
    release();
    // Wait for the held handler to finish, rather than relying on a timed sleep.
    await page.evaluate(() => StatsIntraday.refresh());
    assert.equal(await page.locator('#intraday-date').inputValue(), '2026-10-14');
    assert.match(await page.locator('#intraday-total-note').textContent(), /2026-10-14/);
    assert.equal(await page.locator('#intraday-total').textContent(), '6');
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});

test('five minute refresh follows Beijing midnight and preserves historical selection', async () => {
  const { browser, page, requests } = await setup();
  try {
    await page.waitForFunction(() => document.querySelector('#intraday-total').textContent === '6');
    await page.clock.fastForward(300000);
    await page.waitForFunction(() => !document.querySelector('#intraday-refresh').disabled);
    assert.equal(requests.length, 2);
    await page.locator('#intraday-yesterday').click();
    await page.waitForFunction(() => document.querySelector('#intraday-total-note').textContent.includes('2026-10-13 全天'));
    await page.clock.fastForward(300000);
    assert.equal(requests.length, 3, 'historical date does not poll');
    await page.locator('#intraday-today').click();
    await page.waitForFunction(() => document.querySelector('#intraday-total').textContent === '6');
    await page.route('**/api/stats/intraday?date=2026-10-15', route => {
      requests.push('2026-10-15');
      const payload = fixture('2026-10-15');
      payload.today = '2026-10-15';
      return route.fulfill({ json: payload });
    });
    await page.clock.setSystemTime(new Date('2026-10-14T16:00:00Z'));
    await page.clock.fastForward(300000);
    await page.waitForFunction(() => document.querySelector('#intraday-total-note').textContent.includes('2026-10-15'));
    assert.equal(await page.locator('#intraday-date').inputValue(), '2026-10-15');
    assert.equal(requests.at(-1), '2026-10-15');
  } finally { await browser.close(); }
});
