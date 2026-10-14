// Run with: node --test tests/test_trivia_markdown_browser.cjs (requires playwright).
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const { chromium } = require('playwright');

const staticRoot = path.resolve(__dirname, '../web_runs/static');
const answer = [
  '## Game mechanics', '', '**Weighted rewards** and `items`.', '',
  '| Item | Chance |', '| --- | --- |', '| Gold | 50% |', '',
  '- First reward', '- Second reward', '', '> Verified from game data.',
].join('\n');

test('trivia formats answers and share cards when external CDNs are unavailable', async () => {
  const browser = await chromium.launch({ headless: true,
    ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE
      ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE }
      : { channel: 'chromium' }),
  });
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.origin !== 'http://trivia.test') return route.abort();
      if (url.pathname === '/api/trivia') {
        return route.fulfill({ json: { trivia: [{ id: 1, question: 'Reward rules?',
          answer, status: 'published', upvotes: 2, downvotes: 0,
          publishedAt: '2026-10-14', updatedAt: '2026-10-14' }] } });
      }
      if (url.pathname.startsWith('/api/')) return route.fulfill({ json: {} });
      const relativePath = url.pathname === '/trivia' ? 'trivia.html'
        : url.pathname.startsWith('/static/') ? url.pathname.slice(8) : null;
      if (!relativePath) return route.fulfill({ status: 404, body: '' });
      return route.fulfill({ body: readFileSync(path.join(staticRoot, relativePath)),
        contentType: relativePath.endsWith('.js') ? 'application/javascript' : 'text/html' });
    });
    await page.goto('http://trivia.test/trivia');
    await page.locator('.trivia-header').first().click();
    const content = page.locator('.trivia-card.expanded .md-content');
    assert.equal(await content.locator('h2').textContent(), 'Game mechanics');
    assert.equal(await content.locator('strong').textContent(), 'Weighted rewards');
    assert.equal(await content.locator('code').textContent(), 'items');
    assert.equal(await content.locator('table tbody tr').count(), 1);
    assert.equal(await content.locator('ul li').count(), 2);
    assert.equal(await content.locator('blockquote').count(), 1);
    assert.doesNotMatch(await content.textContent(), /##|\*\*|\| ---/);
    await page.locator('#sort-time').click();
    await page.locator('.trivia-header').first().click();
    assert.equal(await content.locator('table').count(), 1);
    // Inspect the real share flow while stubbing only its unrelated canvas dependency.
    await page.evaluate(() => {
      window.html2canvas = async () => document.createElement('canvas');
    });
    await page.locator('#share-btn-1').click();
    await page.locator('#share-modal-overlay').waitFor();
    assert.equal(await page.locator('#share-card-answer h2').textContent(), 'Game mechanics');
    assert.equal(await page.locator('#share-card-answer table').count(), 1);
    assert.deepEqual(errors, []);
  } finally {
    await browser.close();
  }
});
