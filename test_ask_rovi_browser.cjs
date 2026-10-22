// Exercise Ask Rovi with synthetic accounts and a local-only API transport.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const { chromium, webkit, devices } = require('playwright');

const source = process.env.ROVE_FRONTEND_PATH || path.join(__dirname, 'frontend', 'index.html');
const output = process.env.ROVE_ASK_ROVI_OUTPUT || fs.mkdtempSync(path.join(os.tmpdir(), 'rove-ask-rovi-'));
let origin, requests, nextOutcome, nextCoachV4Insight, nextV3Candidate;
let coachReservations, shownCoachInsights;

function reset() {
  requests = [];
  nextOutcome = '';
  nextCoachV4Insight = null;
  nextV3Candidate = null;
  coachReservations = new Map();
  shownCoachInsights = new Set();
}

function state() {
  const result = {
    ok: true, user_id: 1, netWorth: 12500, onboardingRequired: false,
    onboarding: { required: false, status: 'completed', completed: true, step: 10 },
    identity: { name: 'Synthetic Mentor', email: 'synthetic@example.test' },
    features: { multi_cash_accounts_v1: true },
    financialAccounts: [
      { id: 1, name: 'Girokonto', type: 'checking', balance: 12000, legacyKey: 'giro', roles: ['expense', 'income', 'fixed_cost', 'screenshot'] },
      { id: 2, name: 'Bargeld', type: 'wallet', balance: 50, roles: [] },
      { id: 3, name: 'Bargeld unterwegs', type: 'wallet', balance: 75, roles: [] },
    ],
    assets: [
      { name: 'Girokonto', value: 12000, source: 'bot', icon: 'bank', financialAccountId: 1, assetKey: 'cash-account:1' },
      { name: 'Bargeld', value: 50, source: 'bot', icon: 'cash', financialAccountId: 2, assetKey: 'cash-account:2' },
      { name: 'Bargeld unterwegs', value: 75, source: 'bot', icon: 'cash', financialAccountId: 3, assetKey: 'cash-account:3' },
    ],
    tx: [], valuables: [], budgets: [], goals: [], vertraege: [], reports: [],
    sts: { konto: 12125, income: 3000, fixRest: 0, sparraten: 0, available: 12125,
      free_month_remaining: 12125, financial_month_budget: 0, variableMonthExpenses: 0,
      category_budget_total: 0, category_spent: 0, monthExpenses: 0 },
    monthlyPlan: { incomeStatus: 'confirmed', savingsStatus: 'confirmed', status: 'confirmed' },
    mentor_candidate: nextV3Candidate,
    coach_v4_insight: nextCoachV4Insight,
  };
  if (nextCoachV4Insight) {
    result.tx = [{ d: 'Heute', items: [{ a: -4.5, n: 'Café', cat: 'Essen', classification: 'consumption' }] }];
    result.budgets = [{ cat: 'Essen', limit: 500, source: 'bot' }];
  }
  return result;
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
    if (apiPath === '/v1/state') return json(state());
    if (apiPath.startsWith('/v1/coach/insights/')) {
      let raw = '';
      for await (const chunk of req) raw += chunk;
      const payload = JSON.parse(raw || '{}');
      const key = `${payload.insight_id}:${payload.evidence_fingerprint}`;
      const insight = nextCoachV4Insight;
      const matches = insight && payload.insight_id === insight.insight_id &&
        payload.evidence_fingerprint === insight.evidence_fingerprint;
      if (!matches) return json({ ok: false, error: 'insight_not_current' }, 409);
      if (apiPath.endsWith('/claim')) {
        if (shownCoachInsights.has(key)) return json({ ok: true, status: 'suppressed' });
        const existing = coachReservations.get(key);
        if (existing && existing !== payload.reservation_token) {
          return json({ ok: true, status: 'reserved_elsewhere', retry_after_seconds: 1 });
        }
        coachReservations.set(key, payload.reservation_token);
        return json({ ok: true, status: 'reserved' });
      }
      if (apiPath.endsWith('/acknowledged')) {
        if (coachReservations.get(key) !== payload.reservation_token) {
          return json({ ok: false, error: 'reservation_missing' }, 409);
        }
        shownCoachInsights.add(key);
        return json({ ok: true, acknowledged: true });
      }
      if (apiPath.endsWith('/opened')) {
        return shownCoachInsights.has(key)
          ? json({ ok: true, opened: true })
          : json({ ok: false, error: 'insight_not_acknowledged' }, 409);
      }
      return json({ ok: false, error: 'synthetic_route_not_found' }, 404);
    }
    if (apiPath === '/v1/ai/chat' && req.method === 'POST') {
      let raw = '';
      for await (const chunk of req) raw += chunk;
      const payload = JSON.parse(raw);
      requests.push(payload);
      const outcome = nextOutcome;
      nextOutcome = '';
      await new Promise(resolve => setTimeout(resolve, 220));
      if (outcome === 'server-error') return json({ ok: false, error: 'synthetic_unavailable' }, 503);
      if (payload.insight_id) {
        const answer = payload.message.includes('Warum')
          ? nextCoachV4Insight.browserAnswers.why
          : payload.message.includes('Welche')
            ? nextCoachV4Insight.browserAnswers.evidence
            : nextCoachV4Insight.browserAnswers.action;
        return json({ ok: true, kind: 'rove', intent: 'coach_v4_insight', insight_id: payload.insight_id,
          conversation_id: 'synthetic-conversation', answer });
      }
      if (payload.message.toLowerCase().includes('bargeld')) {
        return json({ ok: true, kind: 'rove', intent: 'wallet_cash',
          conversation_id: 'synthetic-conversation', answer: 'Du hast aktuell 125,00 € Bargeld.' });
      }
      return json({ ok: true, kind: 'rove', conversation_id: 'synthetic-conversation', answer: 'Antwort nach erneutem Versuch.' });
    }
    return json({ ok: false, error: 'synthetic_route_not_found' }, 404);
  }
  const file = url.pathname === '/app/' ? 'index.html' : url.pathname.slice('/app/'.length);
  if (!url.pathname.startsWith('/app/') || !['index.html', 'logo.png', 'app-icon.png', 'manifest.webmanifest', 'sw.js'].includes(file)) {
    res.writeHead(404).end(); return;
  }
  let body = fs.readFileSync(file === 'index.html' ? source : path.join(__dirname, 'frontend', file));
  if (file === 'index.html') body = body.toString().replace('"https://getrove.de/app-api"', JSON.stringify(origin + '/__api'));
  const mime = { '.html': 'text/html; charset=utf-8', '.png': 'image/png', '.js': 'application/javascript', '.webmanifest': 'application/manifest+json' };
  res.writeHead(200, { 'Content-Type': mime[path.extname(file)], 'Cache-Control': 'no-store' });
  res.end(body);
});

