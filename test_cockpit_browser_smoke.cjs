const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const { chromium } = require('playwright');

const root = path.join(__dirname, 'cockpit');
const output = process.env.ROVE_COCKPIT_SMOKE_OUTPUT || fs.mkdtempSync(path.join(os.tmpdir(), 'rove-cockpit-smoke-'));
const csp = "default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'self'; form-action 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: https://cdn.brandfetch.io; font-src 'self' data:; connect-src 'self'; frame-src 'none'; worker-src 'self';";
const userDefaults = {
  username: '', last_active: '2026-09-30', streak_days: 2, clarity_points: 100,
  income: 0, fixed_costs: 0, etf_savings: 0, cash_savings: 0, goal: '',
  goal_amount: 0, score: 0, proof_days: 0, badge_count: 0,
};
const users = [
  { ...userDefaults, user_id: 1, name: 'Testnutzer Ä Ö Ü ß', display_name: 'Testnutzer', status: 'aktiv', expense_count: 3, onboarding_step: 10, onboarding_done: true, report_status: 'done', report_month: '2026-09' },
  { ...userDefaults, user_id: 2, name: 'Zweiter Testnutzer', display_name: 'Zweiter Testnutzer', status: 'still', expense_count: 7, onboarding_step: 10, onboarding_done: true, report_status: '—' },
];

const server = http.createServer(async (request, response) => {
  response.setHeader('Content-Security-Policy', csp);
  response.setHeader('X-Content-Type-Options', 'nosniff');
  const uri = new URL(request.url, 'http://localhost').pathname;
  const payloads = {
    '/app-api/health': { ok: true, service: 'rove-app-api' },
    '/cockpit-api/api/overview': users,
    '/cockpit-api/api/stats': { total_expenses: 10, reports_done: 1 },
    '/cockpit-api/api/expenses/1': [{ amount: 12.5, category: 'Test', description: 'Synthetische Ausgabe', created_at: '2026-09-30 09:00:00' }],
  };
  if (Object.hasOwn(payloads, uri)) {
    response.setHeader('Content-Type', 'application/json');
    response.end(JSON.stringify(payloads[uri]));
    return;
  }
  if (uri === '/favicon.ico') {
    response.writeHead(204).end();
    return;
  }
  const file = path.resolve(root, '.' + uri.slice('/cockpit'.length));
  if (!uri.startsWith('/cockpit/') || !file.startsWith(root + path.sep)) {
    response.writeHead(404).end();
    return;
  }
  try {
    const body = await fs.promises.readFile(file);
    const types = { '.html': 'text/html; charset=utf-8', '.css': 'text/css', '.js': 'text/javascript', '.woff2': 'font/woff2', '.map': 'application/json' };
    response.setHeader('Content-Type', types[path.extname(file)] || 'application/octet-stream');
    response.end(body);
  } catch {
    response.writeHead(404).end();
  }
});

async function observe(context, origin) {
  await context.addInitScript(() => {
    window.cockpitCspViolations = [];
    document.addEventListener('securitypolicyviolation', event => {
      window.cockpitCspViolations.push({ directive: event.effectiveDirective, uri: event.blockedURI });
    });
  });
  const page = await context.newPage();
  const pageErrors = [], consoleErrors = [], externalRequests = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  page.on('console', message => {
    if (message.type() === 'error') consoleErrors.push({ text: message.text(), url: message.location().url });
  });
  page.on('request', request => {
    if (new URL(request.url()).origin !== origin) externalRequests.push(request.url());
  });
  return { page, pageErrors, consoleErrors, externalRequests };
}

async function assertOnline(page) {
  await page.waitForFunction(() => document.getElementById('subtitle').textContent === 'Backend online');
  await page.locator('.trow').first().waitFor({ state: 'visible' });
  assert.equal(await page.locator('.trow').count(), 2);
  assert.equal(await page.locator('#err-banner').isVisible(), false);
  assert.deepEqual(await page.evaluate(() => window.cockpitCspViolations), []);
}

