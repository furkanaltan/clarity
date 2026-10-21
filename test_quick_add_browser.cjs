// Exercise the real Quick-Add UI with synthetic accounts and a local-only transport.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const { chromium, webkit, devices } = require('playwright');

const root = path.join(__dirname, 'frontend');
const source = process.env.ROVE_FRONTEND_PATH || path.join(root, 'index.html');
const output = process.env.ROVE_QUICK_ADD_OUTPUT || fs.mkdtempSync(path.join(os.tmpdir(), 'rove-quick-add-'));
let origin, entries, requests, nextOutcome, stateUnavailable;

function reset() {
  entries = new Map(); requests = []; nextOutcome = ''; stateUnavailable = false;
}

function state() {
  const total = [...entries.values()].reduce((sum, item) => sum + item.amount, 0);
  const checking = 12500 - total, available = 1990 - total;
  return {
    ok: true, user_id: 1, netWorth: checking + 50, onboardingRequired: false,
    onboarding: { required: false, status: 'completed', completed: true, step: 10 },
    identity: { name: 'Synthetic Quick Add', email: 'synthetic@example.test' },
    features: { multi_cash_accounts_v1: true },
    financialAccounts: [
      { id: 1, name: 'Girokonto', type: 'checking', balance: checking, legacyKey: 'giro', roles: ['expense', 'income', 'fixed_cost', 'screenshot'] },
      { id: 2, name: 'Bargeld', type: 'wallet', balance: 50, legacyKey: 'bargeld', roles: [] },
    ],
    assets: [
      { name: 'Girokonto', value: checking, source: 'bot', icon: 'bank', financialAccountId: 1, assetKey: 'cash-account:1' },
      { name: 'Bargeld', value: 50, source: 'bot', icon: 'cash', financialAccountId: 2, assetKey: 'cash-account:2' },
    ],
    tx: [{ d: 'Heute', items: [
      { id: 10, sid: 10, n: 'Startbuchung', a: -10, cat: 'Restaurant', ts: Date.now() },
      ...[...entries.values()].map(item => ({
        id: item.id, sid: item.id, requestId: item.request_id, n: item.merchant,
        a: -item.amount, cat: item.category, ts: Date.now(),
      })),
    ] }],
    valuables: [], budgets: [{ cat: 'Restaurant', limit: 2000, auto: false }], goals: [], vertraege: [], reports: [],
    sts: { konto: checking + 50, income: 3500, fixRest: 0, sparraten: 0,
      available, free_month_remaining: available, financial_month_budget: 2000,
      variableMonthExpenses: 10 + total, category_budget_total: 2000, category_spent: 10 + total, monthExpenses: 10 + total },
    monthlyPlan: { incomeStatus: 'confirmed', savingsStatus: 'confirmed', status: 'confirmed' },
  };
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, origin);
  const json = (body, status = 200) => {
    res.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
    res.end(JSON.stringify(body));
  };
  if (url.pathname.startsWith('/__api/')) {
    const apiPath = url.pathname.slice('/__api'.length);
    if (apiPath === '/v1/auth/me') return json({ ok: true, user_id: 1 });
    if (apiPath.startsWith('/v1/auth/pin/')) return json({ ok: true, pin_status: 'unlocked', onboarding_required: false });
    if (apiPath.startsWith('/v1/push/')) return json({ ok: true, enabled: false });
    if (apiPath === '/v1/state') return stateUnavailable
      ? json({ ok: false, error: 'synthetic_unavailable' }, 503) : json(state());
    if (apiPath === '/v1/expenses' && req.method === 'POST') {
      let raw = '';
      for await (const chunk of req) raw += chunk;
      const item = JSON.parse(raw);
      requests.push(item);
      const outcome = nextOutcome; nextOutcome = '';
      await new Promise(resolve => setTimeout(resolve, 200));
      if (outcome === 'reject') return json({ ok: false, error: 'synthetic_rejection' }, 422);
      if (outcome === 'uncertain') {
        stateUnavailable = true;
        return json({ ok: false, error: 'synthetic_unavailable' }, 503);
      }
      stateUnavailable = false;
      if (!entries.has(item.request_id)) entries.set(item.request_id, { ...item, id: 100 + entries.size });
      return json({ ok: true, id: entries.get(item.request_id).id, available: state().sts.available });
    }
    return json({ ok: false, error: 'synthetic_route_not_found' }, 404);
  }
  const file = url.pathname === '/app/' ? 'index.html' : url.pathname.slice('/app/'.length);
  if (!url.pathname.startsWith('/app/') || !['index.html', 'logo.png', 'app-icon.png', 'manifest.webmanifest', 'sw.js'].includes(file)) {
    res.writeHead(404).end(); return;
  }
  let body = fs.readFileSync(file === 'index.html' ? source : path.join(root, file));
  if (file === 'index.html') body = body.toString().replace('"https://getrove.de/app-api"', JSON.stringify(origin + '/__api'));
  const mime = { '.html': 'text/html; charset=utf-8', '.png': 'image/png', '.js': 'application/javascript', '.webmanifest': 'application/manifest+json' };
  res.writeHead(200, { 'Content-Type': mime[path.extname(file)], 'Cache-Control': 'no-store' });
  res.end(body);
});

async function snapshot(page) {
  return page.evaluate(() => ({
    tab: roveActiveTab(), scroll: document.querySelector('.screen.active').scrollTop,
    net: document.getElementById('networth').textContent,
    checking: document.querySelector('#assets .asset')?.textContent,
    balance: DATA.assets.find(asset => asset.financialAccountId === 1)?.value,
    free: freeBudget(), daily: freeBudget() / Math.max(1, daysLeftInMonth()),
    budget: document.querySelector('.budget-truth-summary')?.textContent,
  }));
}

