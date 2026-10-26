// Run with: node --test tests/test_stats_feature_labels_browser.cjs (requires playwright).
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const { chromium } = require('playwright');

const staticRoot = path.resolve(__dirname, '../web_runs/static');
const active = {
  runs_query: '阵容查询 → 阵容列表', comp_card_query: '阵容查询 → 阵容分析',
  winrate_query: '胜率查询 → 10胜率查询', partner_query: '最佳搭档 → 最佳搭档查询',
  hero_overview: '职业榜单 → 职业概况', topcard_query: '职业榜单 → 卡牌榜',
  comp_query: '职业榜单 → 阵容榜', card_tier_query: '职业榜单 → 卡牌 T 表',
  feedback_submit: '意见反馈 → 提交反馈', feedback_like: '意见反馈 → 点赞',
  feedback_comment: '意见反馈 → 发送评论／回复', trivia_submit: '冷知识 → 提交问题',
  trivia_vote: '冷知识 → 赞同／不赞同', trivia_share: '冷知识 → 分享图片',
  donation_qr_open: '支持 → 查看赞赏码',
};
const retired = ['routes_query', 'routes_summary', 'routes_latest', 'routes_node_detail',
  'routes_expand', 'routes_cores', 'routes_detail'];
const features = [...Object.keys(active), 'support_page_view', ...retired].map((feature, index) => ({
  feature, uses: 100 - index, uv: 8, successes: 50, empty_results: 2, errors: 1,
}));

test('feature rows match frontend positions and keep automatic/offline history separate', async () => {
  const browser = await chromium.launch({ headless: true, channel: 'chromium' });
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 950 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => {
      const url = new URL(route.request().url());
      if (url.pathname === '/api/stats/overview') return route.fulfill({ json: {
        feature_usage: url.searchParams.get('days') === '1' ? [] : features,
        api_dist: [], daily: [], hot_cards: [], tab_pv: [], module_usage: [],
      } });
      if (url.pathname === '/api/stats/intraday') return route.fulfill({ status: 503, json: { error: '模拟接口未提供日内数据' } });
      const relative = url.pathname === '/admin/stats' ? 'stats.html'
        : url.pathname.startsWith('/static/') ? url.pathname.slice(8) : null;
      if (!relative) return route.fulfill({ status: 404, body: '' });
      return route.fulfill({ body: readFileSync(path.join(staticRoot, relative)),
        contentType: relative.endsWith('.js') ? 'application/javascript'
          : relative.endsWith('.css') ? 'text/css' : 'text/html' });
    });
    await page.goto('http://stats.test/admin/stats');
    await page.waitForFunction(() => document.querySelectorAll('#feature-usage-body tr[data-feature]').length === 15);
    for (const [feature, label] of Object.entries(active)) {
      const row = page.locator(`#feature-usage-body tr[data-feature="${feature}"]`);
      assert.equal(await row.locator('.feature-name').textContent(), label);
      assert.ok((await row.locator('.feature-description').textContent()).length > 10);
    }
    assert.equal(await page.locator('#feature-usage-body tr[data-feature^="routes_"]').count(), 0);
    assert.equal(await page.locator('#feature-auto-body tr[data-feature]').count(), 1);
    assert.equal(await page.locator('#feature-retired-body tr[data-feature]').count(), 7);
    assert.equal(await page.locator('#feature-auto-section').getAttribute('open'), null);
    assert.equal(await page.locator('#feature-retired-section').getAttribute('open'), null);
    await page.locator('#feature-auto-section > summary').click();
    await page.locator('#feature-retired-section > summary').click();
    for (const source of features) {
      const row = page.locator(`#feature-usage-section tr[data-feature="${source.feature}"]`);
      assert.deepEqual((await row.locator('td').allTextContents()).slice(1), [
        String(source.uses), '8', '50', '2', '1', `${(50 / source.uses * 100).toFixed(1)}%`,
      ]);
    }
    assert.match(await page.locator('#feature-retired-section .feature-note').textContent(), /阵容路线暂时下线/);
    assert.equal(await page.locator('#feature-retired-section a').count(), 0);
    await page.locator('#feature-auto-section > summary').click();
    await page.locator('#feature-retired-section > summary').click();
    await page.locator('.bz-query-progress').waitFor({ state: 'hidden' });
    if (process.env.STATS_FEATURE_PREVIEW_DIR) await page.locator('#feature-usage-section').screenshot({
      path: path.join(process.env.STATS_FEATURE_PREVIEW_DIR, 'features-desktop.png'),
    });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForFunction(() => Chart.getChart('trend-chart').width < 390);
    const layout = await page.evaluate(() => ({
      width: window.innerWidth, pageWidth: document.documentElement.scrollWidth,
      overflow: [...document.querySelectorAll('body *')].filter(el => el.getBoundingClientRect().right > window.innerWidth)
        .slice(0, 12).map(el => ({ tag: el.tagName, id: el.id, className: el.className, right: el.getBoundingClientRect().right })),
    }));
    assert.ok(layout.pageWidth <= layout.width, JSON.stringify(layout));
    if (process.env.STATS_FEATURE_PREVIEW_DIR) await page.locator('#feature-usage-section').screenshot({
      path: path.join(process.env.STATS_FEATURE_PREVIEW_DIR, 'features-mobile.png'),
    });
    await page.locator('[data-days="1"]').click();
    await page.waitForFunction(() => document.querySelector('#feature-auto-count').textContent === '0 项');
    assert.equal(await page.locator('#feature-usage-section tr[data-feature]').count(), 0);
    assert.equal(await page.locator('#feature-retired-count').textContent(), '0 项');
    assert.match(await page.locator('#feature-usage-body').textContent(), /暂无功能事件数据/);
    // Unknown historic names remain readable, and cannot inject markup or inherit object properties.
    await page.evaluate(() => renderFeatureGroups([
      { feature: '<img src=x onerror=alert(1)>', uses: 0, uv: 0, successes: 0, empty_results: 0, errors: 0 },
      { feature: 'constructor', uses: 1, uv: 1, successes: 1, empty_results: 0, errors: 0 },
    ]));
    assert.equal(await page.locator('#feature-usage-body img').count(), 0);
    assert.deepEqual(await page.locator('#feature-usage-body .feature-name').allTextContents(), ['<img src=x onerror=alert(1)>', 'constructor']);
    assert.deepEqual(errors, []);
  } finally {
    await browser.close();
  }
});
