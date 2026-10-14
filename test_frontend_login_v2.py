import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent

# Execute the shipped functions and delegated handlers with a small DOM double.
# Actual browser layout and native form submission are checked separately.
HARNESS = r'''
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(process.argv[1],'utf8');
const elements=new Map();
let dynamic=[];
class Element {
  constructor(tag='div',attrs={}) {
    this.tagName=tag;this.attrs=attrs;this.dataset={};this.listeners={};
    this.hidden='hidden' in attrs;this.disabled=false;this.value='';this.readOnly=false;
    this.type=attrs.type||(tag==='button'?'submit':'text');this.id=attrs.id||'';
    this.textContent='';this.className=attrs.class||'';
    const change=(name,enabled)=>{const names=new Set(this.className.split(' ').filter(Boolean));enabled?names.add(name):names.delete(name);this.className=[...names].join(' ');};
    this.classList={add:(...names)=>names.forEach(name=>change(name,true)),remove:(...names)=>names.forEach(name=>change(name,false)),
      toggle:(name,enabled)=>change(name,enabled??!this.classList.contains(name)),contains:name=>this.className.split(' ').includes(name)};
    for(const [key,value] of Object.entries(attrs))if(key.startsWith('data-'))this.dataset[key.slice(5).replace(/-([a-z])/g,(_,c)=>c.toUpperCase())]=value;
  }
  set innerHTML(html) {
    if(this.id!=='obBody')return;
    for(const element of dynamic)if(element.id)elements.delete(element.id);
    dynamic=[];
    for(const match of html.matchAll(/<(input|button|form|fieldset|div|section|h1|label|p)\b([^>]*)>/g)){
      const attrs={};
      for(const attr of match[2].matchAll(/([\w-]+)(?:="([^"]*)")?/g))attrs[attr[1]]=attr[2]??'';
      const element=new Element(match[1],attrs);
      element.textContent=html.slice(match.index+match[0].length).split('<',1)[0];
      dynamic.push(element);if(element.id)elements.set(element.id,element);
    }
  }
  setAttribute(name,value){this.attrs[name]=value;if(name==='hidden')this.hidden=true;}
  removeAttribute(name){delete this.attrs[name];if(name==='hidden')this.hidden=false;}
  getAttribute(name){return this.attrs[name]??null;}
  addEventListener(type,handler){(this.listeners[type]??=[]).push(handler);}
  focus(){this.focused=true;}
  checkValidity(){return this.type!=='email'||/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(this.value);}
  closest(selector){return matches(this,selector)?this:null;}
}
function matches(element,selector){
  selector=selector.replace(/^#onboard\s+/,'').trim();
  if(selector==='.auth-form fieldset')return element.tagName==='fieldset';
  if(selector==='.auth-form')return element.tagName==='form';
  if(selector.startsWith('#'))return element.id===selector.slice(1);
  const attr=selector.match(/^\[([\w-]+)(?:="([^"]+)")?\]$/);
  return !!attr&&attr[1] in element.attrs&&(attr[2]===undefined||element.attrs[attr[1]]===attr[2]);
}
for(const id of ['app','onboard','obBody','obFoot','obDots','obBack','obSkip','obLive'])elements.set(id,new Element('div',{id}));
const document={body:new Element('body'),getElementById:id=>elements.get(id)||null,
  querySelectorAll:selector=>[...elements.values(),...dynamic.filter(e=>!e.id)].filter(e=>selector.split(',').some(s=>matches(e,s))),
  querySelector:selector=>document.querySelectorAll(selector)[0]||null};
const calls=[];const routes=new Map();const pinScreens=[];
const response=(status,data)=>({status,ok:status>=200&&status<300,json:async()=>data});
const ctx=vm.createContext({document,Set,TypeError,Date,Promise,AbortController,setTimeout,clearTimeout,
  STARTUP_AUTH_TIMEOUT_MS:8000,PAIR_API_BASE_URL:'https://getrove.de/app-api',AUTH_FORM_STATE:'login',
  AUTH_ACTIONS_IN_FLIGHT:new Set(),AUTH_BOOTSTRAP_IN_FLIGHT:false,EMAIL_AUTH_PENDING:{email:'',mode:'login'},
  APP_MODE:'bridge',ROVE_API:null,BRIDGE_LIVE_STATE_READY:false,API_SESSION_FAILURE_HANDLED:false,
  PIN_SCREEN_ACTIVE:false,PIN_SESSION_STATE:'unknown',PIN_LAST_LOCAL_ACTIVITY:0,PIN_LAST_SERVER_TOUCH:0,
  SERVER_ONBOARDING_REQUIRED:false,OB:{step:0},buzz:()=>{},obRender:()=>{},notePinActivity:()=>{},
  loadBridgeState:async()=>{calls.push({path:'STATE'});elements.get('app').hidden=false;return true;},
  showPinScreen:mode=>{pinScreens.push(mode);elements.get('onboard').hidden=false;elements.get('app').hidden=true;},
  fetch:async(url,options)=>{
    const path=new URL(url).pathname.replace('/app-api','');calls.push({path,options});
    assert.equal(options.credentials,'include');assert.ok(options.signal);
    if(routes.has(path))return routes.get(path)(options);
    if(path==='/v1/auth/pin/status')return response(200,{ok:true,pin_status:'unlocked'});
    throw new Error('Unexpected synthetic endpoint: '+path);
  }
});
for(const name of ['setAuthSurface','setAuthBusy','beginAuthAction','endAuthAction','setAuthMessage','authRequestError','authRequest','authErrorMessage',
  'prepareAuthForm','authBrand','showAppConnect','showNewAccountRegistration','showPasswordSetup','showPasswordReset','showEmailCodeLogin','showAuthRecovery',
  'requestNewAccountCode','requestEmailLoginCode','passwordLogin','completePasswordSetup','requestPasswordReset','confirmPasswordReset','verifyEmailLoginCode',
  'fetchPinStatus','bootstrapAuthenticatedApp','continueWithSession','resumePinAfterBackground','showServerOnboarding']){
  const match=source.match(new RegExp('(?:async )?function '+name+'\\([^\\n]*\\)\\{[\\s\\S]*?\\n\\}'));
  assert.ok(match,name);vm.runInContext(match[0],ctx);
}
for(const [id,type] of [['onboard','click'],['onboard','submit'],['obBack','click']]){
  const start=source.indexOf(`document.getElementById("${id}").addEventListener("${type}"`);
  assert.ok(start>=0);const end=source.indexOf('\n});',start)+5;
  vm.runInContext(source.slice(start,end),ctx);
}
const get=id=>document.getElementById(id);
const button=action=>document.querySelector(`[data-auth="${action}"]`);
const message=()=>get('authHint')||get('registerHint');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function fillLogin(){get('loginEmail').value='synthetic@example.test';get('loginPassword').value='Synthetic-password';}
async function click(element){
  if(element.disabled)return;
  for(const handler of get('onboard').listeners.click)handler({target:element});
  await tick();
}
async function submit(){
  const form=document.querySelector('.auth-form');
  for(const handler of get('onboard').listeners.submit)handler({target:form,preventDefault:()=>{}});
  await tick();
}
async function back(){for(const handler of get('obBack').listeners.click)handler();await tick();}
ctx.showAppConnect();
'''


