// Run by tests/test_visualizer.py (only when Playwright is installed). Opens the Visualizer tab in Chromium and prints what the picture holds:
// its size, what changes when the controls are used, and the colours of the WARNING strip with and without a picture behind it.
const { chromium } = require('playwright');
const zlib = require('zlib');

const base = process.argv[2];

// a solid-colour PNG made here, so the test needs no picture file: pure red, which inverts to pure cyan
function solidPng(width, height, [r, g, b]) {
  const raw = Buffer.alloc((width * 3 + 1) * height);
  for (let y = 0; y < height; y++) {
    const row = y * (width * 3 + 1);
    for (let x = 0; x < width; x++) { raw[row + 1 + x * 3] = r; raw[row + 2 + x * 3] = g; raw[row + 3 + x * 3] = b; }
  }
  const table = Array.from({ length: 256 }, (_, n) => { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; return c >>> 0; });
  const crc = (buf) => { let c = ~0; for (const byte of buf) c = table[(c ^ byte) & 0xff] ^ (c >>> 8); return ~c >>> 0; };
  const chunk = (type, data) => {
    const len = Buffer.alloc(4); len.writeUInt32BE(data.length);
    const body = Buffer.concat([Buffer.from(type), data]);
    const sum = Buffer.alloc(4); sum.writeUInt32BE(crc(body));
    return Buffer.concat([len, body, sum]);
  };
  const head = Buffer.alloc(13); head.writeUInt32BE(width, 0); head.writeUInt32BE(height, 4); head[8] = 8; head[9] = 2;
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', head), chunk('IDAT', zlib.deflateSync(raw)), chunk('IEND', Buffer.alloc(0))]);
}

const skill = (n) => ({ name: 'Skill number ' + n, stats: '3, 1, 2, Slash', statuses: 'Burn:2:2,none', triggers: '[On Use] Gain 2 Protection\n:Coin 2: [On Hit] Inflict 2 Burn' });
const character = (skills) => ({
  v: 1, name: 'Rien', avatar: '', stats: { hp: 140, power: 10, speed: [2, 5], offense: 1, defense: 1 }, ranks: { burn: 2 },
  skills,
  passives: [{ name: 'Zwei Supression', kind: 'passive', cost: 2, lines: ['[Combat Start] Gain 2 Protection'], flavour: 'A hush before the order.' }],
});

async function open(seed, viewport) {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: viewport || { width: 1280, height: 1000 } });
  const errors = [];
  page.on('pageerror', (error) => errors.push('pageerror: ' + error.message));
  page.on('console', (message) => { if (message.type() === 'error' && !/Failed to load resource/.test(message.text())) { errors.push('console: ' + message.text()); } });
  await page.route(/limbuscompany\.wiki\.gg/, (route) => route.abort());
  await page.addInitScript((state) => {
    try { if (!localStorage.getItem('__seeded')) { localStorage.setItem('__seeded', '1'); localStorage.setItem('lugic-character-builder-v1', JSON.stringify(state)); } } catch (e) { /* no storage */ }
  }, seed);
  await page.goto(base + '/index.html', { waitUntil: 'load' });
  await page.waitForSelector('#nav button.tab');
  await page.locator('#nav button.tab', { hasText: /^Visualizer$/i }).click();
  await page.waitForSelector('#sec-visualizer .viz-canvas', { state: 'visible' });
  await settle(page);
  return { browser, page, errors };
}

// the picture is drawn after the fonts have loaded, so wait until the canvas stops changing
async function settle(page) {
  let last = '';
  for (let i = 0; i < 40; i++) {
    await page.waitForTimeout(150);
    const now = await page.evaluate(() => { const c = document.querySelector('.viz-canvas'); return c.width + 'x' + c.height + ':' + c.toDataURL('image/png').length; });
    if (now === last) { return; }
    last = now;
  }
}

const size = (page) => page.evaluate(() => { const c = document.querySelector('.viz-canvas'); return [c.width, c.height]; });
const fingerprint = (page) => page.evaluate(() => {
  const data = document.querySelector('.viz-canvas').getContext('2d').getImageData(0, 0, 1500, 1500).data;
  let sum = 0;
  for (let i = 0; i < data.length; i += 7) { sum = (sum * 31 + data[i]) >>> 0; }
  return sum;
});

