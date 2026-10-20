// Run the real frontend against an isolated in-memory transport, never Production.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const { chromium } = require('playwright');

const root = path.join(__dirname, 'frontend');
const output = process.env.ROVE_VALUABLES_SMOKE_OUTPUT || fs.mkdtempSync(path.join(os.tmpdir(), 'rove-valuables-browser-'));
const note = 'Sachwerte werden nicht ins Gesamtvermögen eingerechnet.';
const position = (id, n, v) => ({ id: id.toString(16).padStart(32, '0'), n, v });
const fixtures = () => new Map([
  [1, [position(1, 'Uhr', 1250), position(2, 'Fahrrad', 650), position(3, 'Kamera', 475.25), position(4, 'Schmuck', 300)]],
  [2, [position(5, 'Gitarre', 700)]],
]);
let stored = fixtures(), failNextCreate = false, origin;
const writes = [];

function state(owner) {
  return {
    ok: true, user_id: owner, netWorth: 12500, onboardingRequired: false,
    onboarding: { required: false, status: 'completed', completed: true, step: 10 },
    identity: { name: 'Synthetischer Test', email: `synthetic-${owner}@example.test` },
    features: { multi_cash_accounts_v1: false },
    assets: [{ name: 'Girokonto', value: 12500, source: 'bot', icon: 'bank', sub: 'Geldkonto' }],
    valuables: stored.get(owner), tx: [], budgets: [], goals: [], vertraege: [], reports: [],
    sts: { konto: 12500, income: 3500, fixRest: 0, sparraten: 0, available: 2000 },
    monthlyPlan: { incomeStatus: 'confirmed', savingsStatus: 'confirmed', status: 'confirmed' },
  };
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, origin);
  const owner = Number(/rove-synthetic-owner=(\d+)/.exec(req.headers.cookie || '')?.[1]) || 1;
  const json = (body, status = 200) => {
    res.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
    res.end(JSON.stringify(body));
  };
  if (url.pathname.startsWith('/__api/')) {
    const apiPath = url.pathname.slice('/__api'.length);
    if (apiPath === '/v1/auth/me') return json({ ok: true, user_id: owner });
    if (apiPath.startsWith('/v1/auth/pin/')) return json({ ok: true, pin_status: 'unlocked', onboarding_required: false });
    if (apiPath.startsWith('/v1/push/')) return json({ ok: true, enabled: false });
    if (apiPath === '/v1/state') return json(state(owner));
    if (apiPath.startsWith('/v1/valuables')) {
      if (req.method !== 'GET' && req.headers['x-rove-user-id'] !== String(owner)) {
        return json({ ok: false, error: 'account_changed' }, 409);
      }
      if (req.method !== 'GET') {
        let raw = '';
        for await (const chunk of req) raw += chunk;
        const item = raw ? JSON.parse(raw) : null;
        writes.push({ owner, method: req.method, item });
        if (failNextCreate && req.method === 'POST') {
          failNextCreate = false;
          return json({ ok: false, error: 'synthetic_unavailable' }, 503);
        }
        const list = stored.get(owner);
        const id = apiPath.split('/')[3];
        if (req.method === 'DELETE') stored.set(owner, list.filter(p => p.id !== id));
        else if (req.method === 'PATCH') stored.set(owner, list.map(p => p.id === id ? item : p));
        else if (req.method === 'POST' && !list.some(p => p.id === item.id)) list.push(item);
        // Keep the first create pending while the browser delivers the second click.
        if (req.method === 'POST') await new Promise(resolve => setTimeout(resolve, 150));
      }
      return json({ ok: true, user_id: owner, valuables: stored.get(owner).slice().sort((a, b) => a.id.localeCompare(b.id)) });
    }
    return json({ ok: false, error: 'synthetic_route_not_found' }, 404);
  }
  const file = url.pathname === '/app/' ? 'index.html' : url.pathname.slice('/app/'.length);
  if (!url.pathname.startsWith('/app/') || !['index.html', 'logo.png', 'app-icon.png', 'manifest.webmanifest', 'sw.js'].includes(file)) {
    res.writeHead(404).end();
    return;
  }
  let body = fs.readFileSync(path.join(root, file));
  if (file === 'index.html') body = body.toString().replace('"https://getrove.de/app-api"', JSON.stringify(origin + '/__api'));
  const mime = { '.html': 'text/html; charset=utf-8', '.png': 'image/png', '.js': 'application/javascript', '.webmanifest': 'application/manifest+json' };
  res.writeHead(200, { 'Content-Type': mime[path.extname(file)], 'Cache-Control': 'no-store' });
  res.end(body);
});

async function loaded(page) {
  await page.locator('#app:not([hidden])').waitFor();
  await page.locator('#splash').waitFor({ state: 'detached' });
  await page.locator('[data-add-valuables]').waitFor();
  assert.equal(await page.locator('[data-add-financial-account]').count(), 0);
  assert.equal(await page.locator('#homeSachwerteSummary').count(), 0);
  assert.ok(!(await page.locator('#tab-home').innerText()).includes('nicht im Vermögensverlauf'));
  assert.match(await page.locator('#networth').innerText(), /^12\.500,00\s€$/);
}

async function open(page) {
  await page.locator('[data-add-valuables]').click();
  await page.getByLabel('Bezeichnung', { exact: true }).waitFor();
  assert.equal(await page.locator('#dbody input').count(), 2);
  assert.equal(await page.locator('#dbody .field button').count(), 0);
  assert.equal(await page.locator('#dbody').getByText(note, { exact: true }).count(), 1);
}

async function fill(page, name, value) {
  await page.getByLabel('Bezeichnung', { exact: true }).fill(name);
  await page.getByLabel('Geschätzter Wert', { exact: true }).fill(value);
}

