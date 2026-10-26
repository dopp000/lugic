// Run by tests/test_builder_site.py (only when Playwright is installed). Opens the page in Chromium and prints what it found,
// once with the files as they are and once with preview.js replaced by an empty older copy, the way a browser that kept an old
// file for a few minutes would load it.
const { chromium } = require('playwright');

const base = process.argv[2];

(async () => {
  const out = {};
  for (const scenario of ['fresh', 'stale']) {
    const browser = await chromium.launch();
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    await page.route(/limbuscompany\.wiki\.gg/, (route) => route.abort());
    if (scenario === 'stale') {
      await page.route('**/preview.js*', (route) => route.fulfill({ contentType: 'application/javascript', body: 'window.LugicPreview = {};' }));
    }
    await page.goto(base + '/index.html', { waitUntil: 'load' });
    await page.waitForTimeout(300);
    out[scenario] = await page.evaluate(() => ({
      tabs: [].map.call(document.querySelectorAll('#nav button.tab'), (tab) => tab.textContent),
      open: [].filter.call(document.querySelectorAll('section.part'), (part) => !part.hidden).map((part) => part.id),
      parts: document.querySelectorAll('section.part').length,
      box: (document.getElementById('trouble') || {}).textContent || '',
      button: !!document.querySelector('#trouble button'),
    }));
    await browser.close();
  }
  process.stdout.write(JSON.stringify(out));
})().catch((error) => { console.error(error); process.exit(1); });
