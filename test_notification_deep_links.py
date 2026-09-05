import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

import report_engine
from rove_app_api import normalize_notification_target, safe_legacy_push_url


ROOT = Path(__file__).resolve().parent
FRONTEND = ROOT / "frontend" / "index.html"
WORKER = ROOT / "frontend" / "sw.js"


def function_source(source: str, name: str) -> str:
    marker = f"function {name}("
    start = source.find(marker)
    if start < 0:
        raise AssertionError(f"missing function {name}")
    brace = source.find("{", start)
    depth = 0
    quote = None
    escaped = False
    for index in range(brace, len(source)):
        char = source[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in {'"', "'", "`"}:
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError(f"unclosed function {name}")


@unittest.skipUnless(shutil.which("node"), "Node.js is required for service-worker tests")
class NotificationDeepLinkFrontendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frontend = FRONTEND.read_text(encoding="utf-8")
        cls.worker = WORKER.read_text(encoding="utf-8")

    def run_node(self, source: str) -> dict:
        result = subprocess.run(
            ["node", "-e", source], check=True, capture_output=True, text=True
        )
        return json.loads(result.stdout)

    def test_frontend_parser_defers_until_pin_then_opens_matching_report(self):
        functions = "\n".join(function_source(self.frontend, name) for name in (
            "normalizeNotificationTarget",
            "notificationTargetFromUrl",
            "queueNotificationTarget",
            "clearNotificationTargetFromUrl",
            "applyNotificationTarget",
            "consumePendingNotificationTarget",
        ))
        script = f"""
let APP_MODE="bridge", PIN_SESSION_STATE="locked", PENDING_NOTIFICATION_TARGET=null;
const DATA={{reports:[{{month:"2026-08"}},{{month:"2026-07"}}]}};
const calls=[];
const location={{href:"https://getrove.de/app/?rove_target=report&month=2026-08",origin:"https://getrove.de",pathname:"/app/"}};
const history={{replaceState:(_,__,value)=>calls.push(["replace",value])}};
function roveIsState(){{return false;}}
function roveActiveTab(){{return "home";}}
function openReports(){{calls.push(["reports"]);}}
function openReportDetail(index){{calls.push(["detail",index]);}}
function openMonthlyPlan(){{calls.push(["monthly"]);}}
function openAnalysis(){{calls.push(["analysis"]);}}
function closeSheet(){{calls.push(["close"]);}}
function go(tab){{calls.push(["go",tab]);}}
{functions}
const parsed=notificationTargetFromUrl();
const queued=queueNotificationTarget(parsed);
const before=consumePendingNotificationTarget();
PIN_SESSION_STATE="unlocked";
const after=consumePendingNotificationTarget();
const consumedAgain=consumePendingNotificationTarget();
const malformed=normalizeNotificationTarget({{type:"report",month:"2026-13"}});
const arbitrary=notificationTargetFromUrl("https://outside.example/?rove_target=report&month=2026-08");
PENDING_NOTIFICATION_TARGET={{type:"report",month:"2025-01"}};
const missing=consumePendingNotificationTarget();
process.stdout.write(JSON.stringify({{parsed,queued,before,after,consumedAgain,malformed,arbitrary,missing,calls}}));
"""
        result = self.run_node(script)
        self.assertEqual(result["parsed"], {"type": "report", "month": "2026-08"})
        self.assertTrue(result["queued"])
        self.assertFalse(result["before"])
        self.assertTrue(result["after"])
        self.assertFalse(result["consumedAgain"])
        self.assertIsNone(result["malformed"])
        self.assertIsNone(result["arbitrary"])
        self.assertTrue(result["missing"])
        self.assertEqual(result["calls"], [
            ["replace", "/app/"], ["reports"], ["detail", 0],
            ["replace", "/app/"], ["reports"],
        ])

    def test_worker_preserves_target_for_open_window_and_safe_url_for_closed_window(self):
        script = f"""
const source={json.dumps(self.worker)};
async function scenario(existing,target,legacyUrl="./") {{
  const listeners={{}}, messages=[], opened=[];
  const client={{
    focus:()=>Promise.resolve(),
    postMessage:(message)=>messages.push(message),
  }};
  global.self={{
    registration:{{scope:"https://getrove.de/app/",showNotification:()=>Promise.resolve()}},
    clients:{{
      matchAll:()=>Promise.resolve(existing?[client]:[]),
      openWindow:(url)=>{{opened.push(url);return Promise.resolve();}},
    }},
    addEventListener:(type,handler)=>{{listeners[type]=handler;}},
    skipWaiting:()=>Promise.resolve(),
  }};
  eval(source);
  let pending;
  listeners.notificationclick({{
    notification:{{close:()=>{{}},data:{{target,legacyUrl}}}},
    waitUntil:(promise)=>{{pending=promise;}},
  }});
  await pending;
  return {{messages,opened}};
}}
(async()=>{{
  const open=await scenario(true,{{type:"report",month:"2026-08"}});
  const closed=await scenario(false,{{type:"report",month:"2026-08"}});
  const invalid=await scenario(false,{{type:"report",month:"2026-13",url:"https://outside.example"}});
  const legacy=await scenario(false,null,"./#add");
  process.stdout.write(JSON.stringify({{open,closed,invalid,legacy}}));
}})();
"""
        result = self.run_node(script)
        self.assertEqual(result["open"]["messages"], [{
            "type": "rove:notification-target",
            "target": {"type": "report", "month": "2026-08"},
        }])
        self.assertEqual(result["open"]["opened"], [])
        self.assertEqual(result["closed"]["opened"], [
            "https://getrove.de/app/?rove_target=report&month=2026-08"
        ])
        self.assertEqual(result["invalid"]["opened"], ["https://getrove.de/app/"])
        self.assertEqual(result["legacy"]["opened"], ["https://getrove.de/app/#add"])


class NotificationDeepLinkServerTests(unittest.TestCase):
    def test_server_target_allowlist_and_legacy_urls_reject_arbitrary_navigation(self):
        self.assertEqual(
            normalize_notification_target({"type": "report", "month": "2026-08"}),
            {"type": "report", "month": "2026-08"},
        )
        self.assertEqual(normalize_notification_target({"type": "analysis"}), {"type": "analysis"})
        self.assertIsNone(normalize_notification_target({"type": "report", "month": "2026-13"}))
        self.assertIsNone(normalize_notification_target({"type": "report", "month": "2026-08", "id": "1"}))
        self.assertEqual(safe_legacy_push_url("./"), "./")
        self.assertEqual(safe_legacy_push_url("./#add"), "./#add")
        self.assertIsNone(safe_legacy_push_url("https://outside.example"))

    def test_report_push_contains_only_the_report_month_target(self):
        captured = {}

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self):
                return b'{"ok": true, "sent": 1}'

        def fake_urlopen(request, timeout):
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            captured["timeout"] = timeout
            return Response()

        with patch.object(report_engine, "APP_PUSH_INTERNAL_SECRET", "test-secret"), patch(
            "report_engine.urllib.request.urlopen", side_effect=fake_urlopen
        ):
            report_engine.send_report_push(7, "2026-08")

        self.assertEqual(captured["timeout"], 8)
        self.assertEqual(captured["payload"]["target"], {"type": "report", "month": "2026-08"})
        self.assertNotIn("report_data", captured["payload"])


if __name__ == "__main__":
    unittest.main()