class LoginV2InteractionTests(unittest.TestCase):
    def run_flow(self, assertions):
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node.js is required for login interaction tests")
        script = HARNESS + "\n(async()=>{\n" + assertions + "\n})().catch(error=>{console.error(error);process.exitCode=1;});"
        result = subprocess.run(
            [node, "-e", script, str(ROOT / "frontend/index.html")],
            cwd=ROOT, text=True, capture_output=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_initial_login_has_no_error_or_code_request(self):
        self.run_flow("""
assert.equal(message().textContent,'');assert.equal(get('authTitle').textContent,'Willkommen zurück');
assert.equal(button('send-code'),null);assert.equal(calls.length,0);
assert.equal(get('loginEmail').attrs.autocomplete,'username');
assert.equal(message().attrs['aria-live'],'polite');
""")

    def test_password_visibility_is_reversible(self):
        self.run_flow("""
const toggle=button('toggle-password');await click(toggle);
assert.equal(get('loginPassword').type,'text');assert.equal(toggle.getAttribute('aria-pressed'),'true');
await click(toggle);assert.equal(get('loginPassword').type,'password');
""")

    def test_form_submission_uses_password_login_and_session_before_state(self):
        self.run_flow("""
fillLogin();routes.set('/v1/auth/password/login',()=>response(200,{ok:true}));
await submit();assert.deepEqual(calls.map(c=>c.path),['/v1/auth/password/login','/v1/auth/pin/status','STATE']);
assert.equal(get('onboard').hidden,true);assert.equal(get('app').hidden,false);
""")

    def test_false_credentials_preserve_inputs_and_have_neutral_wait_hint(self):
        self.run_flow("""
fillLogin();routes.set('/v1/auth/password/login',()=>response(401,{ok:false,error:'invalid_credentials'}));
await submit();assert.match(message().textContent,/Prüfe E-Mail-Adresse und Passwort/);
assert.match(message().textContent,/mehreren Fehlversuchen/);assert.equal(get('loginEmail').value,'synthetic@example.test');
assert.equal(button('password-login').disabled,false);
""")

    def test_invalid_email_is_rejected_before_request(self):
        self.run_flow("""
fillLogin();get('loginEmail').value='invalid-email';await submit();
assert.equal(calls.length,0);assert.match(message().textContent,/gültige E-Mail/);
""")

    def test_post_login_session_failure_preserves_inputs_and_explains(self):
        for status in (401,503):
            with self.subTest(status=status):
                self.run_flow(f"""
fillLogin();routes.set('/v1/auth/password/login',()=>response(200,{{ok:true}}));
routes.set('/v1/auth/pin/status',()=>response({status},{{ok:false}}));await submit();
assert.equal(get('loginEmail').value,'synthetic@example.test');assert.equal(get('loginPassword').value,'Synthetic-password');
assert.equal(get('onboard').hidden,false);assert.equal(get('app').hidden,true);
assert.match(message().textContent,/Anmeldung konnte noch nicht bestätigt/);
""")

    def test_pin_setup_and_onboarding_are_preserved(self):
        self.run_flow("""
fillLogin();routes.set('/v1/auth/password/login',()=>response(200,{ok:true}));
routes.set('/v1/auth/pin/status',()=>response(200,{ok:true,pin_status:'setup_required'}));await submit();
assert.deepEqual(pinScreens,['setup']);assert.equal(calls.filter(c=>c.path==='STATE').length,0);
routes.set('/v1/auth/pin/status',()=>response(200,{ok:true,pin_status:'unlocked',onboarding_required:true}));
assert.equal(await ctx.bootstrapAuthenticatedApp(),'onboarding');
""")

    def test_background_expiry_or_server_error_always_reveals_login(self):
        for status in (401,503):
            with self.subTest(status=status):
                self.run_flow(f"""
get('obBody').innerHTML='<div>PIN</div>';get('onboard').hidden=true;get('app').hidden=false;
ctx.AUTH_FORM_STATE='password-setup';ctx.PIN_SESSION_STATE='unlocked';
routes.set('/v1/auth/pin/status',()=>response({status},{{ok:false}}));await ctx.resumePinAfterBackground();
assert.equal(get('onboard').hidden,false);assert.equal(get('app').hidden,true);
assert.ok(message().textContent.length>0);assert.equal(ctx.PIN_SESSION_STATE,'unknown');
""")

    def test_background_success_loads_state_after_pin(self):
        self.run_flow("""
ctx.PIN_SESSION_STATE='unlocked';await ctx.resumePinAfterBackground();
assert.deepEqual(calls.map(c=>c.path),['/v1/auth/pin/status','STATE']);assert.equal(get('onboard').hidden,true);
""")

    def test_state_failure_never_reveals_finances_or_erases_login(self):
        self.run_flow("""
fillLogin();ctx.loadBridgeState=async()=>false;routes.set('/v1/auth/password/login',()=>response(200,{ok:true}));
await submit();assert.equal(get('app').hidden,true);assert.equal(get('onboard').hidden,false);
assert.equal(get('loginPassword').value,'Synthetic-password');assert.ok(message().textContent);
""")

    def test_timeout_unlocks_form_and_explains(self):
        self.run_flow("""
fillLogin();ctx.STARTUP_AUTH_TIMEOUT_MS=5;
routes.set('/v1/auth/password/login',options=>new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject(new DOMException('synthetic','AbortError')))));
await ctx.passwordLogin();assert.match(message().textContent,/zu lange/);assert.equal(button('password-login').disabled,false);
""")

    def test_response_body_is_also_bounded_by_timeout(self):
        self.run_flow("""
fillLogin();ctx.STARTUP_AUTH_TIMEOUT_MS=5;
routes.set('/v1/auth/password/login',options=>({status:200,ok:true,json:()=>new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject(new DOMException('synthetic','AbortError'))))}));
await ctx.passwordLogin();assert.match(message().textContent,/zu lange/);assert.equal(button('password-login').disabled,false);
""")

    def test_network_and_server_failures_are_actionable(self):
        self.run_flow("""
fillLogin();routes.set('/v1/auth/password/login',()=>{throw new TypeError('synthetic network failure');});
await submit();assert.match(message().textContent,/Verbindung/);
routes.set('/v1/auth/password/login',()=>response(503,{ok:false,error:'internal_value_must_not_leak'}));
await submit();assert.match(message().textContent,/später/);assert.ok(!message().textContent.includes('internal_value'));
""")

    def test_double_submission_is_blocked_until_session_check_finishes(self):
        self.run_flow("""
fillLogin();let finish;routes.set('/v1/auth/password/login',()=>response(200,{ok:true}));
routes.set('/v1/auth/pin/status',()=>new Promise(resolve=>{finish=resolve;}));
const first=ctx.passwordLogin();await tick();await ctx.passwordLogin();
assert.equal(button('password-login').disabled,true);assert.equal(calls.filter(c=>c.path==='/v1/auth/password/login').length,1);
finish(response(200,{ok:true,pin_status:'unlocked'}));await first;
assert.equal(ctx.AUTH_ACTIONS_IN_FLIGHT.size,0);
""")

    def test_reset_back_returns_to_login(self):
        self.run_flow("""
await click(button('show-reset'));assert.ok(get('resetEmail'));await back();
assert.ok(get('loginPassword'));assert.equal(ctx.AUTH_FORM_STATE,'login');
""")

    def test_registration_uses_existing_contract_and_returns_to_login(self):
        self.run_flow("""
await click(dynamic.find(e=>e.dataset.pair==='new'));get('registerEmail').value='synthetic@example.test';
routes.set('/v1/auth/request-code',()=>response(200,{ok:true,sent:true}));await submit();
assert.equal(JSON.parse(calls[0].options.body).registration,true);assert.equal(get('registerCodeWrap').hidden,false);
assert.equal(button('register-send').type,'button');await back();assert.ok(get('loginPassword'));
""")

    def test_registration_continues_through_password_then_pin_without_financial_state(self):
        self.run_flow("""
ctx.showNewAccountRegistration();get('registerEmail').value='synthetic@example.test';
routes.set('/v1/auth/request-code',()=>response(200,{ok:true,sent:true}));await submit();
get('registerCode').value='123456';routes.set('/v1/auth/verify-code',()=>response(200,{ok:true}));
routes.set('/v1/auth/pin/status',()=>response(200,{ok:true,password_setup_required:true,pin_status:'setup_required',onboarding_required:true}));
await submit();assert.equal(ctx.AUTH_FORM_STATE,'password-setup');assert.ok(get('passwordSetup'));
get('passwordSetup').value='Synthetic-new-password';get('passwordSetupConfirm').value='Synthetic-new-password';
routes.set('/v1/auth/password/setup',()=>response(200,{ok:true}));
routes.set('/v1/auth/pin/status',()=>response(200,{ok:true,password_setup_required:false,pin_status:'setup_required',onboarding_required:true}));
await submit();assert.deepEqual(pinScreens,['setup']);assert.equal(calls.filter(c=>c.path==='STATE').length,0);
""")

    def test_saved_password_is_explained_when_following_session_check_fails(self):
        self.run_flow("""
ctx.showPasswordSetup(true);get('passwordSetup').value='Synthetic-new-password';get('passwordSetupConfirm').value='Synthetic-new-password';
routes.set('/v1/auth/password/setup',()=>response(200,{ok:true}));routes.set('/v1/auth/pin/status',()=>response(503,{ok:false}));
await submit();assert.ok(get('loginPassword'));assert.match(message().textContent,/Passwort wurde gespeichert/);
assert.match(message().textContent,/Anmeldung konnte noch nicht bestätigt/);
""")

    def test_reset_expiry_allows_resend_and_success_continues_session(self):
        self.run_flow("""
await click(button('show-reset'));get('resetEmail').value='synthetic@example.test';
routes.set('/v1/auth/password/reset/request',()=>response(200,{ok:true,sent:true}));await submit();
get('resetCode').value='123456';get('resetPassword').value='New-synthetic-password';get('resetConfirm').value='New-synthetic-password';
routes.set('/v1/auth/password/reset/confirm',()=>response(401,{ok:false,error:'invalid_or_expired_reset_code'}));
await submit();assert.match(message().textContent,/abgelaufen/);assert.equal(button('reset-request').disabled,false);
await click(button('reset-request'));assert.equal(calls.filter(c=>c.path==='/v1/auth/password/reset/request').length,2);
get('resetCode').value='654321';routes.set('/v1/auth/password/reset/confirm',()=>response(200,{ok:true}));
await submit();assert.equal(get('onboard').hidden,true);
""")

    def test_email_code_login_is_available_only_on_demand_and_can_resend(self):
        self.run_flow("""
await click(button('show-reset'));await click(button('show-code'));get('loginEmail').value='synthetic@example.test';
routes.set('/v1/auth/request-code',()=>response(200,{ok:true,sent:true}));await submit();
get('loginCode').value='123456';routes.set('/v1/auth/verify-code',()=>response(401,{ok:false,error:'code_expired'}));
await submit();assert.match(message().textContent,/abgelaufen/);await click(button('send-code'));
assert.equal(calls.filter(c=>c.path==='/v1/auth/request-code').length,2);
assert.equal(get('loginCode').value,'');
""")


if __name__ == "__main__":
    unittest.main()
