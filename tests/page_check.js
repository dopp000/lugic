// Run by tests/test_builder_site.py (only when Playwright is installed). Opens the page in Chromium and prints what it found,
// once with the files as they are and once with preview.js replaced by an empty older copy, the way a browser that kept an old
// file for a few minutes would load it.
const { chromium } = require('playwright');

const base = process.argv[2];

// What a reader does on the page: pick an attack type, a coin status with no keyword and a group tag, and break a rule.
async function behaviour(root) {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  await page.route(/limbuscompany\.wiki\.gg/, (route) => route.abort());
  const seed = {
    v: 1, name: 'Rien', avatar: '',
    skills: [{ name: 'Piercing Shot', stats: '4, 2, 3, Pierce', statuses: 'Poise:2:2,none,none', triggers: '' }],
  };
  await page.addInitScript((state) => {
    try { if (!localStorage.getItem('__seeded')) { localStorage.setItem('__seeded', '1'); localStorage.setItem('lugic-character-builder-v1', JSON.stringify(state)); } } catch (e) { /* no storage */ }
  }, seed);
  await page.goto(root + '/index.html', { waitUntil: 'load' });
  await page.waitForSelector('#nav button.tab');
  const found = {};
  const open = async (name) => { await page.locator('#nav button.tab', { hasText: new RegExp('^' + name + '$', 'i') }).click(); await page.waitForTimeout(150); };

  // the note under the form comes straight after the form, before the load and tools bar
  await open('Identity');
  found.footer = await page.evaluate(() => {
    const foot = document.querySelector('.foot').getBoundingClientRect();
    const sheet = document.getElementById('sheet').getBoundingClientRect();
    const tools = document.getElementById('tools').getBoundingClientRect();
    return { gap: Math.round(foot.top - sheet.bottom), beforeTools: foot.top < tools.top };
  });

  // a rule broken (a status the character has no rank in) turns its tab and its part red
  await open('Skills');
  found.flag = await page.evaluate(() => ({
    tab: [].filter.call(document.querySelectorAll('#nav button.tab.flagged'), (t) => t.textContent.trim()).map((t) => t.textContent.trim()),
    part: document.getElementById('sec-skills').classList.contains('flagged'),
    banner: !!document.querySelector('#sec-skills .part-flag'),
  }));

  // the attack type is three buttons
  const types = await page.$$eval('#sec-skills input[type=radio][name^="attack-type"]', (inputs) => inputs.map((i) => i.value));
  await page.locator('#sec-skills .segments.type label', { hasText: 'Blunt' }).first().click();
  await page.waitForTimeout(200);
  found.attack = await page.evaluate((offered) => ({
    offered,
    checked: [].filter.call(document.querySelectorAll('#sec-skills input[name^="attack-type"]'), (i) => i.checked).map((i) => i.value),
    preview: document.querySelector('#sec-skills .pv-wrap').innerText.includes('Blunt'),
    typeSelect: [].some.call(document.querySelectorAll('#sec-skills select'), (s) => [].some.call(s.options, (o) => o.textContent.trim() === 'Blunt')),
  }), types);

  // the limits for the skill's coins are written on the card, and follow the coin count
  const limitText = () => page.evaluate(() => [].map.call(document.querySelectorAll('#sec-skills .note'), (n) => n.textContent).filter((t) => /^At Power/.test(t))[0]);
  found.limit = [await limitText()];
  await page.locator('#sec-skills select').first().selectOption('4');
  await page.waitForTimeout(200);
  found.limit.push(await limitText());
  await page.locator('#sec-skills select').first().selectOption('3');
  await page.waitForTimeout(200);

  // a coin status with no keyword has a Potency and no Count
  const row = page.locator('#sec-skills .coin-row').nth(1);
  await row.locator('[role=combobox]').first().click();
  await page.locator('[role=option]', { hasText: /^\s*Haste/ }).first().click();
  await page.waitForTimeout(200);
  found.coin = await page.evaluate(() => [].map.call(document.querySelectorAll('#sec-skills .coin-row'), (r) => ({
    countHidden: [].some.call(r.querySelectorAll('label.mini'), (l) => /Count/.test(l.textContent) && l.hidden),
  })));

  // group tags: every tag the page knows, and Other
  await open('Group tags');
  const slot = page.locator('#sec-group-tags [role=combobox]').first();
  await slot.click();
  found.tagOptions = await page.locator('[role=option]').allTextContents();
  found.known = await page.evaluate(() => window.LUGIC_TAGS || null);
  await page.locator('[role=option]', { hasText: /Other/ }).first().click();
  const typed = page.locator('#sec-group-tags input[type=text]:visible').first();
  await typed.fill('My Own Crew');
  await page.waitForTimeout(200);
  found.tagOther = await page.evaluate(() => document.getElementById('card').innerText.includes('My Own Crew'));

  // the Stats tab says it too
  await open('Stats');
  found.statsHint = await page.evaluate(() => document.getElementById('sec-stats').innerText.replace(/\s+/g, ' '));

  // a phone: nothing runs off the side on any tab
  await page.setViewportSize({ width: 390, height: 800 });
  const tabs = await page.$$eval('#nav button.tab', (list) => list.map((t) => t.textContent.trim()));
  found.phone = [];
  for (const name of tabs) {
    await open(name);
    found.phone.push(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth));
  }
  await browser.close();
  return found;
}

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
  out.behaviour = await behaviour(base);
  process.stdout.write(JSON.stringify(out));
})().catch((error) => { console.error(error); process.exit(1); });