async function flow(browser, label, viewport, standalone = false) {
  reset();
  const mobile = viewport.width === 390;
  const context = await browser.newContext({ viewport, serviceWorkers: 'block',
    ...(mobile ? { isMobile: true, hasTouch: true, userAgent: devices['iPhone 13'].userAgent } : {}) });
  if (standalone) await context.addInitScript(() => {
    Object.defineProperty(navigator, 'standalone', { get: () => true, configurable: true });
  });
  const failedRequests = [];
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin === origin && url.pathname === '/__api/v1/ai/chat' && nextOutcome === 'network-error') {
      nextOutcome = '';
      return route.abort('failed');
    }
    return url.origin === origin ? route.continue() : route.abort('blockedbyclient');
  });
  const page = await context.newPage(), errors = [];
  page.setDefaultTimeout(10000);
  page.on('pageerror', error => errors.push(error.message));
  page.on('requestfailed', request => failedRequests.push(request.url()));
  try {
    await page.goto(origin + '/app/');
    await page.locator('#app:not([hidden])').waitFor();
    await page.locator('#splash').waitFor({ state: 'detached' });
    await page.waitForFunction(() => BRIDGE_USER_ID === 1 && DATA.financialAccounts.length === 3);
    if (standalone) assert.equal(await page.locator('html.is-standalone').count(), 1);

    await page.locator('#tab-home .mentor').click();
    await page.locator('#talksheet.on').waitFor();
    const input = page.locator('#talkIn');
    await input.focus();

    await input.fill('Wie viel Bargeld habe ich?');
    await page.locator('#talkSend').click();
    await page.getByText('Du hast aktuell 125,00 € Bargeld.', { exact: true }).waitFor();
    assert.equal(requests.length, 1, 'explicit wallet questions must use canonical API');
    assert.equal(requests[0].message, 'Wie viel Bargeld habe ich?');
    assert.equal(await input.inputValue(), '');

    nextOutcome = 'server-error';
    const serverQuestion = 'Was bedeutet Duration bei Anleihen?';
    await input.fill(serverQuestion);
    await page.locator('#talkSend').click();
    await page.getByText(/Rov\.E kann gerade nicht antworten/, { exact: false }).waitFor();
    assert.equal(await input.inputValue(), serverQuestion, 'server failure must preserve the prompt');
    assert.equal(await page.locator('.bub.me').filter({ hasText: serverQuestion }).count(), 1);

    await input.press('Enter');
    await page.getByText('Antwort nach erneutem Versuch.', { exact: true }).waitFor();
    assert.equal(await input.inputValue(), '', 'successful retry should clear only the submitted prompt');
    assert.equal(await page.locator('.bub.me').filter({ hasText: serverQuestion }).count(), 1, 'retry must reuse the original user bubble');
    assert.equal(await page.locator('.bub.rov').filter({ hasText: 'Antwort nach erneutem Versuch.' }).count(), 1);

    nextOutcome = 'network-error';
    const networkQuestion = 'Wie funktioniert ein Anleiheindex?';
    const requestsBeforeNetworkFailure = requests.length;
    await input.fill(networkQuestion);
    await page.locator('#talkSend').click();
    await page.getByText(/Verbindung zu Rov\.E ist fehlgeschlagen/, { exact: false }).waitFor();
    assert.equal(requests.length, requestsBeforeNetworkFailure, 'aborted network request must not reach the API fixture');
    assert.equal(failedRequests.filter(url => url.includes('/v1/ai/chat')).length, 1);
    assert.equal(await input.inputValue(), networkQuestion, 'network failure must preserve the prompt');
    assert.equal(await page.locator('.bub.me').filter({ hasText: networkQuestion }).count(), 1);

    await input.press('Enter');
    await page.waitForFunction(() => [...document.querySelectorAll('.bub.rov')]
      .filter(node => node.textContent === 'Antwort nach erneutem Versuch.').length === 2);
    assert.equal(await page.locator('.bub.me').filter({ hasText: networkQuestion }).count(), 1);
    assert.equal(await page.locator('.bub.rov').filter({ hasText: 'Antwort nach erneutem Versuch.' }).count(), 2);

    await input.fill('Erste Frage bei laufender Antwort');
    await page.locator('#talkSend').click();
    await page.waitForTimeout(80);
    await input.fill('Meine neue Frage bleibt erhalten');
    await page.waitForTimeout(900);
    assert.equal(await input.inputValue(), 'Meine neue Frage bleibt erhalten', 'late completion must not erase newer input');
    await input.fill('');

    const beforeDuplicate = requests.length;
    await page.evaluate(async () => {
      const prompt = 'Was ist eine Duration?';
      const results = await Promise.all([askRove(prompt), askRove(prompt)]);
      window.__askRoveDuplicateResults = results;
    });
    await page.waitForFunction(() => [...document.querySelectorAll('.bub.rov')]
      .filter(node => node.textContent === 'Antwort nach erneutem Versuch.').length === 4);
    assert.equal(requests.length, beforeDuplicate + 1, 'concurrent sends must make one API request');
    assert.equal(await page.locator('.bub.me').filter({ hasText: 'Was ist eine Duration?' }).count(), 1);
    assert.deepEqual(errors, []);

    if (mobile) {
      await page.setViewportSize({ width: 390, height: 560 }); // iOS keyboard-sized visual viewport.
      await input.focus();
      await page.waitForTimeout(150);
      const geometry = await page.evaluate(() => {
        const sheet = document.getElementById('talksheet').getBoundingClientRect();
        const field = document.querySelector('#talksheet .field').getBoundingClientRect();
        const feed = document.getElementById('chat');
        const feedRect = feed.getBoundingClientRect();
        const lastBubble = feed.lastElementChild?.getBoundingClientRect();
        return { innerHeight: window.innerHeight, visualHeight: window.visualViewport?.height ?? window.innerHeight,
          sheetBottom: sheet.bottom, fieldBottom: field.bottom,
          feedScrollHeight: feed.scrollHeight, feedClientHeight: feed.clientHeight,
          feedScrollTop: feed.scrollTop, feedMaxScroll: feed.scrollHeight - feed.clientHeight,
          lastBubbleBottom: lastBubble?.bottom, feedBottom: feedRect.bottom };
      });
      assert.ok(geometry.sheetBottom <= geometry.visualHeight + 2, JSON.stringify(geometry));
      assert.ok(geometry.fieldBottom <= geometry.visualHeight + 2, JSON.stringify(geometry));
      assert.ok(geometry.feedScrollHeight > geometry.feedClientHeight, JSON.stringify(geometry));
      assert.ok(Math.abs(geometry.feedMaxScroll - geometry.feedScrollTop) <= 2, JSON.stringify(geometry));
      assert.ok(geometry.lastBubbleBottom <= geometry.feedBottom + 2, JSON.stringify(geometry));
      await page.screenshot({ path: path.join(output, label + '-keyboard-390.png') });
    } else {
      await page.screenshot({ path: path.join(output, label + '-desktop.png') });
    }
    console.log(label.toUpperCase() + '_WALLET_ERROR_RETRY_DEDUP_LAYOUT=PASS');
  } finally {
    await context.close();
  }
}