async function assertLayout(page) {
  const layout = await page.evaluate(() => ({
    width: innerWidth, pageWidth: document.documentElement.scrollWidth,
    sheetWidth: document.getElementById('dsheet').clientWidth,
    contentWidth: document.getElementById('dsheet').scrollWidth,
    fields: [...document.querySelectorAll('#dbody input, #svSend, #dbody .valuable-row')].map(el => {
      const box = el.getBoundingClientRect();
      return { left: box.left, right: box.right };
    }),
  }));
  assert.ok(layout.pageWidth <= layout.width, JSON.stringify(layout));
  assert.ok(layout.contentWidth <= layout.sheetWidth + 1, JSON.stringify(layout));
  assert.ok(layout.fields.every(box => box.left >= 0 && box.right <= layout.width), JSON.stringify(layout));
}

async function flow(browser, label, viewport) {
  stored = fixtures(); writes.length = 0;
  const context = await browser.newContext({ viewport, serviceWorkers: 'block' });
  // Any accidental external request is blocked before it reaches the network.
  await context.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort('blockedbyclient'));
  await context.addCookies([{ name: 'rove-synthetic-owner', value: '1', url: origin }]);
  const page = await context.newPage(), errors = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    await page.goto(origin + '/app/');
    await loaded(page);
    await page.locator('[data-add-valuables]').scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(output, label + '-overview.png') });
    await open(page);
    assert.equal(await page.locator('#dbody .valuable-row').count(), 4);
    await fill(page, 'Koffer', '175,50');
    await page.locator('#svSend').dblclick();
    await page.waitForFunction(() => document.getElementById('toast').textContent === 'Sachwert hinzugefügt');
    assert.equal(writes.filter(write => write.method === 'POST').length, 1);
    assert.equal(stored.get(1).filter(item => item.n === 'Koffer').length, 1);
    assert.equal(await page.locator('#svName').inputValue(), '');
    assert.equal(await page.locator('#svAmt').inputValue(), '');
    const added = page.locator('#dbody .valuable-row').filter({ hasText: 'Koffer' });
    assert.match(await added.innerText(), /175,50\s€/);
    const box = await added.boundingBox();
    assert.ok(box.y >= 0 && box.y + box.height <= viewport.height, 'Saved row must be immediately in view');
    await assertLayout(page);
    await page.screenshot({ path: path.join(output, label + '-saved.png') });
    await page.locator('#svSend').scrollIntoViewIfNeeded();
    await assertLayout(page);
    await page.screenshot({ path: path.join(output, label + '-form.png') });

    await fill(page, 'Fehlerprobe', '25');
    failNextCreate = true;
    await page.locator('#svSend').click();
    await page.waitForFunction(() => document.getElementById('toast').textContent.startsWith('Speicherung nicht bestätigt'));
    assert.equal(await page.locator('#svName').inputValue(), 'Fehlerprobe');
    assert.equal(await page.locator('#svAmt').inputValue(), '25');
    assert.equal(stored.get(1).length, 5);
    const failed = writes.at(-1).item;
    await page.locator('#svSend').click();
    await page.waitForFunction(() => document.getElementById('svName').value === '');
    assert.equal(writes.at(-1).item.id, failed.id);
    assert.equal(stored.get(1).filter(item => item.n === 'Fehlerprobe').length, 1);

    await added.click();
    await fill(page, 'Reisekoffer', '180,75');
    await page.getByRole('button', { name: 'Änderungen speichern', exact: true }).click();
    await page.waitForFunction(() => document.getElementById('toast').textContent === 'Sachwert aktualisiert.');
    await page.reload();
    await loaded(page); await open(page);
    const edited = page.locator('#dbody .valuable-row').filter({ hasText: 'Reisekoffer' });
    assert.match(await edited.innerText(), /180,75\s€/);
    await edited.click();
    await page.locator('#svDelete').click();
    assert.equal(await edited.count(), 1);
    await page.locator('#svDelete').click();
    await edited.waitFor({ state: 'detached' });
    await page.reload();
    await loaded(page); await open(page);
    assert.equal(await page.locator('#dbody .valuable-row').filter({ hasText: 'Reisekoffer' }).count(), 0);
    assert.equal(stored.get(1).length, 5);

    await context.addCookies([{ name: 'rove-synthetic-owner', value: '2', url: origin }]);
    await page.reload();
    await loaded(page); await open(page);
    assert.equal(await page.locator('#dbody .valuable-row').count(), 1);
    assert.ok((await page.locator('#dbody .valuable-row').innerText()).includes('Gitarre'));
    await fill(page, 'Zweites Konto', '80');
    await page.locator('#svSend').click();
    await page.waitForFunction(() => document.getElementById('svName').value === '');
    await context.addCookies([{ name: 'rove-synthetic-owner', value: '1', url: origin }]);
    await page.reload();
    await loaded(page); await open(page);
    assert.equal(await page.locator('#dbody .valuable-row').count(), 5);
    assert.equal(await page.locator('#dbody').getByText('Zweites Konto', { exact: true }).count(), 0);
    assert.deepEqual(errors, []);
    console.log(label.toUpperCase() + '_SAVE_EDIT_DELETE_RELOAD_ACCOUNT_SWITCH=PASS');
  } finally {
    await context.close();
  }
}

async function main() {
  fs.mkdirSync(output, { recursive: true });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  origin = `http://127.0.0.1:${server.address().port}`;
  let browser;
  try {
    browser = await chromium.launch({ headless: true, executablePath: process.env.ROVE_BROWSER_BIN || undefined });
    await flow(browser, 'desktop', { width: 1280, height: 900 });
    await flow(browser, 'mobile-390', { width: 390, height: 844 });
    console.log('SCREENSHOTS=' + output);
  } finally {
    if (browser) await browser.close();
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
