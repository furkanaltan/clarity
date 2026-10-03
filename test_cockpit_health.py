import hashlib
import json
import pathlib
import re
import subprocess
import unittest


ROOT = pathlib.Path(__file__).resolve().parent
COCKPIT = ROOT / "cockpit" / "cockpit.html"

NODE_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');

const scenario = JSON.parse(process.argv[1]);
const html = fs.readFileSync(process.argv[2], 'utf8');
const script = html.match(/<script>\s*([\s\S]*?)<\/script>/)?.[1];
if (!script) throw new Error('Cockpit script missing');

const elements = new Map();
function element(id) {
  if (!elements.has(id)) {
    elements.set(id, {
      textContent: '', className: '', style: {}, children: [], hidden: false,
      set innerHTML(value) { this.markup = value; this.children = []; },
      get innerHTML() { return this.markup || ''; },
      classList: { add() {}, remove() {} },
      appendChild(child) { this.children.push(child); },
    });
  }
  return elements.get(id);
}
const document = {
  getElementById: element,
  createElement: () => ({ innerHTML: '' }),
};
const calls = [];
async function fetch(url) {
  calls.push(url);
  if (url === '/app-api/health') {
    if (scenario.healthError) throw new Error('network error');
    return { ok: scenario.healthStatus !== 500, json: async () => scenario.health };
  }
  if (scenario.dataError) throw new Error('cockpit data error');
  if (url === '/cockpit-api/api/overview') {
    return { ok: true, json: async () => [{ user_id: 1, name: 'Test', status: 'aktiv', expense_count: 3, onboarding_step: 10 }] };
  }
  if (url === '/cockpit-api/api/stats') {
    return { ok: true, json: async () => ({ total_expenses: 0, reports_done: 0 }) };
  }
  throw new Error('unexpected request: ' + url);
}
const context = { document, fetch, AbortSignal, Date, Intl, setInterval() {} };
let chartConstructed = 0, chartDestroyed = 0, chartConfig = null, activeChart = null;
if (scenario.chartAvailable || scenario.chartThrows) {
  context.Chart = class {
    constructor(canvas, config) {
      chartConstructed++;
      chartConfig = config;
      activeChart = this;
      if (scenario.chartThrows) throw new Error('chart render error');
    }
    destroy() { chartDestroyed++; activeChart = null; }
    static getChart() { return activeChart; }
  };
}
const vmContext = vm.createContext(context);
vm.runInContext(script, vmContext);
setTimeout(async () => {
  if (scenario.refresh) await vm.runInContext('loadAll()', vmContext);
  process.stdout.write(JSON.stringify({
    status: element('subtitle').textContent,
    error: element('err-banner').textContent,
    usersRendered: element('tester-list').children.length,
    chartHidden: element('actChart').hidden,
    fallbackHidden: element('actChartFallback').hidden,
    chartConstructed, chartDestroyed, chartConfig,
    calls,
  }));
}, 0);
"""


class CockpitHealthTests(unittest.TestCase):
    def run_cockpit(self, **scenario):
        result = subprocess.run(
            ["node", "-e", NODE_HARNESS, json.dumps(scenario), str(COCKPIT)],
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(result.stdout)

    def test_minimal_health_is_online_and_users_render_without_chart(self):
        result = self.run_cockpit(health={"ok": True, "service": "rove-app-api"})
        self.assertEqual(result["status"], "Backend online")
        self.assertEqual(result["usersRendered"], 1)
        self.assertEqual(result["error"], "")
        self.assertTrue(result["chartHidden"])
        self.assertFalse(result["fallbackHidden"])

    def test_extra_legacy_health_fields_are_optional(self):
        result = self.run_cockpit(health={"ok": True, "marketDataConfigured": False})
        self.assertEqual(result["status"], "Backend online")

    def test_http_500_is_offline(self):
        result = self.run_cockpit(healthStatus=500, health={"ok": True})
        self.assertEqual(result["status"], "Backend nicht erreichbar")
        self.assertEqual(result["calls"], ["/app-api/health"])

    def test_network_error_is_offline(self):
        result = self.run_cockpit(healthError=True)
        self.assertEqual(result["status"], "Backend nicht erreichbar")

    def test_ok_false_is_offline(self):
        result = self.run_cockpit(health={"ok": False})
        self.assertEqual(result["status"], "Backend nicht erreichbar")

    def test_invalid_health_response_is_offline(self):
        result = self.run_cockpit(health={"service": "rove-app-api"})
        self.assertEqual(result["status"], "Backend nicht erreichbar")

    def test_cockpit_data_error_does_not_mislabel_app_backend(self):
        result = self.run_cockpit(health={"ok": True}, dataError=True)
        self.assertEqual(result["status"], "Backend online")
        self.assertIn("Cockpit-Daten", result["error"])

    def test_chart_still_renders_when_library_is_available(self):
        result = self.run_cockpit(health={"ok": True}, chartAvailable=True)
        self.assertEqual(result["status"], "Backend online")
        self.assertEqual(result["usersRendered"], 1)
        self.assertEqual(result["chartConstructed"], 1)
        self.assertEqual(result["chartConfig"]["type"], "bar")
        self.assertEqual(result["chartConfig"]["data"]["datasets"][0]["data"], [3])
        self.assertFalse(result["chartHidden"])
        self.assertTrue(result["fallbackHidden"])

    def test_chart_render_failure_keeps_online_user_list_and_fallback(self):
        result = self.run_cockpit(health={"ok": True}, chartThrows=True)
        self.assertEqual(result["status"], "Backend online")
        self.assertEqual(result["usersRendered"], 1)
        self.assertEqual(result["error"], "")
        self.assertEqual(result["chartDestroyed"], 1)
        self.assertTrue(result["chartHidden"])
        self.assertFalse(result["fallbackHidden"])

    def test_refresh_replaces_chart_without_accumulating_instances(self):
        result = self.run_cockpit(health={"ok": True}, chartAvailable=True, refresh=True)
        self.assertEqual(result["status"], "Backend online")
        self.assertEqual(result["usersRendered"], 1)
        self.assertEqual(result["chartConstructed"], 2)
        self.assertEqual(result["chartDestroyed"], 1)

    def test_chart_asset_is_local_and_pinned_to_existing_version(self):
        source = COCKPIT.read_text(encoding="utf-8")
        self.assertIn('src="./vendor/chartjs-4.4.1/chart.umd.js"', source)
        asset = ROOT / "cockpit/vendor/chartjs-4.4.1/chart.umd.js"
        self.assertEqual(
            hashlib.sha256(asset.read_bytes()).hexdigest(),
            "74401d738dd3e03ee5dfb3b6841210fe2c4ead8a960c4011ca4ba0b78a9fd8f3",
        )

    def test_scripts_styles_and_font_sources_use_local_assets(self):
        source = COCKPIT.read_text(encoding="utf-8")
        self.assertNotRegex(source, r'(?:src|href)=["\'](?:https?:)?//')
        fonts = ROOT / "cockpit/vendor/fonts"
        font_css = (fonts / "fonts.css").read_text(encoding="utf-8")
        self.assertNotRegex(font_css, r"https?://")
        urls = set(re.findall(r"url\(([^)]+)\)", font_css))
        self.assertEqual(len(urls), 13)
        for url in urls:
            self.assertTrue(url.startswith("./"))
            self.assertEqual((fonts / url).read_bytes()[:4], b"wOF2")


if __name__ == "__main__":
    unittest.main()