const mentorScenarios = [
  {
    key: 'delivery-weekday', type: 'repeated_spending_pattern',
    title: 'Wiederkehrende Bestellungen',
    message: 'An 3 von 4 Montagen gab es Buchungen bei Lieferando mit insgesamt 86,00 €.',
    evidenceQuestion: 'Welche Ausgaben meinst du?',
    browserAnswers: {
      why: '3 von 4 Montagen: 3 Buchungen bei Lieferando über 86,00 €; keine Ursache belegt.',
      evidence: '3 Buchungen im Zeitraum, Beispiel Lieferando 30,00 €.',
      action: 'Du kannst die Buchungen prüfen und selbst einen Wochenrahmen wählen.',
    },
  },
  {
    key: 'rising-category', type: 'spending_trend_worsening',
    title: 'Ausgaben steigen',
    message: 'Bei Restaurants stiegen die erfassten Ausgaben über 3 aufeinanderfolgende Monate von 90,00 € auf 200,00 €.',
    evidenceQuestion: 'Welche Ausgaben meinst du?',
    browserAnswers: {
      why: 'Restaurants: Juni 90,00 €, Juli 140,00 €, August 200,00 €; 6 Buchungen, Ursache nicht belegt.',
      evidence: '6 Buchungen im Zeitraum. Beispiel Restaurant 45,00 €.',
      action: 'Du kannst die Buchungen prüfen und selbst über deinen Rahmen entscheiden.',
    },
  },
  {
    key: 'post-income', type: 'post_income_pattern',
    title: 'Ausgaben nach Geldeingang',
    message: 'In 3 Gehaltszyklen lagen Ausgaben im Schnitt bei 200,00 € gegenüber 40,00 € in der Vergleichsbasis. Kein Kausalbeleg.',
    evidenceQuestion: 'Welche Ausgaben meinst du?',
    browserAnswers: {
      why: '3 Zyklen, 200,00 € gegenüber 40,00 €, 6 Buchungen; zeitlicher Zusammenhang, keine Ursache belegt.',
      evidence: '6 Buchungen in den belegten Zeitfenstern. Beispiel Restaurant 100,00 €.',
      action: 'Du kannst nach Geldeingang erst deine geplanten Ausgaben prüfen.',
    },
  },
  {
    key: 'similar-contracts', type: 'subscription_cluster',
    title: 'Ähnliche laufende Verträge',
    message: '3 laufende Verträge sind als Streaming eingeordnet; Monatswert 75,00 €. Ähnlichkeit beweist kein Doppelabo.',
    evidenceQuestion: 'Welche Verträge meinst du?',
    browserAnswers: {
      why: '3 aktive Services, Monatswert 75,00 €; das belegt weder ein unnötiges Doppelabo noch Ersparnis.',
      evidence: '4 aktive Vertragszeilen: Netflix 25,00 € pro Monat; Ähnlichkeit beweist kein Doppelabo.',
      action: 'Du kannst einzeln prüfen, ob du die Verträge noch nutzt. Rov.E kündigt nichts.',
    },
  },
  {
    key: 'budget-recovery', type: 'budget_recovery',
    title: 'Budget-Recovery bei Restaurants',
    message: 'Im abgeschlossenen Monat lagen die Ausgaben bei 40,00 € von 100,00 €. Zuvor lag Restaurants in 3 Monaten über dem Rahmen.',
    evidenceQuestion: 'Welche Ausgaben meinst du?',
    browserAnswers: {
      why: 'Restaurants: 40,00 € von 100,00 €; zuvor Juni, Juli und August über dem Rahmen; keine Ursache belegt.',
      evidence: '2 Buchungen im abgeschlossenen Monat. Beispiel Restaurant 20,00 €.',
      action: 'Wenn der Rahmen passt, kannst du deinen bisherigen Umgang beibehalten.',
    },
  },
];