async function open(page, raw) {
  await page.locator('#addBtn').click();
  await page.locator('#sheet.on').waitFor();
  await page.locator('#quickIn').fill(raw);
}

async function assertClosedInContext(page, tab) {
  await page.locator('#sheet.on').waitFor({ state: 'detached' });
  // Wait through popstate and sheet transitions, not just the synchronous handler.
  await page.waitForTimeout(450);
  assert.equal(await page.locator('.screen.active').getAttribute('id'), 'tab-' + tab);
  assert.deepEqual(await page.evaluate(() => history.state), { roveNav: true, tab, sheet: null });
  assert.equal(await page.locator('#quickIn').inputValue(), '');
  assert.match(await page.locator('#toast').innerText(), /✓/);
  assert.equal(await page.locator('#toast').evaluate(el => el.classList.contains('on')), true);
}

async function flow(browser, label, viewport, standalone = false) {
  reset();
  const mobile = viewport.width === 390;
  const context = await browser.newContext({ viewport, serviceWorkers: 'block',
    ...(mobile ? { isMobile: true, hasTouch: true, userAgent: devices['iPhone 13'].userAgent } : {}) });
  if (standalone) await context.addInitScript(() => {
    Object.defineProperty(navigator, 'standalone', { get: () => true, configurable: true });
  });
  await context.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort('blockedbyclient'));
  const page = await context.newPage(), errors = [];
  page.setDefaultTimeout(10000);
  page.on('pageerror', error => errors.push(error.message));
  try {
    await page.goto(origin + '/app/');
    await page.locator('#app:not([hidden])').waitFor();
    await page.locator('#splash').waitFor({ state: 'detached' });
    await page.waitForFunction(() => BRIDGE_USER_ID === 1 && DATA.sts.available === 1990);
    if (standalone) assert.equal(await page.locator('html.is-standalone').count(), 1);
    await page.locator('#tab-home').evaluate(el => { el.scrollTop = 60; });
    const before = await snapshot(page);
    await open(page, 'Kaffee 4,50');
    await page.locator('#quickSend').dblclick();
    await assertClosedInContext(page, 'home');
    assert.equal(requests.length, 1, 'Double submit must make one expense request');
    assert.equal(entries.size, 1);
    const after = await snapshot(page);
    assert.equal(after.scroll, before.scroll);
    assert.equal(after.free, before.free - 4.5);
    assert.ok(after.daily < before.daily);
    assert.match(after.net, /12\.545,50/);
    assert.equal(after.balance, before.balance - 4.5);
    assert.match(after.checking, /12\.496/);
    assert.notEqual(after.budget, before.budget);
    await page.screenshot({ path: path.join(output, label + '-dashboard-saved.png') });
    await page.goBack();
    assert.equal(await page.locator('.screen.active').getAttribute('id'), 'tab-home');
    await page.goForward();
    assert.equal(await page.locator('#sheet.on').count(), 0, 'Forward must not reopen the saved form');
    assert.equal(await page.locator('.screen.active').getAttribute('id'), 'tab-home');

    for (const tab of ['ziele', 'tx', 'vertraege']) {
      await page.locator(`.tab[data-tab="${tab}"]`).click();
      const raw = `Kaffee ${requests.length + 2},50`;
      await open(page, raw);
      await page.locator('#quickSend').click();
      await assertClosedInContext(page, tab);
    }

    await page.locator('.tab[data-tab="home"]').click();
    nextOutcome = 'reject';
    await open(page, 'Kaffee 8,50');
    await page.locator('#quickSend').click();
    await page.locator('#quickError.on').waitFor();
    assert.equal(await page.locator('#quickIn').inputValue(), 'Kaffee 8,50');
    assert.equal(await page.locator('.screen.active').getAttribute('id'), 'tab-home');
    assert.match(await page.locator('#toast').innerText(), /Nicht gespeichert/);
    const countBeforeRetry = entries.size;
    await page.locator('#quickSend').click();
    await assertClosedInContext(page, 'home');
    assert.equal(entries.size, countBeforeRetry + 1);

    nextOutcome = 'uncertain';
    await open(page, 'Kaffee 9,50');
    await page.locator('#quickSend').click();
    await page.locator('#quickRetry.on').waitFor();
    const pendingId = requests.at(-1).request_id;
    assert.equal(await page.locator('#quickIn').inputValue(), 'Kaffee 9,50');
    assert.equal(await page.locator('.screen.active').getAttribute('id'), 'tab-home');
    const pendingCount = entries.size;
    await page.locator('#quickRetry').dblclick();
    await assertClosedInContext(page, 'home');
    assert.equal(requests.at(-1).request_id, pendingId);
    assert.equal(requests.filter(item => item.request_id === pendingId).length, 2);
    assert.equal(entries.size, pendingCount + 1);
    await page.reload();
    await page.waitForFunction(() => BRIDGE_USER_ID === 1 && DATA.tx.flatMap(day => day.items).length === 7);
    assert.equal(entries.size, 6);
    assert.deepEqual(errors, []);
    console.log(label.toUpperCase() + '_CONTEXT_SAVE_RETRY_DOUBLE_SUBMIT_RELOAD=PASS');
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
    const engine = process.env.ROVE_BROWSER_ENGINE === 'webkit' ? webkit : chromium;
    browser = await engine.launch({ headless: true, executablePath: process.env.ROVE_BROWSER_BIN || undefined });
    await flow(browser, 'desktop', { width: 1280, height: 900 });
    await flow(browser, 'mobile-390', { width: 390, height: 844 });
    await flow(browser, 'iphone-pwa-390', { width: 390, height: 844 }, true);
    console.log('SCREENSHOTS=' + output);
  } finally {
    if (browser) await browser.close();
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
