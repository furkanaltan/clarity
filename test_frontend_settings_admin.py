"""Focused Settings regressions without production services or customer data."""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path


FRONTEND = Path(__file__).resolve().parent / "frontend" / "index.html"


def function_source(source, name):
    match = re.search(rf"(?m)^(?:async )?function {re.escape(name)}\(", source)
    if not match:
        raise AssertionError(f"Missing function {name}")
    end = source.find("\n}", match.start())
    if end < 0:
        raise AssertionError(f"Unclosed function {name}")
    return source[match.start():end + 2]


@unittest.skipUnless(shutil.which("node"), "Node.js required for inline JS regressions")
class FrontendSettingsAdminTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = FRONTEND.read_text(encoding="utf-8")

    def run_js(self, names, script):
        functions = "\n".join(function_source(self.source, name) for name in names)
        result = subprocess.run(["node", "-e", functions + "\n" + script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_profile_html_is_escaped_and_settings_actions_are_buttons(self):
        result = self.run_js(
            ("escapeAccountHtml", "settingsDataRows", "renderSettings"),
            """
const body={innerHTML:'',querySelector:()=>null};
const document={activeElement:{closest:()=>null},documentElement:{dataset:{}},getElementById:()=>body};
const navigator={};
const DATA={identity:{name:'<img src=x onerror=alert(1)>',email:'a<b@example.test'},sts:{},payday:{}};
const APP_MODE='bridge',SERVER_ONBOARDING_REQUIRED=false,PREFS={haptik:false};
const PUSH={available:true,abo:null,trackingReminder:false};
function announcementUnseenItems(){return [];}
function eur2(n){return String(n);}
renderSettings();
process.stdout.write(JSON.stringify({html:body.innerHTML}));
""",
        )
        html = result["html"]
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", html)
        self.assertNotIn("<img src=x", html)
        self.assertIn("a&lt;b@example.test", html)
        for action in ("name", "logout", "export-data", "delete-account"):
            self.assertIn(f'<button type="button" class="set-row set-action" data-act="{action}"', html)
        self.assertNotIn("Push-Grundlage", html)
        self.assertIn('role="switch" aria-checked="false" data-push-master', html)
        self.assertIn('aria-pressed="true"', html)

    def test_push_reenable_preserves_disabled_tracking_preference(self):
        result = self.run_js(
            ("registerBrowserPush", "togglePush"),
            """
const subscription={endpoint:'https://push.example.test/id',toJSON:()=>({endpoint:'https://push.example.test/id',keys:{p256dh:'x',auth:'y'}})};
const navigator={serviceWorker:{ready:Promise.resolve({pushManager:{getSubscription:async()=>subscription,subscribe:async()=>{throw Error('must not subscribe again');}}})}};
const Notification={requestPermission:async()=>'granted'};
const PUSH={available:true,publicKey:'key',abo:null,trackingReminder:false,erlaubt:false};
let PUSH_ACTION_IN_FLIGHT=false,PUSH_REFRESH_IN_FLIGHT=null,posted=null;
function b64ToUint8(){return new Uint8Array();}
function deviceTimezone(){return 'Europe/Berlin';}
function renderSettings(){}
function showToast(){}
async function apiFetch(path,options){posted=JSON.parse(options.body);return {ok:true,json:async()=>({ok:true,trackingReminder:false,timezone:'Europe/Berlin'})};}
(async()=>{await togglePush();process.stdout.write(JSON.stringify({posted,active:PUSH.abo===subscription,tracking:PUSH.trackingReminder}));})();
""",
        )
        self.assertTrue(result["active"])
        self.assertFalse(result["tracking"])
        self.assertNotIn("trackingReminder", result["posted"])

    def test_push_server_failure_never_looks_active_after_reopen(self):
        result = self.run_js(
            ("registerBrowserPush", "initPush"),
            """
const subscription={endpoint:'https://push.example.test/id',toJSON:()=>({endpoint:'https://push.example.test/id',keys:{p256dh:'x',auth:'y'}})};
const navigator={serviceWorker:{ready:Promise.resolve({pushManager:{getSubscription:async()=>subscription}})}};
const window={PushManager:function(){}};
const Notification={permission:'granted'};
const APP_MODE='bridge';
const PUSH={available:false,publicKey:'',abo:null,trackingReminder:false,erlaubt:false};
let PUSH_REFRESH_IN_FLIGHT=null;
const messages=[];
const document={getElementById:()=>({classList:{contains:()=>true}})};
function apiReady(){return true;}
function renderSettings(){}
function showToast(message){messages.push(message);}
function deviceTimezone(){return 'Europe/Berlin';}
async function apiFetch(path){return path==='/v1/push/key'
  ? {ok:true,json:async()=>({available:true,publicKey:'key'})}
  : {ok:false,status:500,json:async()=>({ok:false})};}
(async()=>{await initPush();await initPush();process.stdout.write(JSON.stringify({active:!!PUSH.abo,messages}));})();
""",
        )
        self.assertFalse(result["active"])
        self.assertEqual(len(result["messages"]), 2)

    def test_push_disable_does_not_claim_success_when_browser_or_server_fails(self):
        result = self.run_js(
            ("togglePush",),
            """
let removed=false,serverCalls=0,PUSH_ACTION_IN_FLIGHT=false,PUSH_REFRESH_IN_FLIGHT=null;
const subscription={endpoint:'https://push.example.test/id',unsubscribe:async()=>removed};
const navigator={serviceWorker:{ready:Promise.resolve({pushManager:{getSubscription:async()=>subscription}})}};
const PUSH={available:true,abo:subscription};
const messages=[];
function showToast(message){messages.push(message);}
function renderSettings(){}
async function apiFetch(){serverCalls++;return {ok:false,status:500};}
(async()=>{
 await togglePush();
 const browserFailure={active:!!PUSH.abo,serverCalls,message:messages.at(-1)};
 removed=true;
 await togglePush();
 const serverFailure={active:!!PUSH.abo,serverCalls,message:messages.at(-1)};
 process.stdout.write(JSON.stringify({browserFailure,serverFailure}));
})();
""",
        )
        self.assertTrue(result["browserFailure"]["active"])
        self.assertEqual(result["browserFailure"]["serverCalls"], 0)
        self.assertIn("Konnte nicht abbestellen", result["browserFailure"]["message"])
        self.assertFalse(result["serverFailure"]["active"])
        self.assertEqual(result["serverFailure"]["serverCalls"], 1)
        self.assertIn("Serverabmeldung nicht bestätigt", result["serverFailure"]["message"])

    def test_expired_delete_code_offers_resend_and_network_copy_is_localized(self):
        result = self.run_js(
            ("escapeAccountHtml", "renderAccountDeleteSheet", "accountDeleteMessage", "confirmAccountDelete"),
            """
const body={innerHTML:''},hint={textContent:'',classList:{add:()=>{}}};
let resendFocused=false;
const document={
 getElementById:id=>id==='deletebody'?body:id==='accountDeleteHint'?hint:
  id==='accountDeleteCode'?{value:'123456'}:id==='accountDeletePhrase'?{value:'LOESCHEN'}:null,
 querySelector:()=>({focus:()=>{resendFocused=true;}}),
};
const DATA={identity:{email:'<test>@example.test'}};
const ACCOUNT_DELETE_FLOW={codeSent:true,busy:false,canResend:false};
function apiReady(){return true;}
let fail='expired';
async function apiFetch(){if(fail==='expired')return {ok:false,json:async()=>({ok:false,error:'delete_code_expired'})};throw new TypeError('Failed to fetch');}
(async()=>{
 await confirmAccountDelete();
 const expired={message:hint.textContent,resend:body.innerHTML.includes('data-delete-code'),focused:resendFocused,escaped:body.innerHTML.includes('&lt;test&gt;')};
 fail='network';await confirmAccountDelete();
 const network=hint.textContent;
 apiReady=()=>false;await confirmAccountDelete();
 process.stdout.write(JSON.stringify({expired,network,offline:hint.textContent}));
})();
""",
        )
        self.assertTrue(result["expired"]["resend"])
        self.assertTrue(result["expired"]["focused"])
        self.assertTrue(result["expired"]["escaped"])
        self.assertIn("abgelaufen", result["expired"]["message"])
        self.assertIn("Verbindung", result["network"])
        self.assertNotIn("Failed to fetch", result["network"])
        self.assertIn("Verbindung", result["offline"])

    def test_keyboard_dialog_and_mobile_wrap_have_explicit_support(self):
        source = self.source
        self.assertIn('id="setsheet" role="dialog" aria-modal="true"', source)
        self.assertIn('id="deletesheet" role="dialog" aria-modal="true"', source)
        self.assertIn('id="psheet" role="dialog" aria-modal="true"', source)
        self.assertIn('document.addEventListener("keydown",event=>{', source)
        self.assertIn('el.toggleAttribute("inert",!!dialog)', source)
        self.assertIn('data-close-settings', source)
        self.assertIn('overflow-wrap:anywhere;text-align:right', source)
        self.assertIn('id="accountDeleteHint" role="alert"', source)

    def test_closing_settings_restores_focus_when_original_opener_is_not_focusable(self):
        result = self.run_js(
            ("closeSheet",),
            """
let focused='';
const avatar={focus:()=>{focused='avatar';}};
const SETTINGS_RETURN_PLACEHOLDER={isConnected:true,tabIndex:-1,closest:()=>null,focus:()=>{focused='unfocusable';}};
let SETTINGS_RETURN_FOCUS=SETTINGS_RETURN_PLACEHOLDER;
const document={body:{classList:{remove:()=>{}}},getElementById:()=>avatar};
const sheetBg={classList:{remove:()=>{}}};
const sheet={classList:{remove:()=>{}},style:{removeProperty:()=>{}}};
function activeSettingsDialog(){return {};}
function closeAllSheetsSoft(){}
function liftTabbar(){}
function dismissKeyboard(){}
function setQuickError(){}
function roveSyncSheetClose(){return false;}
function requestAnimationFrame(callback){callback();}
closeSheet();
process.stdout.write(JSON.stringify({focused}));
""",
        )
        self.assertEqual(result["focused"], "avatar")


if __name__ == "__main__":
    unittest.main()