async function assertChart(page) {
  await page.waitForFunction(() => typeof Chart === 'function' && !!Chart.getChart(document.getElementById('actChart')));
  await page.evaluate(() => {
    const chart = Chart.getChart(document.getElementById('actChart'));
    chart.stop();
    chart.update('none');
  });
  const facts = await page.evaluate(() => {
    const canvas = document.getElementById('actChart');
    const chart = Chart.getChart(canvas);
    const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
    return {
      version: Chart.version,
      type: chart.config.type,
      values: [...chart.data.datasets[0].data],
      drawn: pixels.some((value, index) => index % 4 === 3 && value > 0),
      instances: Object.keys(Chart.instances).length,
    };
  });
  assert.deepEqual(facts, { version: '4.4.1', type: 'bar', values: [3, 7], drawn: true, instances: 1 });
  assert.equal(await page.locator('#actChart').isVisible(), true);
  assert.equal(await page.locator('#actChartFallback').isVisible(), false);
}

async function main() {
  fs.mkdirSync(output, { recursive: true });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  let browser;
  try {
    browser = await chromium.launch({ headless: true, executablePath: process.env.ROVE_COCKPIT_CHROME_BIN || undefined });
    const normalContext = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const normal = await observe(normalContext, origin);
    await normal.page.goto(origin + '/cockpit/cockpit.html');
    await assertOnline(normal.page);
    await assertChart(normal.page);
    await normal.page.evaluate(() => document.fonts.ready);
    await normal.page.locator('.trow').first().click();
    await normal.page.locator('#detail-1 .exp-load').click();
    await normal.page.locator('#exp-1 .exp-row').waitFor({ state: 'visible' });
    assert.equal(await normal.page.locator('#exp-1 .exp-amt').innerText(), '12,50 €');
    const firstId = await normal.page.evaluate(() => Chart.getChart(document.getElementById('actChart')).id);
    await normal.page.locator('.refresh-btn').click();
    await normal.page.waitForFunction(id => Chart.getChart(document.getElementById('actChart'))?.id !== id, firstId);
    await assertOnline(normal.page);
    await assertChart(normal.page);
    assert.deepEqual(normal.pageErrors, []);
    assert.deepEqual(normal.consoleErrors, []);
    assert.deepEqual(normal.externalRequests, []);
    await normal.page.screenshot({ path: path.join(output, 'cockpit-online.png'), fullPage: true });
    console.log('DESKTOP_CHART_CSP_USERLIST_REFRESH=PASS');
    await normalContext.close();

    const missingContext = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const chartUrl = origin + '/cockpit/vendor/chartjs-4.4.1/chart.umd.js';
    await missingContext.route(chartUrl, route => route.abort('failed'));
    const missing = await observe(missingContext, origin);
    await missing.page.goto(origin + '/cockpit/cockpit.html');
    await assertOnline(missing.page);
    assert.equal(await missing.page.locator('#actChartFallback').isVisible(), true);
    assert.equal(await missing.page.locator('#actChart').isVisible(), false);
    await missing.page.locator('.refresh-btn').click();
    await assertOnline(missing.page);
    assert.deepEqual(missing.pageErrors, []);
    assert.deepEqual(missing.externalRequests, []);
    // Only the deliberately aborted script request may produce a network error.
    assert.ok(missing.consoleErrors.every(error => error.url === chartUrl && error.text.includes('ERR_FAILED')));
    await missing.page.screenshot({ path: path.join(output, 'cockpit-chart-fallback.png'), fullPage: true });
    console.log('DESKTOP_MISSING_CHART_ONLINE_FALLBACK=PASS');

    await missingContext.unroute(chartUrl);
    await missing.page.addScriptTag({ url: chartUrl });
    await missing.page.evaluate(() => loadAll());
    await assertOnline(missing.page);
    await assertChart(missing.page);
    assert.deepEqual(missing.pageErrors, []);
    console.log('DESKTOP_CHART_RECOVERY=PASS');
    await missingContext.close();
    console.log('SMOKE_OUTPUT=' + output);
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