// How many pixels in the top strip are (nearly) pure cyan, which is red turned inside out, and the colour of the page far from any text.
const stripLook = (page) => page.evaluate(() => {
  const canvas = document.querySelector('.viz-canvas');
  const ctx = canvas.getContext('2d');
  const strip = ctx.getImageData(0, 0, canvas.width, 64).data;
  let cyan = 0, other = 0;
  for (let i = 0; i < strip.length; i += 4) {
    if (strip[i] < 40 && strip[i + 1] > 215 && strip[i + 2] > 215) { cyan++; }
    else if (strip[i] + strip[i + 1] + strip[i + 2] > 150) { other++; }
  }
  const body = ctx.getImageData(750, canvas.height - 150, 1, 1).data;
  return { cyan, bright: other, body: [body[0], body[1], body[2]] };
});

(async () => {
  const out = {};

  // a small build: a square picture, which switches between the skills and the passives and follows the strip words
  {
    const { browser, page, errors } = await open(character([skill(1)]));
    out.tab = await page.$$eval('#nav button.tab', (tabs) => tabs.map((t) => t.textContent.trim()));
    out.small = await size(page);
    out.label = await page.getAttribute('.viz-canvas', 'aria-label');
    out.skillsFingerprint = await fingerprint(page);
    out.noPicture = await stripLook(page);

    // Download keeps the same picture
    const [file] = await Promise.all([page.waitForEvent('download'), page.locator('#sec-visualizer button', { hasText: 'Download picture' }).click()]);
    const bytes = await new Promise((resolve, reject) => { const parts = []; file.createReadStream().then((stream) => { stream.on('data', (d) => parts.push(d)); stream.on('end', () => resolve(Buffer.concat(parts))); stream.on('error', reject); }); });
    out.download = { name: file.suggestedFilename(), signature: bytes.slice(0, 8).toString('hex'), width: bytes.readUInt32BE(16), height: bytes.readUInt32BE(20), bytes: bytes.length };

    await page.locator('#sec-visualizer label.segment', { hasText: 'Passives' }).click();
    await settle(page);
    out.passivesSize = await size(page);
    out.passivesFingerprint = await fingerprint(page);
    await page.locator('#sec-visualizer label.segment', { hasText: 'Skills' }).click();
    await settle(page);

    await page.locator('#sec-visualizer input[type=text]').nth(1).fill('DANGER');
    await settle(page);
    out.stripWordFingerprint = await fingerprint(page);

    // a picture behind everything: the body is dimmed, the strip letters show it again, inverted, at full colour
    await page.locator('#sec-visualizer input[type=text]').nth(1).fill('WARNING');
    await page.locator('#sec-visualizer input[type=file]').nth(0).setInputFiles({ name: 'red.png', mimeType: 'image/png', buffer: solidPng(40, 40, [255, 0, 0]) });
    await page.waitForFunction(() => [].some.call(document.querySelectorAll('#sec-visualizer button'), (b) => b.textContent === 'Remove' && !b.hidden));
    await settle(page);
    out.withPicture = await stripLook(page);
    out.pictureSize = await size(page);

    // it is kept for the next visit
    await page.waitForTimeout(700);
    await page.reload({ waitUntil: 'load' });
    await page.locator('#nav button.tab', { hasText: /^Visualizer$/i }).click();
    await settle(page);
    out.afterReload = await stripLook(page);

    await page.locator('#sec-visualizer .art-row button', { hasText: 'Remove' }).first().click();
    await settle(page);
    out.afterRemove = await stripLook(page);
    out.errors = errors;
    await browser.close();
  }

  // many skills: the picture grows taller and stays as wide
  {
    const { browser, page, errors } = await open(character(Array.from({ length: 10 }, (_, i) => skill(i + 1))));
    out.tall = await size(page);
    out.tallErrors = errors;
    await browser.close();
  }

  // a phone: the picture shrinks to the screen and nothing runs off the side
  {
    const { browser, page } = await open(character([skill(1)]), { width: 390, height: 800 });
    out.phone = await page.evaluate(() => ({ fits: document.documentElement.scrollWidth <= document.documentElement.clientWidth, shown: Math.round(document.querySelector('.viz-canvas').getBoundingClientRect().width) }));
    await browser.close();
  }

  process.stdout.write(JSON.stringify(out));
})().catch((error) => { console.error(error); process.exit(1); });
