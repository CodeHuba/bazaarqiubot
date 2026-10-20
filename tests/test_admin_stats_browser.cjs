// Run with: node --test tests/test_admin_stats_browser.cjs (requires playwright).
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const { chromium } = require('playwright');

const staticRoot = path.resolve(__dirname, '../web_runs/static');

for (const mode of ['normal', 'missing-library', 'chart-error']) {
  test(`stats day switching: ${mode}`, async () => {
    const browser = await chromium.launch({ headless: true, channel: 'chromium' });
    try {
      const page = await browser.newPage();
      const errors = [];
      const requests = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.addInitScript(() => {
        const original = window.setInterval;
        window.statsRefreshTimers = 0;
        window.setInterval = function (callback, delay, ...args) {
          if (delay === 1800000) window.statsRefreshTimers++;
          return original(callback, delay, ...args);
        };
      });
      await page.route('**/*', async route => {
        const url = new URL(route.request().url());
        assert.equal(url.origin, 'http://stats.test', 'dashboard uses local assets');
        if (url.pathname === '/api/stats/overview') {
          const days = Number(url.searchParams.get('days'));
          requests.push(days);
          return route.fulfill({ json: {
            total_calls: days * 10, total_uv: days,
            daily: [{ day: '2026-10-14', cnt: days * 10, uv: days }],
            module_usage: [{ module: 'runs', uses: days }],
            tab_pv: [{ tab: 'runs', cnt: days }],
            hot_cards: [{ card: `card-${days}`, cnt: days }],
            api_dist: [{ endpoint: '/api/runs', cnt: days, avg_ms: 12, errors: 0 }],
          } });
        }
        let relativePath;
        if (url.pathname === '/admin/stats') relativePath = 'stats.html';
        else if (url.pathname.startsWith('/static/')) relativePath = url.pathname.slice(8);
        else return route.fulfill({ status: 404, body: '' });
        if (relativePath.startsWith('vendor/chart.umd') && mode === 'missing-library') {
          return route.abort();
        }
        if (relativePath.startsWith('vendor/chart.umd') && mode === 'chart-error') {
          return route.fulfill({ contentType: 'application/javascript',
            body: 'window.Chart = function () { throw new Error("canvas unavailable"); };' });
        }
        return route.fulfill({ body: readFileSync(path.join(staticRoot, relativePath)),
          contentType: relativePath.endsWith('.js') ? 'application/javascript'
            : relativePath.endsWith('.css') ? 'text/css' : 'text/html' });
      });

      await page.goto('http://stats.test/admin/stats');
      for (const [index, days] of [7, 1, 30, 90, 7].entries()) {
        if (index > 0) {
          await page.locator(`[data-days="${days}"]`).click();
        }
        await page.waitForFunction(days =>
          document.querySelector('#hot-cards-body').textContent.includes(`card-${days}`), days);
        assert.equal(await page.locator('#monitor-kpis .kpi-value').first().textContent(), String(days * 10));
        assert.equal(await page.locator('[data-days].active').getAttribute('data-days'), String(days));
        assert.match(await page.locator('#api-detail-body').textContent(), /\/api\/runs/);
        assert.match(await page.locator('#tab-pv-list').textContent(), /阵容查询/);
        assert.match(await page.locator('#update-time').textContent(), new RegExp(`近 ${days} 天`));
        assert.equal(await page.locator('#main-content.error-msg').count(), 0);
        if (mode === 'normal') {
          assert.deepEqual(await page.evaluate(() => ({
            version: Chart.version,
            charts: Object.keys(Chart.instances).length,
            counts: Chart.getChart('trend-chart').data.datasets[0].data,
          })), { version: '4.4.0', charts: 1, counts: [days * 10] });
        } else {
          assert.equal(await page.locator('#trend-chart-wrap [role="status"]').count(), 1);
        }
      }
      // Programmatic calls must also work without the browser's implicit event.
      await page.evaluate(() => setDays(1));
      await page.waitForFunction(() => document.querySelector('#hot-cards-body').textContent.includes('card-1'));
      assert.deepEqual(requests, [7, 1, 30, 90, 7, 1]);
      assert.equal(await page.evaluate(() => window.statsRefreshTimers), 1);
      assert.deepEqual(errors, []);

      // Real API failures should still be reported rather than hidden by chart fallback.
      await page.route('**/api/stats/overview?*', route => route.fulfill({ status: 500, json: { error: 'database error' } }));
      await page.evaluate(() => loadStats());
      assert.match(await page.locator('#main-content.error-msg').textContent(), /请求失败/);
    } finally {
      await browser.close();
    }
  });
}
