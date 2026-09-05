import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import report_engine
import rove_app_api as api
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


class PushEndpointSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "clarity.db"
        with sqlite3.connect(self.db_path) as conn:
            api.ensure_push_table(conn)
        self.patchers = [
            patch.object(api, "DB_PATH", self.db_path),
            patch.object(api, "PUSH_LIB_OK", True),
            patch.object(api, "VAPID_PUBLIC_KEY", "public-key"),
            patch.object(api, "VAPID_PRIVATE_KEY", "private-key"),
            patch.object(api, "user_from_token", lambda _conn, token: 1 if token == "test-token" else None),
        ]
        for patcher in self.patchers:
            patcher.start()
        api.app.config.update(TESTING=True)

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    @staticmethod
    def subscription(endpoint):
        return {"endpoint": endpoint, "keys": {"p256dh": "test-p256dh", "auth": "test-auth"}}

    def subscribe(self, endpoint):
        with api.app.test_client() as client:
            return client.post(
                "/v1/push/subscribe",
                json=self.subscription(endpoint),
                headers={"Authorization": "Bearer test-token", "Origin": "https://getrove.de"},
            )

    def test_registration_accepts_supported_browser_push_hosts(self):
        for host in (
            "https://fcm.googleapis.com/fcm/send/token",
            "https://updates.push.services.mozilla.com/wpush/v2/token",
            "https://web.push.apple.com/QH/token",
            "https://db3.notify.windows.com/?token=opaque",
        ):
            response = self.subscribe(host)
            self.assertEqual(response.status_code, 200, response.get_json())

    def test_registration_rejects_non_push_and_private_endpoints(self):
        invalid = (
            "http://fcm.googleapis.com/fcm/send/token",
            "https://localhost/push",
            "https://127.0.0.1/push",
            "https://10.0.0.8/push",
            "https://[::1]/push",
            "https://[fe80::1]/push",
            "https://[fd00::1]/push",
            "https://example.test/push",
            "https://fcm.googleapis.com:8443/fcm/send/token",
            "https://fcm.googleapis.com/",
        )
        for endpoint in invalid:
            response = self.subscribe(endpoint)
            self.assertEqual(response.status_code, 400, endpoint)
            self.assertEqual(response.get_json()["error"], "invalid_push_endpoint")

    def test_invalid_legacy_subscription_is_removed_without_network_request(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO app_push_subscriptions (user_id, endpoint, p256dh, auth) VALUES (1, ?, 'p', 'a')",
                ("https://127.0.0.1/private",),
            )
        with api.db() as conn, patch.object(api, "webpush") as send:
            self.assertEqual(api.send_push_to_user(conn, 1, "Titel", "Text"), 0)
            send.assert_not_called()
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM app_push_subscriptions").fetchone()[0], 0)

    def test_valid_send_uses_a_no_redirect_session(self):
        endpoint = "https://fcm.googleapis.com/fcm/send/token"
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO app_push_subscriptions (user_id, endpoint, p256dh, auth) VALUES (1, ?, 'p', 'a')",
                (endpoint,),
            )
        with api.db() as conn, patch.object(api, "webpush") as send:
            self.assertEqual(api.send_push_to_user(conn, 1, "Titel", "Text"), 1)
        self.assertEqual(send.call_args.kwargs["subscription_info"]["endpoint"], endpoint)
        session = send.call_args.kwargs["requests_session"]
        with patch.object(api.requests.Session, "request", return_value="ok") as request:
            self.assertEqual(session.request("POST", endpoint), "ok")
        self.assertFalse(request.call_args.kwargs["allow_redirects"])


if __name__ == "__main__":
    unittest.main()
