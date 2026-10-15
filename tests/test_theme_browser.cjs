// NODE_PATH must include the bundled Playwright package when it is not installed locally.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const root = process.env.THEME_STATIC_ROOT || path.resolve(__dirname, '../web_runs/static');

async function serve(context) {
  await context.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.hostname !== 'theme.test') return route.abort();
    if (url.pathname.startsWith('/api/')) {
      return route.fulfill({ json: { cards: [], runs: [], total: 0, items: [],
        supporters: [], spaces: [], trivia: [], feedback: [], daily: [], hot_cards: [],
        tab_pv: [], module_usage: [], feature_usage: [], api_dist: [] } });
    }
    const name = url.pathname.replace(/^\/static\//, '').replace(/^\//, '') || 'runs.html';
    const file = path.join(root, name);
    if (!fs.existsSync(file) || !fs.statSync(file).isFile()) return route.fulfill({ status: 404, body: '' });
    await route.fulfill({ body: fs.readFileSync(file), contentType: name.endsWith('.js') ? 'application/javascript'
      : name.endsWith('.css') ? 'text/css' : 'text/html' });
  });
}

test('themes persist across pages and tabs, support keyboard input and storage failures', async () => {
  const browser = await chromium.launch({ headless: true, channel: 'chromium' });
  try {
    const context = await browser.newContext();
    await serve(context);
    const page = await context.newPage();
    await page.goto('http://theme.test/runs.html');
    assert.equal(await page.locator('html').getAttribute('data-theme'), 'dark');
    assert.equal(await page.evaluate(() => getComputedStyle(document.body).backgroundColor), 'rgb(9, 10, 42)');
    const light = page.getByRole('button', { name: '明亮主题', exact: true });
    await light.focus();
    await light.press('Enter');
    assert.equal(await light.getAttribute('aria-pressed'), 'true');
    assert.equal(await page.evaluate(() => getComputedStyle(document.body).backgroundColor), 'rgb(245, 250, 252)');
    await page.reload();
    assert.equal(await light.getAttribute('aria-pressed'), 'true');
    const second = await context.newPage();
    await second.goto('http://theme.test/winrate.html');
    assert.equal(await second.locator('html').getAttribute('data-theme'), 'light');
    await second.getByRole('button', { name: '暗色主题', exact: true }).click();
    await page.waitForFunction(() => document.documentElement.dataset.theme === 'dark');
    await second.evaluate(() => localStorage.setItem('bazaarqiubot-theme', 'invalid'));
    await second.reload();
    assert.equal(await second.locator('html').getAttribute('data-theme'), 'dark');

    const blocked = await browser.newContext();
    await blocked.addInitScript(() => {
      for (const method of ['getItem', 'setItem']) {
        const original = Storage.prototype[method];
        Storage.prototype[method] = function (key, ...args) {
          if (key === 'bazaarqiubot-theme') throw new Error('Theme storage blocked');
          return original.call(this, key, ...args);
        };
      }
    });
    await serve(blocked);
    const privatePage = await blocked.newPage();
    const errors = [];
    privatePage.on('pageerror', error => errors.push(error.message));
    await privatePage.goto('http://theme.test/runs.html');
    await privatePage.getByRole('button', { name: '明亮主题', exact: true }).click();
    assert.equal(await privatePage.locator('html').getAttribute('data-theme'), 'light');
    assert.deepEqual(errors.filter(error => /Theme storage|theme\.js/.test(error)), []);
  } finally { await browser.close(); }
});

test('every page has a light palette and reachable theme controls on desktop and mobile', async () => {
  const browser = await chromium.launch({ headless: true, channel: 'chromium' });
  try {
    const context = await browser.newContext();
    await context.addInitScript(() => localStorage.setItem('bazaarqiubot-theme', 'light'));
    await serve(context);
    const page = await context.newPage();
    const released = process.env.THEME_RELEASE_MANIFEST
      ? new Set(Object.keys(JSON.parse(fs.readFileSync(process.env.THEME_RELEASE_MANIFEST)).files)) : null;
    for (const file of fs.readdirSync(root).filter(name => name.endsWith('.html') && (!released || released.has(name)))) {
      await page.goto('http://theme.test/' + file);
      assert.equal(await page.locator('.theme-switch').count(), 1, file);
      assert.equal(await page.locator('html').getAttribute('data-theme'), 'light', file);
      for (const width of [1440, 390]) {
        await page.setViewportSize({ width, height: 900 });
        await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
        for (const name of ['明亮主题', '暗色主题']) {
          const box = await page.getByRole('button', { name, exact: true }).boundingBox();
          assert.ok(box && box.x >= 0 && box.x + box.width <= width + 1, file + ': ' + name + ' at ' + width);
        }
        const body = await page.evaluate(() => ({
          bg: getComputedStyle(document.body).backgroundColor,
          overflow: document.documentElement.scrollWidth > innerWidth + 1,
          wide: [...document.querySelectorAll('body *')].filter(el => el.getBoundingClientRect().right > innerWidth + 1).slice(0, 8).map(el => el.tagName + '.' + el.className),
        }));
        assert.equal(body.bg, 'rgb(245, 250, 252)', file);
        assert.equal(body.overflow, false, file + ': horizontal overflow at ' + width + ': ' + body.wide.join(', '));
      }
    }
    await page.goto('http://theme.test/runs.html');
    // Exercise components added after load, including CSS fetched after theme.css.
    await page.evaluate(() => {
      document.body.insertAdjacentHTML('beforeend', '<div class="bcp-panel"><div class="bcp-controls"><select><option>测试卡牌</option></select></div></div><div class="bz-query-progress is-active"><div class="bz-query-progress-panel">查询中</div></div>');
    });
    assert.equal(await page.locator('.bcp-panel').evaluate(el => getComputedStyle(el).backgroundColor), 'rgb(255, 255, 255)');
    assert.equal(await page.locator('.bcp-controls select').evaluate(el => getComputedStyle(el).color), 'rgb(29, 48, 64)');
    assert.equal(await page.locator('.bz-query-progress-panel').evaluate(el => getComputedStyle(el).backgroundColor), 'rgb(255, 255, 255)');
  } finally { await browser.close(); }
});

test('dashboard canvas labels follow theme switches and recover the dark palette', async () => {
  const browser = await chromium.launch({ headless: true, channel: 'chromium' });
  try {
    const context = await browser.newContext();
    await serve(context);
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto('http://theme.test/stats.html');
    await page.waitForFunction(() => window.Chart?.getChart('trend-chart'));
    const palette = () => page.evaluate(() => {
      const chart = Chart.getChart('trend-chart');
      return [chart.legend.options.labels.color, chart.scales.x.options.ticks.color, chart.scales.x.options.grid.color];
    });
    const dark = await palette();
    await page.getByRole('button', { name: '明亮主题', exact: true }).click();
    assert.deepEqual(await palette(), ['#536879', '#536879', '#dce7ed']);
    await page.getByRole('button', { name: '暗色主题', exact: true }).click();
    assert.deepEqual(await palette(), dark);
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});