async function mentorInsightFlow(browser, label, viewport, standalone = false, scenario = mentorScenarios[0]) {
  reset();
  nextCoachV4Insight = {
    insight_id: 'v4:shadow:behavior_insight:synthetic-' + scenario.key,
    evidence_fingerprint: 'a'.repeat(64),
    insight_type: scenario.type,
    title: scenario.title,
    message: scenario.message,
    facts: { category: 'Essen', period: { start: '2026-10-01', end: '2026-10-21' } },
    browserAnswers: scenario.browserAnswers,
  };
  const mobile = viewport.width === 390;
  const context = await browser.newContext({ viewport, serviceWorkers: 'block',
    ...(mobile ? { isMobile: true, hasTouch: true, userAgent: devices['iPhone 13'].userAgent } : {}) });
  if (standalone) await context.addInitScript(() => {
    Object.defineProperty(navigator, 'standalone', { get: () => true, configurable: true });
  });
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    return url.origin === origin ? route.continue() : route.abort('blockedbyclient');
  });
  const page = await context.newPage();
  page.setDefaultTimeout(10000);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    await page.goto(origin + '/app/');
    await page.locator('#app:not([hidden])').waitFor();
    await page.locator('#splash').waitFor({ state: 'detached' });
    await page.waitForFunction(id => DATA.coachV4Insight?.insight_id === id,
      nextCoachV4Insight.insight_id);
    const mentor = page.locator('#tab-home .mentor');
    await page.getByText(scenario.title, { exact: true }).waitFor();
    assert.equal(await mentor.getAttribute('data-coach-v4-insight-id'), nextCoachV4Insight.insight_id);
    await page.evaluate(() => {
      DATA.mentorCandidate = { type: 'budget_overrun', priority: 100,
        title: 'Dein Monatsplan braucht Aufmerksamkeit', message: 'Prüfe den aktuellen Rahmen.' };
      mentorLine();
    });
    await page.getByText('Dein Monatsplan braucht Aufmerksamkeit', { exact: true }).waitFor();
    assert.equal(await mentor.getAttribute('data-coach-v4-insight-id'), null);
    await page.evaluate(() => { DATA.mentorCandidate = null; mentorLine(); });
    await page.evaluate(() => {
      DATA.monthlyPlan = { ...DATA.monthlyPlan, incomeStatus: 'pending' };
      DATA.payday = { gebucht: false, faellig: true };
      mentorLine();
    });
    await page.getByText(/Gehalt ist für diesen Monat noch nicht bestätigt/, { exact: false }).waitFor();
    assert.equal(await mentor.getAttribute('data-coach-v4-insight-id'), null);
    await page.evaluate(() => {
      DATA.monthlyPlan.incomeStatus = 'confirmed';
      mentorLine();
    });
    await page.getByText(scenario.title, { exact: true }).waitFor();
    assert.equal(await mentor.getAttribute('data-coach-v4-insight-id'), nextCoachV4Insight.insight_id);
    if (standalone) assert.equal(await page.locator('html.is-standalone').count(), 1);
    if (standalone || !mobile) await mentor.locator('[data-mentor-cta]').click();
    else await mentor.click();
    await page.locator('#talksheet.on').waitFor();
    await page.getByText(/Du hast den Hinweis/, { exact: false }).waitFor();
    assert.equal(await mentor.getAttribute('data-coach-v4-insight-id'), null, 'opened insight is suppressed for today');
    for (const question of ['Warum sagst du mir das?', scenario.evidenceQuestion, 'Was kann ich konkret ändern?']) {
      await page.locator('#qsug .qs').filter({ hasText: question }).waitFor();
    }

    nextOutcome = 'server-error';
    await page.locator('#qsug .qs').filter({ hasText: 'Warum sagst du mir das?' }).click();
    await page.getByText(/Rov\.E kann gerade nicht antworten/, { exact: false }).waitFor();
    assert.equal(await page.locator('.bub.me').filter({ hasText: 'Warum sagst du mir das?' }).count(), 1);
    await page.locator('#qsug .qs').filter({ hasText: 'Warum sagst du mir das?' }).click();
    await page.getByText(scenario.browserAnswers.why, { exact: true }).waitFor();
    assert.equal(await page.locator('.bub.me').filter({ hasText: 'Warum sagst du mir das?' }).count(), 1);
    await page.locator('#qsug .qs').filter({ hasText: scenario.evidenceQuestion }).click();
    await page.getByText(scenario.browserAnswers.evidence, { exact: true }).waitFor();
    await page.locator('#qsug .qs').filter({ hasText: 'Was kann ich konkret ändern?' }).click();
    await page.getByText(scenario.browserAnswers.action, { exact: true }).waitFor();
    assert.equal(requests.length, 4, 'one failed request plus three successful context questions');
    assert.ok(requests.every(request => request.insight_id === nextCoachV4Insight.insight_id));
    assert.equal(errors.length, 0, errors.join('\n'));
    if (mobile) {
      await page.locator('#qsug').evaluate(element => { element.scrollLeft = 0; });
      const chip = await page.locator('#qsug .qs').first().boundingBox();
      assert.ok(chip && chip.x >= 0 && chip.x + chip.width <= viewport.width + 2, JSON.stringify(chip));
      await page.screenshot({ path: path.join(output, label + '-' + scenario.key + '-mentor-insight-390.png') });
    } else {
      await page.screenshot({ path: path.join(output, label + '-' + scenario.key + '-mentor-insight-desktop.png') });
    }
    await page.reload();
    await page.locator('#app:not([hidden])').waitFor();
    await page.locator('#splash').waitFor({ state: 'detached' });
    await page.waitForFunction(() => BRIDGE_USER_ID === 1 &&
      DATA.financialAccounts.length === 3 && DATA.coachV4Insight === null);
    assert.equal(await mentor.getAttribute('data-coach-v4-insight-id'), null, 'reload must not repeat the opened insight');
    console.log(label.toUpperCase() + '_' + scenario.key.toUpperCase() + '_V4_MENTOR_CONTEXT_RETRY=PASS');
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
    for (const scenario of mentorScenarios) {
      await mentorInsightFlow(browser, 'desktop', { width: 1280, height: 900 }, false, scenario);
      await mentorInsightFlow(browser, 'iphone-pwa-390', { width: 390, height: 844 }, true, scenario);
    }
    console.log('SCREENSHOTS=' + output);
  } finally {
    if (browser) await browser.close();
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
