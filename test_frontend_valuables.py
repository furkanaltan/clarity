"""Execute the real valuables frontend logic in Node without network or a browser."""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
FRONTEND = ROOT / "frontend" / "index.html"


def section(source: str, start: str, end: str) -> str:
    offset = source.index(start)
    return source[offset : source.index(end, offset)]


def function(source: str, name: str) -> str:
    match = re.search(r"^(?:async )?function " + re.escape(name) + r"\(", source, re.M)
    if match is None:
        raise AssertionError(f"Frontend function not found: {name}")
    start = match.start()
    first_line = source[start : source.index("\n", start)]
    if first_line.rstrip().endswith("}"):
        return first_line + "\n"
    return source[start : source.index("\n}", start) + 2] + "\n"


# These adapters provide browser/transport boundaries, not alternative valuables logic.
HARNESS = r"""
const assert=require('node:assert/strict');
const crypto=require('node:crypto');
const elements=new Map(), requests=[], messages=[], navigation=[], storageWrites=[];
const documentHandlers={};
const scrolledValuables=[];
const timers=new Map();
let timerId=0, buzzes=0, persisted=0, closed=0, pinContinues=0;
let MULTI_CASH_ENABLED=true, accountAddOpened=0;
const openedAssetKinds=[];
const decode=value=>String(value).replace(/&quot;/g,'"').replace(/&#39;/g,"'")
  .replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&amp;/g,'&');
function element(id=''){
  let html='';
  return {id,value:'',dataset:{},textContent:'',hidden:false,disabled:false,style:{},handlers:{},
    classList:{add(){},remove(){},toggle(){},contains(){return false;}},
    setAttribute(name,value){this[name]=String(value);},removeAttribute(name){delete this[name];},
    focus(){this.focused=true;},append(){},
    addEventListener(type,handler){this.handlers[type]=handler;},
    get innerHTML(){return html;},
    set innerHTML(value){
      html=String(value);
      // Read form attributes from the actual rendered HTML, including owner and stable ID.
      for(const tag of html.matchAll(/<(?:input|button)\b[^>]*>/g)){
        const attrs=Object.fromEntries([...tag[0].matchAll(/([\w-]+)="([^"]*)"/g)]
          .map(([,key,value])=>[key,decode(value)]));
        if(!attrs.id)continue;
        const child=document.getElementById(attrs.id);
        child.dataset={}; child.disabled=false;
        for(const [key,value] of Object.entries(attrs)){
          if(key.startsWith('data-'))child.dataset[key.slice(5).replace(/-([a-z])/g,(_,c)=>c.toUpperCase())]=value;
        }
        if('value' in attrs)child.value=attrs.value;
      }
    }};
}
const document={
  addEventListener(type,handler){documentHandlers[type]=handler;},
  getElementById(id){if(!elements.has(id))elements.set(id,element(id));return elements.get(id);},
  querySelector(selector){
    return selector.startsWith('#dbody .valuable-row')
      ? {scrollIntoView:options=>scrolledValuables.push({selector,options})} : null;
  },
  querySelectorAll(selector){
    if(selector.includes('#svSend'))return mutationButtons();
    return [];
  },
  createElement:()=>element()
};
function mutationButtons(){return ['svSend','svDelete','svGroupDelete','importValuables'].map(id=>document.getElementById(id));}
function click(root,selector,target=null){
  const node=target||document.getElementById(selector.replace(/^#/,''));
  document.getElementById(root).handlers.click({target:{closest:query=>query===selector?node:null},
    preventDefault(){},stopImmediatePropagation(){}});
  return node;
}
function memoryStorage(){
  const values=new Map();
  return {get length(){return values.size;},key:index=>[...values.keys()][index]??null,
    getItem:key=>values.get(String(key))??null,
    setItem(key,value){values.set(String(key),String(value));storageWrites.push(String(key));},
    removeItem:key=>values.delete(String(key))};
}
const localStorage=memoryStorage(), sessionStorage=memoryStorage();
const window={}, navigator={};
const location={pathname:'/synthetic-app',replace:path=>navigation.push(path),reload:()=>navigation.push('reload')};
const setTimeout=(callback,ms)=>{const id=++timerId;timers.set(id,{callback,ms});return id;};
const clearTimeout=id=>timers.delete(id);
const requestAnimationFrame=callback=>callback();
function transport(path,options={}){
  return new Promise((resolve,reject)=>{
    requests.push({path,options,resolve,reject});
    options.signal?.addEventListener('abort',()=>reject(Object.assign(new Error('synthetic timeout'),{name:'AbortError'})),{once:true});
  });
}
const apiFetch=transport, fetch=transport;
// Even an accidental call outside the injected transport must never reach a live server.
globalThis.fetch=()=>{throw new Error('Real network access forbidden in frontend tests');};
function respond(index,data,ok=true){requests[index].resolve({ok,status:ok?200:503,json:async()=>data});}
function confirmed(index,positions,owner=BRIDGE_USER_ID){respond(index,{ok:true,user_id:owner,valuables:positions});}
const settle=()=>new Promise(resolve=>setImmediate(resolve));
const eur=value=>Number(value).toLocaleString('de-DE')+' EUR';
const eur2=value=>Number(value).toLocaleString('de-DE',{minimumFractionDigits:2,maximumFractionDigits:2})+' EUR';
let APP_MODE='bridge', BRIDGE_USER_ID=41, BRIDGE_LIVE_STATE_READY=true;
let DATA={assets:[{name:'Girokonto',value:12500}],valuables:[],vertraege:[],goals:[],budgets:[],
  tx:[],sts:{konto:12500},netWorth:12500,netWorthAvailable:true};
const PROFILE_META={netHistory:[],netHistorySchemaVersion:2};
const PAIR_API_BASE_URL='https://invalid.test';
let ROVE_API={}, ready=true;
let dIdx=0,svEditIndex=null,svArm=null,svFormOpen=false,investFormOpen=false,investFormEditIndex=null,investNewAssetType='stock';
let PRICES_STALE=false;
const STALE_LBL='synthetic', ASSET_NACHTRAG=new Set(['Krypto','ETF & Investments']);
const sheetBg=element(), ICONS={};
let SERVER_ONBOARDING_REQUIRED=true,SERVER_ONBOARDING_AWAITING_PIN=false;
let ONBOARDING_DRAFT_SAVE_ERROR='';
let ONBOARDING_DRAFT_LOAD_IN_FLIGHT=null,authSurface=false;
const ONBOARDING_DRAFT_TIMEOUT_MS=10000;
function apiReady(){return ready;}
function showToast(message){messages.push(message);}
function buzz(){buzzes++;}
function persistAppState(){persisted++;}
function closeSheet(){closed++;}
function logActivity(){}
function multiCashEnabled(){return MULTI_CASH_ENABLED;}
function openFinancialAccountAdd(){accountAddOpened++;}
function openAssetKind(kind){openedAssetKinds.push(kind);}
function ic(){return '<svg></svg>';}
function emptyCard({action,cta,sub}){return `<div data-empty="${action}"><p>${sub}</p>${cta}</div>`;}
function cashAccountMeta(){return null;}
function staleHint(){return '';}
function rovePushState(name){navigation.push(name);}
function liftTabbar(){}
function clearPaydayHistory(){}
function resetCancellationView(){}
function setAuthSurface(value){authSurface=value;}
async function continueAfterServerOnboarding(){pinContinues++;return true;}
let goalStateRevision=0,bufferStateRevision=0,appDataRefreshQueued=false,TXID=1000;
const CHART={range:'1W'};
for(const name of ['applyServerGoals','applyServerBuffer','reconcilePendingBookingFromCanonical',
  'renderGoals','renderTx','renderBudgets','renderVertraege','renderActivity','renderAnalysisIfOpen',
  'mentorLine','applyHomeEmpty','applyProfileIdentity','seedNetHistoryIfEmpty','syncNetHistory',
  'rebuildSeriesFromHistory','syncPropertyCoverageBoundaryFromServer','drawChart','updateChangeBadge',
  'maybeOpenMonthlyPlanOnMonthStart'])globalThis[name]=()=>{};
function legacySnapshot(userId=41){
  return {userId,assets:[
    {name:'Sachwerte',manual:true,value:375,positions:[{id:'legacy-id',n:'Uhr',v:125},{n:'Rad',v:250}]},
    {name:'ETF Depot',value:800,positions:[{n:'Not a valuable',v:800}]},
    {name:'Unknown preserved asset',value:50}
  ],vertraege:[],goals:[],budgets:[],netHistory:[],savedAt:7};
}
const scopedKey=owner=>`rove:${owner}:${BRIDGE_LOCAL_KEY}`;
function seedLegacy(snapshot=legacySnapshot(),scoped=false){
  const raw=JSON.stringify(snapshot);
  localStorage.setItem(scoped?scopedKey(snapshot.userId):BRIDGE_LOCAL_KEY,raw);
  return raw;
}
const first={id:'1'.repeat(32),n:'Uhr',v:125};
const second={id:'2'.repeat(32),n:'Rad',v:250};
function namedCashAccount(){
  return {name:'Sachwerte',assetKey:'cash-account:901',source:'financial-account',
    financialAccountId:901,accountType:'checking',value:750,sub:'Geldkonto'};
}
function openValuableForm(positions=[],editIndex=null){
  applyServerValuables(positions);
  if(!positions.length)DATA.assets.push({name:'Sachwerte',manual:true,positions:[],value:0,sub:''});
  dIdx=valuableAssetIndex();svEditIndex=editIndex;svFormOpen=true;
  openAssetDetail(dIdx);
}
function setInputs(name='Neue Uhr',value='1.234,56'){
  document.getElementById('svName').value=name;
  document.getElementById('svAmt').value=value;
}
"""


class FrontendValuablesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        source = FRONTEND.read_text(encoding="utf-8")
        cls.production = section(source, 'const BRIDGE_LOCAL_KEY =', 'let BRIDGE_USER_ID =')
        cls.production += section(source, 'const BRIDGE_BOT_ASSET_NAMES =', 'function classifyBridgeAsset(')
        cls.production += section(source, 'const ASSET_ORDER_KEY_BY_NAME=', 'function newValuableId(')
        cls.production += section(source, 'const ASSET_NACHTRAG_META =', 'function ensureAssetPlaceholders(')
        for name in (
            'bridgeStorageKey', 'classifyBridgeAsset', 'readBridgeLocal', 'saveBridgeLocal',
            'restoreBridgeLocal', 'serverContractId', 'normalizedContractId', 'validatedNetHistory',
            'clearRoveBrowserData', 'logoutCurrentUser', 'mergeServerAssets', 'applyAssetOrder',
            'newValuableId', 'isValuableAsset', 'valuableAssetIndex',
            'renderAssets', 'ensureAssetPlaceholders', 'missingAssetRow', 'legacyValuablePositions',
            'legacyValuablesHtml', 'applyServerValuables', 'requestValuables', 'importLegacyValuables',
            'escapeAccountHtml', 'moduleIcon', 'assetGlyph', 'assetIcon', 'homeAssetIcon',
            'assetIndexByName', 'parseGermanAmount', 'openAssetDetail',
            'addAssetPosition', 'setCanonicalNetWorth', 'recalcNetWorth', 'fetchCanonicalAppState',
            'finishOnboarding', 'showServerOnboarding',
        ):
            cls.production += function(source, name)
        cls.production += section(source, 'const OB = {', 'async function continueAfterServerOnboarding(')
        cls.production += section(
            source, 'document.getElementById("dbody").addEventListener("click",e=>{',
            'document.getElementById("dbody").addEventListener("input",e=>{',
        )
        cls.production += section(
            source, 'document.getElementById("onboard").addEventListener("click", e=>{',
            'document.getElementById("onboard").addEventListener("input", e=>{',
        )
        cls.production += section(
            source, 'document.getElementById("assets").addEventListener("click",e=>{',
            'let dIdx=null, svArm=null',
        )
        cls.production += section(
            source, 'document.addEventListener("click",async e=>{',
            '\n});\n// "Erstell dir selbst ein Budget"',
        ) + '\n});\n'

    def run_js(self, body: str) -> None:
        script = HARNESS + self.production + '\n(async()=>{\n' + body + r"""
})().then(()=>{assert.equal(timers.size,0,'Request timeout was not cleaned up');
  console.log('VALUABLES_OK');}).catch(error=>{console.error(error);process.exitCode=1;});
"""
        result = subprocess.run(
            ['node', '--unhandled-rejections=strict', '--input-type=commonjs'],
            input=script, cwd=ROOT, text=True, capture_output=True, timeout=15, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('VALUABLES_OK', result.stdout, 'Async scenario did not reach its assertions')

    def test_ids_are_unique_hex_and_reset_starts_a_new_completion(self):
        self.run_js(r"""
const ids=Array.from({length:100},()=>newValuableId());
for(const id of ids)assert.match(id,/^[0-9a-f]{32}$/);
assert.equal(new Set(ids).size,100);
const previous=OB.completionId;
OB.valuables=[{...first}];OB.name='Synthetic';
resetOnboardingDraft();
assert.deepEqual(OB.valuables,[]);assert.equal(OB.name,'');
assert.match(OB.completionId,/^[0-9a-f]{32}$/);assert.notEqual(OB.completionId,previous);
""")

    def test_old_drafts_default_to_empty_optional_valuables(self):
        self.run_js(r"""
for(const draft of [null,[],{}, {name:'Old draft',wealth:{giro:100}},
  {valuables:null},{valuables:{}},{valuables:'invalid'}]){
  OB.valuables=[{...first}];applyServerOnboardingDraft(draft);
  assert.deepEqual(OB.valuables,[]);assert.match(OB.completionId,/^[0-9a-f]{32}$/);
  assert.deepEqual(serializeOnboardingDraft().valuables,[]);
}
applyServerOnboardingDraft({name:'Old draft',income:2500,wealth:{giro:100}});
assert.equal(OB.name,'Old draft');assert.equal(OB.income,2500);assert.deepEqual(OB.wealth,{giro:100});
assert.equal(requests.length,0);
""")

    def test_new_draft_ids_and_values_round_trip_without_aliases(self):
        self.run_js(r"""
OB.valuables=[{...first},{...second}];OB.wealth={giro:100};
const saved=JSON.parse(JSON.stringify(serializeOnboardingDraft()));
const completion=OB.completionId;
resetOnboardingDraft();applyServerOnboardingDraft(saved);
assert.deepEqual(OB.valuables,[first,second]);assert.equal(OB.completionId,completion);
assert.deepEqual(serializeOnboardingDraft(),saved);
saved.valuables[0].n='Changed externally';saved.wealth.giro=999;
assert.equal(OB.valuables[0].n,'Uhr');assert.equal(OB.wealth.giro,100);
const exported=serializeOnboardingDraft();exported.valuables[0].v=999;
assert.equal(OB.valuables[0].v,125);
for(const id of ['',null,'bad','A'.repeat(32)]){
  applyServerOnboardingDraft({completionId:id});assert.match(OB.completionId,/^[0-9a-f]{32}$/);
}
""")

    def test_draft_get_bootstraps_initial_identity_and_posts_owner_bound_round_trip(self):
        self.run_js(r"""
BRIDGE_USER_ID=null;
const saved={...serializeOnboardingDraft(),name:'Fresh registration',wealth:{giro:1000},
  valuables:[first],completionId:'a'.repeat(32)};
const task=showServerOnboarding(), duplicate=showServerOnboarding();
assert.equal(requests.length,1);assert.equal(BRIDGE_USER_ID,null);
assert.equal(requests[0].path,'/v1/onboarding/draft');assert.equal(requests[0].options.method,'GET');
respond(0,{ok:true,user_id:41,step:5,draft:saved});
assert.equal(await task,true);assert.equal(await duplicate,true);
assert.equal(BRIDGE_USER_ID,41);assert.equal(OB.step,5);assert.equal(OB.name,saved.name);
assert.deepEqual(OB.valuables,[first]);assert.equal(OB.completionId,saved.completionId);
assert.equal(authSurface,true);assert.equal(document.getElementById('onboard').hidden,false);
assert.equal(ONBOARDING_DRAFT_LOAD_IN_FLIGHT,null);
const post=persistServerOnboardingDraft(6);
assert.equal(requests[1].path,'/v1/onboarding/draft');assert.equal(requests[1].options.method,'POST');
assert.equal(requests[1].options.headers['X-Rove-User-ID'],'41');
assert.deepEqual(JSON.parse(requests[1].options.body),{step:6,draft:saved});
respond(1,{ok:true,user_id:41});assert.equal(await post,true);
""")

    def test_foreign_draft_get_leaves_existing_owner_and_draft_unchanged(self):
        self.run_js(r"""
OB.name='Current owner';OB.valuables=[{...first}];
const before=JSON.stringify(OB), stateBefore=JSON.stringify(DATA);
const task=showServerOnboarding();
respond(0,{ok:true,user_id:42,step:5,draft:{name:'Foreign owner',valuables:[second]}});
assert.equal(await task,false);assert.equal(BRIDGE_USER_ID,41);
assert.equal(JSON.stringify(OB),before);assert.equal(JSON.stringify(DATA),stateBefore);
assert.equal(ONBOARDING_DRAFT_LOAD_IN_FLIGHT,null);assert.equal(storageWrites.length,0);
""")

    def test_initial_draft_identity_must_be_a_positive_safe_integer(self):
        for identity in ('0', '-1', '1.5', "'41'", 'null', 'undefined', 'Number.MAX_SAFE_INTEGER+1'):
            with self.subTest(identity=identity):
                self.run_js('const responseOwner=' + identity + ';\n' + r"""
BRIDGE_USER_ID=null;OB.valuables=[{...first}];const before=JSON.stringify(OB);
const task=showServerOnboarding();
respond(0,{ok:true,user_id:responseOwner,step:5,draft:{name:'Must not apply',valuables:[second]}});
assert.equal(await task,false);assert.equal(BRIDGE_USER_ID,null);
assert.equal(JSON.stringify(OB),before);assert.equal(ONBOARDING_DRAFT_LOAD_IN_FLIGHT,null);
""")

    def test_draft_get_cannot_rebind_identity_changed_while_loading(self):
        self.run_js(r"""
for(const initialOwner of [null,41]){
  BRIDGE_USER_ID=initialOwner;const index=requests.length, before=JSON.stringify(OB);
  const task=showServerOnboarding();BRIDGE_USER_ID=42;
  respond(index,{ok:true,user_id:42,step:5,draft:{name:'New cookie owner',valuables:[second]}});
  assert.equal(await task,false);assert.equal(BRIDGE_USER_ID,42);
  assert.equal(JSON.stringify(OB),before);assert.equal(ONBOARDING_DRAFT_LOAD_IN_FLIGHT,null);
}
""")

    def test_draft_post_rejects_foreign_or_late_acknowledgment(self):
        for change_owner, response_owner in ((False, '42'), (False, 'undefined'), (True, '41'), (True, '42')):
            with self.subTest(change_owner=change_owner, response_owner=response_owner):
                prefix = f"const changeOwner={str(change_owner).lower()}, responseOwner={response_owner};\n"
                self.run_js(prefix + r"""
OB.valuables=[{...first}];const before=JSON.stringify(OB);
const task=persistServerOnboardingDraft(5);
assert.equal(requests[0].options.headers['X-Rove-User-ID'],'41');
if(changeOwner)BRIDGE_USER_ID=42;
respond(0,{ok:true,user_id:responseOwner});
await assert.rejects(task,error=>error.httpStatus===401);
assert.equal(BRIDGE_USER_ID,changeOwner?42:41);assert.equal(JSON.stringify(OB),before);
assert.equal(SERVER_ONBOARDING_REQUIRED,true);assert.equal(SERVER_ONBOARDING_AWAITING_PIN,false);
assert.equal(storageWrites.length,0);
""")

    def test_onboarding_add_remove_handlers_use_real_validation_and_ids(self):
        self.run_js(r"""
OB.step=OB_STEPS.indexOf('wealth');
document.getElementById('obValuableName').value='  Auto  ';
document.getElementById('obValuableValue').value='12.345,67';
click('onboard','#obValuableAdd');
assert.equal(OB.valuables.length,1);assert.match(OB.valuables[0].id,/^[0-9a-f]{32}$/);
assert.equal(OB.valuables[0].n,'Auto');assert.equal(OB.valuables[0].v,12345.67);
const id=OB.valuables[0].id;
const saved=serializeOnboardingDraft();resetOnboardingDraft();applyServerOnboardingDraft(saved);
assert.equal(OB.valuables[0].id,id);
click('onboard','[data-ob-valuable-remove]',{dataset:{obValuableRemove:id}});
assert.deepEqual(OB.valuables,[]);assert.equal(requests.length,0);
""")

    def test_onboarding_compact_section_keeps_existing_resume_and_inputs(self):
        self.run_js(r"""
OB.step=OB_STEPS.indexOf('wealth');OB.valuables=[];obRender();
let html=document.getElementById('obBody').innerHTML;
assert.ok(html.includes('<details class="ob-field ob-valuables">'));
assert.ok(html.includes('Sachwerte (optional)'));
assert.ok(html.includes('href="#rove-module-asset-diamond"'));
assert.ok(html.includes('Zum Beispiel Auto, Schmuck oder Wertgegenstände. Nur geschätzte Werte eintragen.'));
assert.ok(!html.includes('Sachwerte werden separat geführt'));
OB.valuables=[first];obRender();html=document.getElementById('obBody').innerHTML;
assert.ok(html.includes('<details class="ob-field ob-valuables" open>'));
for(const id of ['obValuableName','obValuableValue','obValuableAdd','obValuableError']){
  assert.ok(html.includes('id="'+id+'"'));
}
assert.deepEqual(OB.valuables,[first]);assert.equal(requests.length,0);
""")

    def test_server_valuable_subline_is_only_the_real_position_count(self):
        self.run_js(r"""
for(const count of [1,4]){
  const positions=Array.from({length:count},(_,i)=>({id:String(i+1).repeat(32),n:'Wert '+i,v:100}));
  applyServerValuables(positions);renderAssets();
  const asset=DATA.assets.find(isValuableAsset);
  assert.equal(asset.sub,count===1?'1 Wert':'4 Werte');
  openAssetDetail(valuableAssetIndex());
  const html=document.getElementById('dbody').innerHTML;
  assert.ok(html.includes('<div class="dt">Sachwerte</div>'));
  assert.ok(html.includes('<div class="dv">'+asset.sub+'</div>'));
  assert.ok(html.includes('Sachwerte werden nicht ins Gesamtvermögen eingerechnet.'));
  assert.ok(!html.includes('im Konto gespeichert'));
}
delete DATA.valuables;DATA.assets=legacySnapshot().assets;renderAssets();
assert.equal(DATA.assets.find(isValuableAsset).sub,'2 Werte · auf diesem Gerät');
assert.equal(requests.length,0);
""")

    def test_server_valuables_render_after_load_and_reload_without_multi_cash(self):
        self.run_js(r"""
MULTI_CASH_ENABLED=false;
const serverState={ok:true,user_id:41,features:{multi_cash_accounts_v1:false},
  assets:[{name:'Girokonto',value:12500}],valuables:[first,second],netWorth:12500};
const firstLoad=fetchCanonicalAppState();respond(0,serverState);assert.equal(await firstLoad,true);
let html=document.getElementById('assets').innerHTML;
assert.equal(DATA.assets.filter(isValuableAsset).length,1);
assert.ok(html.includes('Sachwerte'));assert.ok(html.includes('375 EUR'));
assert.ok(html.includes('data-add-valuables'));assert.ok(!html.includes('data-add-financial-account'));
assert.equal(DATA.netWorth,12500);
openAssetDetail(valuableAssetIndex());
assert.ok(document.getElementById('dbody').innerHTML.includes('Sachwerte werden nicht ins Gesamtvermögen eingerechnet.'));

DATA.assets=[];DATA.valuables=[];
const reload=fetchCanonicalAppState();respond(1,serverState);assert.equal(await reload,true);
html=document.getElementById('assets').innerHTML;
assert.equal(DATA.assets.filter(isValuableAsset).length,1);
assert.ok(html.includes('Sachwerte'));assert.ok(html.includes('375 EUR'));
assert.equal(DATA.netWorth,12500);
assert.equal(requests.length,2);
""")

    def test_valuables_add_action_is_available_without_enabling_account_pilot(self):
        self.run_js(r"""
MULTI_CASH_ENABLED=false;
DATA.assets=[{name:'Girokonto',value:12500}];DATA.valuables=[];renderAssets();
let html=document.getElementById('assets').innerHTML;
assert.ok(html.includes('data-add-valuables'));assert.ok(html.includes('Sachwert hinzufügen'));
assert.ok(!html.includes('Konto oder Wert hinzufügen'));
assert.ok(!html.includes('data-add-financial-account'));
click('assets','[data-add-valuables]');
assert.deepEqual(openedAssetKinds,['valuables']);assert.equal(accountAddOpened,0);

MULTI_CASH_ENABLED=true;renderAssets();html=document.getElementById('assets').innerHTML;
assert.ok(html.includes('data-add-financial-account'));assert.ok(!html.includes('data-add-valuables'));
assert.ok(html.includes('Konto oder Wert hinzufügen'));
click('assets','[data-add-financial-account]');
assert.equal(accountAddOpened,1);assert.deepEqual(openedAssetKinds,['valuables']);
assert.equal(requests.length,0);
""")

    def test_valuable_form_reuses_investment_plus_pencil_and_save_controls(self):
        self.run_js(r"""
openValuableForm([first]);
let html=document.getElementById('dbody').innerHTML;
assert.equal([...html.matchAll(/<input\b/g)].length,2);
assert.match(html,/<label[^>]*for="svName">Bezeichnung<\/label>/);
assert.match(html,/<label[^>]*for="svAmt">Geschätzter Wert<\/label>/);
assert.match(html,/<button type="button" class="position-add" id="svFormToggle"[^>]*aria-expanded="true"/);
assert.match(html,/<button type="button" class="crypto-position-edit" data-svedit="0"[^>]*aria-label="Uhr bearbeiten"><svg[^>]*><path d="m4.5 19.5/);
assert.match(html,/<input id="svAmt"[^>]*>\s*<button type="button" class="send" id="svSend"[^>]*aria-label="Sachwert speichern"/);
assert.ok(html.includes('<path d="M5 13l4 4L19 6"/>'));
assert.ok(!html.includes('class="asset-position-edit"'));
assert.ok(!html.includes('class="vd-btn"'));
assert.ok(html.indexOf('class="detail-rows"')<html.indexOf('id="svForm"'));
assert.ok(!html.includes('id="svDelete"'));assert.ok(!html.includes('id="svGroupDelete"'));
openValuableForm([first],0);html=document.getElementById('dbody').innerHTML;
assert.equal([...html.matchAll(/<input\b/g)].length,2);
assert.match(html,/<button type="button" class="send" id="svSend"[^>]*aria-label="Sachwert speichern"/);
assert.equal(document.getElementById('svSend').dataset.valuableId,first.id);
assert.ok(html.includes('id="svDelete"'));
assert.ok(html.includes('id="svGroupDelete"'));
""")

    def test_plus_and_pencil_open_and_close_the_existing_form_without_mutations(self):
        self.run_js(r"""
openValuableForm([first,second]);svFormOpen=false;openAssetDetail(dIdx);
let html=document.getElementById('dbody').innerHTML;
assert.match(html,/id="svFormToggle"[^>]*aria-expanded="false"/);
assert.match(html,/id="svForm"[^>]* inert/);
const before=JSON.stringify(DATA);
click('dbody','#svFormToggle');
assert.equal(svFormOpen,true);assert.equal(document.getElementById('svName').focused,true);
assert.match(document.getElementById('dbody').innerHTML,/id="svFormToggle"[^>]*aria-expanded="true"/);
click('dbody','#svFormToggle');assert.equal(svFormOpen,false);
const pencil={dataset:{svedit:'0',valuableOwner:'41',valuableId:first.id}};
applyServerValuables([second,first]);
click('dbody','[data-svedit]',pencil);
assert.equal(svEditIndex,1);assert.equal(svFormOpen,true);
assert.equal(document.getElementById('svName').value,'Uhr');
assert.equal(document.getElementById('svSend').dataset.valuableId,first.id);
assert.equal(document.getElementById('svAmt').focused,true);
click('dbody','#svFormToggle');
assert.equal(svEditIndex,null);assert.equal(svFormOpen,false);
assert.equal(document.getElementById('svName').value,'');
assert.equal(requests.length,0);assert.equal(DATA.netWorth,JSON.parse(before).netWorth);
dIdx=assetIndexByName('Girokonto');openAssetDetail(valuableAssetIndex(),true);
assert.equal(svFormOpen,true);assert.equal(svEditIndex,null);
assert.match(document.getElementById('dbody').innerHTML,/id="svFormToggle"[^>]*aria-expanded="true"/);
""")

    def test_form_navigation_cannot_interrupt_a_pending_save_or_switch_owner(self):
        self.run_js(r"""
openValuableForm([first]);setInputs();
const toggle=document.getElementById('svFormToggle');
const pencil={dataset:{svedit:'0',valuableOwner:'41',valuableId:first.id}};
const task=addAssetPosition();
click('dbody','#svFormToggle',toggle);click('dbody','[data-svedit]',pencil);
assert.equal(svFormOpen,true);assert.equal(svEditIndex,null);
assert.equal(document.getElementById('svName').value,'Neue Uhr');
requests[0].reject(new Error('synthetic offline'));await task;
assert.equal(document.getElementById('svName').value,'Neue Uhr');
BRIDGE_USER_ID=42;
const before=document.getElementById('dbody').innerHTML;
click('dbody','#svFormToggle',toggle);click('dbody','[data-svedit]',pencil);
assert.equal(document.getElementById('dbody').innerHTML,before);
assert.equal(requests.length,1);assert.equal(svEditIndex,null);
""")

    def test_empty_state_valuables_action_opens_the_valuables_form(self):
        self.run_js(r"""
APP_MODE='demo';MULTI_CASH_ENABLED=false;DATA.assets=[];renderAssets();
const html=document.getElementById('assets').innerHTML;
assert.ok(html.includes('data-empty="valuables"'));
assert.ok(html.includes('Sachwert hinzufügen'));
assert.ok(html.includes('Erfasse Sachwerte mit ihrem geschätzten Wert.'));
assert.ok(!html.includes('Konto'));assert.ok(!html.includes('Depot'));
const empty={dataset:{empty:'valuables'}};
await documentHandlers.click({target:{closest:selector=>selector==='[data-empty]'?empty:null}});
assert.deepEqual(openedAssetKinds,['valuables']);
assert.equal(accountAddOpened,0);assert.equal(requests.length,0);
""")

    def test_enabled_account_empty_state_keeps_its_existing_chooser(self):
        self.run_js(r"""
APP_MODE='demo';MULTI_CASH_ENABLED=true;DATA.assets=[];renderAssets();
const html=document.getElementById('assets').innerHTML;
assert.ok(html.includes('data-empty="financial-asset"'));
assert.ok(html.includes('Konto oder Wert hinzufügen'));
assert.ok(html.includes('Füge dein erstes Konto, Depot oder einen Vermögenswert hinzu.'));
const empty={dataset:{empty:'financial-asset'}};
await documentHandlers.click({target:{closest:selector=>selector==='[data-empty]'?empty:null}});
assert.equal(accountAddOpened,1);assert.deepEqual(openedAssetKinds,[]);
assert.equal(requests.length,0);
""")

    def test_onboarding_rejects_invalid_values_and_more_than_fifty_items(self):
        self.run_js(r"""
OB.step=5;
for(const [name,value] of [['','100'],['x'.repeat(81),'100'],['bad\nname','100'],
  ['Uhr','-5'],['Uhr','0'],['Uhr','NaN'],['Uhr','10000001'],['Uhr','12,345']]){
  document.getElementById('obValuableName').value=name;
  document.getElementById('obValuableValue').value=value;
  click('onboard','#obValuableAdd');assert.deepEqual(OB.valuables,[]);
  assert.notEqual(document.getElementById('obValuableError').textContent,'');
}
OB.valuables=Array.from({length:50},()=>({...first,id:newValuableId()}));
document.getElementById('obValuableName').value='One too many';
document.getElementById('obValuableValue').value='1';
click('onboard','#obValuableAdd');assert.equal(OB.valuables.length,50);
assert.equal(requests.length,0);
""")

    def test_finish_retries_same_completion_and_positions_without_optimistic_success(self):
        self.run_js(r"""
OB.valuables=[{...first}];OB.name='Synthetic';const completion=OB.completionId;
const pending=finishOnboarding();await finishOnboarding();
assert.equal(requests.length,1);assert.equal(pinContinues,0);
assert.equal(SERVER_ONBOARDING_REQUIRED,true);
const body=JSON.parse(requests[0].options.body);
assert.equal(requests[0].path,'/v1/onboarding');assert.equal(body.request_id,completion);
assert.deepEqual(body.valuables,[first]);
requests[0].reject(new TypeError('synthetic network failure'));await pending;
assert.equal(SERVER_ONBOARDING_REQUIRED,true);assert.equal(ONBOARDING_FINISH_IN_FLIGHT,false);
assert.deepEqual(OB.valuables,[first]);assert.equal(OB.completionId,completion);
const retry=finishOnboarding();assert.equal(requests.length,2);
assert.deepEqual(JSON.parse(requests[1].options.body),body);
respond(1,{ok:true,user_id:41});await retry;
assert.equal(SERVER_ONBOARDING_REQUIRED,false);assert.equal(pinContinues,1);
""")

    def test_optional_empty_onboarding_still_completes(self):
        self.run_js(r"""
const task=finishOnboarding();const body=JSON.parse(requests[0].options.body);
assert.deepEqual(body.valuables,[]);assert.match(body.request_id,/^[0-9a-f]{32}$/);
respond(0,{ok:true,user_id:41});await task;assert.equal(pinContinues,1);
""")

    def test_finish_foreign_or_late_ack_cannot_complete_other_owner_state(self):
        for change_owner, response_owner in ((False, '42'), (False, 'undefined'), (True, '41'), (True, '42')):
            with self.subTest(change_owner=change_owner, response_owner=response_owner):
                prefix = f"const changeOwner={str(change_owner).lower()}, responseOwner={response_owner};\n"
                self.run_js(prefix + r"""
OB.valuables=[{...first}];const task=finishOnboarding();
assert.equal(requests[0].options.headers['X-Rove-User-ID'],'41');
if(changeOwner){BRIDGE_USER_ID=42;applyServerOnboardingDraft({name:'New owner',valuables:[second]});}
const before=JSON.stringify(OB), stateBefore=JSON.stringify(DATA);
respond(0,{ok:true,user_id:responseOwner});await task;
assert.equal(BRIDGE_USER_ID,changeOwner?42:41);assert.equal(JSON.stringify(OB),before);
assert.equal(JSON.stringify(DATA),stateBefore);assert.equal(SERVER_ONBOARDING_REQUIRED,true);
assert.equal(SERVER_ONBOARDING_AWAITING_PIN,false);assert.equal(ONBOARDING_FINISH_IN_FLIGHT,false);
assert.equal(pinContinues,0);assert.equal(storageWrites.length,0);
""")

    def test_apply_rejects_stale_or_invalid_state_and_clones_confirmed_positions(self):
        self.run_js(r"""
const staleRevision=valuableStateRevision;
valuableStateRevision++;
const before=JSON.stringify(DATA);
assert.equal(applyServerValuables([first],staleRevision),false);
for(const invalid of [null,{},'bad'])assert.equal(applyServerValuables(invalid),false);
assert.equal(JSON.stringify(DATA),before);
const response=[{...first},{...second}];
assert.equal(applyServerValuables(response),true);
response[0].v=900;
assert.deepEqual(DATA.valuables,[first,second]);
const asset=DATA.assets.find(isValuableAsset);
assert.equal(asset.value,375);assert.equal(asset.source,'server-valuables');
assert.equal(DATA.assets.filter(isValuableAsset).length,1);
assert.equal(DATA.netWorth,12500);
applyServerValuables([]);
assert.deepEqual(DATA.valuables,[]);assert.equal(valuableAssetIndex(),-1);
assert.equal(DATA.assets[0].name,'Girokonto');
""")

    def test_cash_account_named_sachwerte_survives_nonempty_and_empty_apply(self):
        self.run_js(r"""
const cash=namedCashAccount(), cashBefore=JSON.stringify(cash);
DATA.assets.unshift(cash);
assert.equal(isValuableAsset(cash),false);assert.equal(valuableAssetIndex(),-1);
const legacy=legacySnapshot().assets[0];DATA.assets.push(legacy);
assert.equal(isValuableAsset(legacy),true);assert.equal(DATA.assets[valuableAssetIndex()],legacy);
applyServerValuables([first,second]);
const valuable=DATA.assets[valuableAssetIndex()];
assert.equal(valuable.source,'server-valuables');assert.equal(valuable.value,375);
assert.equal(DATA.assets.filter(isValuableAsset).length,1);assert.ok(DATA.assets.includes(cash));
assert.equal(JSON.stringify(cash),cashBefore);
applyServerValuables([]);
assert.deepEqual(DATA.valuables,[]);assert.equal(valuableAssetIndex(),-1);
assert.ok(DATA.assets.includes(cash));assert.equal(JSON.stringify(cash),cashBefore);
recalcNetWorth();assert.equal(DATA.netWorth,12500);
""")

    def test_confirmed_apply_clears_only_matching_owner_pending_create(self):
        self.run_js(r"""
pendingValuableCreate={userId:41,item:{...first}};
applyServerValuables([second]);assert.notEqual(pendingValuableCreate,null);
pendingValuableCreate={userId:42,item:{...first}};
applyServerValuables([first]);assert.equal(pendingValuableCreate.userId,42);
pendingValuableCreate={userId:41,item:{...first}};
applyServerValuables([first]);assert.equal(pendingValuableCreate,null);
""")

    def test_authoritative_empty_marker_blocks_merge_and_legacy_restore(self):
        self.run_js(r"""
const snapshot=legacySnapshot(), legacy=snapshot.assets[0];
for(const multiCash of [false,true]){
  const bank={name:'Girokonto',value:50};
  assert.deepEqual(mergeServerAssets([bank],[legacy],multiCash,true),[bank]);
  assert.deepEqual(mergeServerAssets([bank],[legacy],multiCash),[bank,legacy]);
}
restoreBridgeLocal(snapshot,41);assert.equal(valuableAssetIndex(),-1);
delete DATA.valuables;restoreBridgeLocal(snapshot,42);
assert.equal(valuableAssetIndex(),-1);
restoreBridgeLocal(snapshot,41);assert.deepEqual(DATA.assets.find(isValuableAsset),legacy);
""")

    def test_create_waits_for_server_confirmation_and_uses_exact_wire_fields(self):
        self.run_js(r"""
openValuableForm();setInputs();const before=JSON.stringify(DATA);
const task=addAssetPosition();
assert.equal(requests.length,1);assert.equal(JSON.stringify(DATA),before);
assert.equal(valuableBusy,true);assert.ok(mutationButtons().every(button=>button.disabled));
assert.equal(messages.length,0);assert.equal(buzzes,0);assert.equal(persisted,0);
const request=requests[0],body=JSON.parse(request.options.body);
assert.equal(request.path,'/v1/valuables');assert.equal(request.options.method,'POST');
assert.equal(request.options.headers['X-Rove-User-ID'],'41');
assert.deepEqual(Object.keys(body).sort(),['id','n','v']);
assert.match(body.id,/^[0-9a-f]{32}$/);assert.equal(body.n,'Neue Uhr');assert.equal(body.v,1234.56);
assert.deepEqual(pendingValuableCreate,{userId:41,item:body});
const canonical={...body,v:1200};confirmed(0,[canonical]);await task;
assert.deepEqual(DATA.valuables,[canonical]);assert.equal(pendingValuableCreate,null);
assert.equal(DATA.assets.find(isValuableAsset).value,1200);
assert.equal(DATA.netWorth,12500);assert.equal(valuableBusy,false);
assert.ok(mutationButtons().every(button=>!button.disabled));
assert.deepEqual(messages,['Sachwert hinzugefügt']);assert.equal(buzzes,1);
assert.equal(document.getElementById('svName').value,'');
assert.equal(document.getElementById('svAmt').value,'');
assert.ok(document.getElementById('dbody').innerHTML.includes('Neue Uhr'));
assert.ok(document.getElementById('dbody').innerHTML.includes('data-valuable-id="'+body.id+'"'));
assert.deepEqual(scrolledValuables,[{selector:'#dbody .valuable-row[data-valuable-id="'+body.id+'"]',options:{block:'nearest'}}]);
assert.equal(closed,0);
""")

    def test_every_valuable_mutation_sends_the_captured_owner_header(self):
        self.run_js(r"""
const cases=[['/v1/valuables','POST',first],['/v1/valuables/'+first.id,'PATCH',first],
  ['/v1/valuables/'+first.id,'DELETE'],['/v1/valuables','DELETE'],
  ['/v1/valuables/import','POST',{confirmed:true,positions:[{n:first.n,v:first.v}]}]];
for(const [path,method,payload] of cases){
  const index=requests.length, task=requestValuables(path,method,payload);
  assert.equal(requests[index].options.headers['X-Rove-User-ID'],'41');
  assert.equal(requests[index].options.headers['Content-Type'],'application/json');
  confirmed(index,[]);assert.equal(await task,true);
}
""")

    def test_failed_or_unconfirmed_creates_keep_inputs_state_and_stable_retry_id(self):
        failures = {
            'network': "requests[0].reject(new TypeError('synthetic network reset'));",
            'timeout': "const timer=[...timers.values()][0];assert.equal(timer.ms,15000);timer.callback();assert.equal(requests[0].options.signal.aborted,true);",
            'http': "respond(0,{ok:true,user_id:41,valuables:[first]},false);",
            'unconfirmed': "respond(0,{ok:false,user_id:41,valuables:[first]});",
            'missing_positions': "respond(0,{ok:true,user_id:41});",
            'invalid_positions': "respond(0,{ok:true,user_id:41,valuables:{}});",
            'invalid_json': "requests[0].resolve({ok:true,json:async()=>{throw new SyntaxError('synthetic invalid JSON');}});",
        }
        for failure, reject in failures.items():
            with self.subTest(failure=failure):
                self.run_js(r"""
openValuableForm();setInputs();const before=JSON.stringify(DATA);
const task=addAssetPosition();const sent=JSON.parse(requests[0].options.body);
""" + reject + r"""
await task;
assert.equal(JSON.stringify(DATA),before);assert.equal(valuableBusy,false);
assert.equal(document.getElementById('svName').value,'Neue Uhr');
assert.equal(document.getElementById('svAmt').value,'1.234,56');
assert.deepEqual(pendingValuableCreate,{userId:41,item:sent});
assert.equal(persisted,0);assert.equal(buzzes,0);
assert.ok(!messages.includes('Sachwert hinzugefügt'));assert.deepEqual(scrolledValuables,[]);
assert.ok(mutationButtons().every(button=>!button.disabled));
const retry=addAssetPosition();assert.equal(requests.length,2);
assert.deepEqual(JSON.parse(requests[1].options.body),sent);
confirmed(1,[sent]);await retry;
assert.deepEqual(DATA.valuables,[sent]);assert.equal(pendingValuableCreate,null);
""")

    def test_definitive_400_create_allows_corrected_input_with_a_new_id(self):
        self.run_js(r"""
openValuableForm([first]);setInputs();const before=JSON.stringify(DATA);
const task=addAssetPosition(), rejected=JSON.parse(requests[0].options.body);
requests[0].resolve({ok:false,status:400,json:async()=>({ok:false,error:'invalid_valuable_value'})});
await task;
assert.equal(JSON.stringify(DATA),before);assert.equal(pendingValuableCreate,null);
assert.equal(document.getElementById('svName').value,'Neue Uhr');
assert.equal(document.getElementById('svAmt').value,'1.234,56');
assert.equal(buzzes,0);assert.equal(persisted,0);
setInputs('Corrected object','500');const retry=addAssetPosition();
assert.equal(requests.length,2);const corrected=JSON.parse(requests[1].options.body);
assert.notEqual(corrected.id,rejected.id);assert.equal(corrected.n,'Corrected object');assert.equal(corrected.v,500);
confirmed(1,[first,corrected]);await retry;
assert.deepEqual(DATA.valuables,[first,corrected]);assert.equal(pendingValuableCreate,null);
""")

    def test_unavailable_create_does_not_reserve_an_id_or_block_corrected_input(self):
        for unavailable in ('ready=false;', 'delete DATA.valuables;'):
            with self.subTest(unavailable=unavailable):
                self.run_js(r"""
openValuableForm();setInputs();
""" + unavailable + r"""
const before=JSON.stringify(DATA);await addAssetPosition();
assert.equal(JSON.stringify(DATA),before);assert.equal(pendingValuableCreate,null);
assert.equal(requests.length,0);assert.equal(valuableBusy,false);
ready=true;DATA.valuables=[];setInputs('Corrected after reconnect','500');
const retry=addAssetPosition();assert.equal(requests.length,1);
const body=JSON.parse(requests[0].options.body);assert.equal(body.n,'Corrected after reconnect');
confirmed(0,[body]);await retry;assert.deepEqual(DATA.valuables,[body]);
""")

    def test_changed_retry_input_is_blocked_until_original_create_is_confirmed(self):
        self.run_js(r"""
openValuableForm();setInputs();const task=addAssetPosition();
const sent=JSON.parse(requests[0].options.body);
requests[0].reject(new Error('synthetic offline'));await task;
setInputs('Different object','5');await addAssetPosition();
assert.equal(requests.length,1);assert.deepEqual(pendingValuableCreate.item,sent);
assert.deepEqual(DATA.valuables,[]);
setInputs();const retry=addAssetPosition();confirmed(1,[sent]);await retry;
assert.equal(pendingValuableCreate,null);assert.deepEqual(DATA.valuables,[sent]);
""")

    def test_double_click_handler_sends_one_create_and_no_optimistic_success(self):
        self.run_js(r"""
openValuableForm();setInputs();const before=JSON.stringify(DATA);
click('dbody','#svSend');click('dbody','#svSend');await settle();
assert.equal(requests.length,1);assert.equal(JSON.stringify(DATA),before);
assert.equal(messages.length,0);assert.equal(buzzes,0);
const body=JSON.parse(requests[0].options.body);confirmed(0,[body]);await settle();
assert.deepEqual(DATA.valuables,[body]);assert.equal(buzzes,1);
assert.equal(pendingValuableCreate,null);
assert.deepEqual(messages,['Sachwert hinzugefügt']);
assert.equal(document.getElementById('svName').value,'');assert.equal(document.getElementById('svAmt').value,'');
""")

    def test_unavailable_api_or_missing_server_marker_never_mutates(self):
        self.run_js(r"""
ready=false;const before=JSON.stringify(DATA);
assert.equal(await requestValuables('/v1/valuables','POST',first),false);
assert.equal(JSON.stringify(DATA),before);ready=true;delete DATA.valuables;
assert.equal(await requestValuables('/v1/valuables','POST',first),false);
assert.equal(requests.length,0);assert.equal(valuableBusy,false);
""")

    def test_server_form_rejects_invalid_input_without_local_fallback(self):
        self.run_js(r"""
openValuableForm();const before=JSON.stringify(DATA);
for(const [name,value] of [['','10'],['x'.repeat(81),'10'],['bad\nname','10'],
  ['Uhr','-10'],['Uhr','0'],['Uhr','10000001'],['Uhr','1,234'],['Uhr','10abc']]){
  setInputs(name,value);await addAssetPosition();
  assert.equal(requests.length,0);assert.equal(JSON.stringify(DATA),before);
}
assert.equal(persisted,0);assert.equal(pendingValuableCreate,null);
""")

    def test_edit_uses_rendered_stable_id_after_asset_and_position_reordering(self):
        self.run_js(r"""
openValuableForm([first,second],0);
const cash=namedCashAccount(), cashBefore=JSON.stringify(cash);DATA.assets.unshift(cash);
assert.equal(document.getElementById('svSend').dataset.valuableOwner,'41');
assert.equal(document.getElementById('svSend').dataset.valuableId,first.id);
applyServerValuables([second,first]);
DATA.assets.reverse();dIdx=assetIndexByName('Girokonto');svEditIndex=0;
setInputs('Neue Bezeichnung','500');const before=JSON.stringify(DATA);
const task=addAssetPosition();
assert.equal(requests[0].path,'/v1/valuables/'+first.id);
assert.equal(requests[0].options.method,'PATCH');
const body=JSON.parse(requests[0].options.body);
assert.deepEqual(body,{id:first.id,n:'Neue Bezeichnung',v:500});
assert.equal(JSON.stringify(DATA),before);assert.equal(pendingValuableCreate,null);
confirmed(0,[second,body]);await task;
assert.deepEqual(DATA.valuables,[second,body]);assert.equal(DATA.netWorth,12500);
assert.equal(JSON.stringify(cash),cashBefore);assert.ok(DATA.assets.includes(cash));
assert.equal(dIdx,valuableAssetIndex());assert.notEqual(DATA.assets[dIdx],cash);
assert.deepEqual(messages,['Sachwert aktualisiert.']);
assert.equal(document.getElementById('svName').value,'');assert.equal(document.getElementById('svAmt').value,'');
""")

    def test_foreign_form_and_cross_owner_response_cannot_change_new_owner_state(self):
        self.run_js(r"""
openValuableForm();setInputs();BRIDGE_USER_ID=42;
await addAssetPosition();assert.equal(requests.length,0);
BRIDGE_USER_ID=41;const task=addAssetPosition();const sent=JSON.parse(requests[0].options.body);
BRIDGE_USER_ID=42;DATA={...DATA,assets:[{name:'Girokonto',value:7}],valuables:[],netWorth:7};
const before=JSON.stringify(DATA);const notices=messages.length;
confirmed(0,[sent],41);await task;
assert.equal(JSON.stringify(DATA),before);assert.equal(messages.length,notices);
assert.equal(buzzes,0);assert.equal(pendingValuableCreate.userId,41);assert.equal(valuableBusy,false);
openValuableForm();setInputs();const next=addAssetPosition();
const nextBody=JSON.parse(requests[1].options.body);
assert.notEqual(nextBody.id,sent.id);assert.equal(pendingValuableCreate.userId,42);
confirmed(1,[nextBody]);await next;assert.deepEqual(DATA.valuables,[nextBody]);
""")

    def test_cookie_switched_write_identity_is_rejected_with_local_owner_unchanged(self):
        for identity in ('42', 'null', 'undefined'):
            with self.subTest(identity=identity):
                self.run_js('const responseOwner=' + identity + ';\n' + r"""
const cash=namedCashAccount();DATA.assets.unshift(cash);
openValuableForm([first]);setInputs();const before=JSON.stringify(DATA);
const task=addAssetPosition(), sent=JSON.parse(requests[0].options.body);
assert.equal(requests[0].options.headers['X-Rove-User-ID'],'41');
respond(0,{ok:true,user_id:responseOwner,valuables:[{...sent,n:'Must not apply'}]});
await task;
assert.equal(BRIDGE_USER_ID,41);assert.equal(JSON.stringify(DATA),before);
assert.deepEqual(pendingValuableCreate,{userId:41,item:sent});
assert.equal(document.getElementById('svName').value,'Neue Uhr');
assert.equal(document.getElementById('svAmt').value,'1.234,56');
assert.equal(buzzes,0);assert.equal(valuableBusy,false);assert.equal(storageWrites.length,0);
assert.ok(!messages.includes('Sachwert hinzugefügt'));assert.deepEqual(scrolledValuables,[]);
const retry=addAssetPosition();assert.deepEqual(JSON.parse(requests[1].options.body),sent);
confirmed(1,[first,sent],41);await retry;
assert.deepEqual(DATA.valuables,[first,sent]);assert.ok(DATA.assets.includes(cash));
assert.equal(dIdx,valuableAssetIndex());assert.notEqual(DATA.assets[dIdx],cash);
""")

    def test_delete_handler_requires_confirmation_and_keeps_stable_id(self):
        self.run_js(r"""
openValuableForm([first,second],0);
const button=document.getElementById('svDelete');
assert.equal(button.dataset.valuableOwner,'41');assert.equal(button.dataset.valuableId,first.id);
applyServerValuables([second,first]);dIdx=assetIndexByName('Girokonto');svEditIndex=0;
click('dbody','#svDelete',button);assert.equal(requests.length,0);
const before=JSON.stringify(DATA);
click('dbody','#svDelete',button);click('dbody','#svDelete',button);
assert.equal(requests.length,1);assert.equal(requests[0].path,'/v1/valuables/'+first.id);
assert.equal(requests[0].options.method,'DELETE');assert.ok(!('body' in requests[0].options));
assert.equal(JSON.stringify(DATA),before);
requests[0].reject(new Error('synthetic offline'));await settle();
assert.equal(JSON.stringify(DATA),before);assert.equal(closed,0);
click('dbody','#svDelete',button);confirmed(1,[second]);await settle();
assert.deepEqual(DATA.valuables,[second]);assert.equal(DATA.netWorth,12500);
""")

    def test_group_delete_uses_rendered_owner_after_asset_index_changes(self):
        self.run_js(r"""
openValuableForm([first,second],0);const button=document.getElementById('svGroupDelete');
const cash=namedCashAccount(), cashBefore=JSON.stringify(cash);DATA.assets.push(cash);
assert.equal(button.dataset.valuableOwner,'41');
dIdx=assetIndexByName('Girokonto');const before=JSON.stringify(DATA);
click('dbody','#svGroupDelete',button);assert.equal(requests.length,0);
click('dbody','#svGroupDelete',button);
assert.equal(requests[0].path,'/v1/valuables');assert.equal(requests[0].options.method,'DELETE');
assert.equal(JSON.stringify(DATA),before);assert.equal(closed,0);
confirmed(0,[]);await settle();
assert.deepEqual(DATA.valuables,[]);assert.equal(valuableAssetIndex(),-1);
assert.equal(DATA.assets[0].name,'Girokonto');assert.equal(closed,1);
assert.ok(DATA.assets.includes(cash));assert.equal(JSON.stringify(cash),cashBefore);
""")

    def test_foreign_owner_delete_controls_send_nothing(self):
        self.run_js(r"""
openValuableForm([first],0);BRIDGE_USER_ID=42;
const before=JSON.stringify(DATA);
for(const selector of ['#svDelete','#svGroupDelete']){
  const button=document.getElementById(selector.slice(1));
  assert.equal(button.dataset.valuableOwner,'41');
  click('dbody',selector,button);click('dbody',selector,button);
}
assert.equal(requests.length,0);assert.equal(JSON.stringify(DATA),before);assert.equal(persisted,0);
""")

    def test_legacy_offer_is_owner_bound_and_never_imports_implicitly(self):
        self.run_js(r"""
const raw=seedLegacy();
const expected=[{n:'Uhr',v:125},{n:'Rad',v:250}];
assert.deepEqual(legacyValuablePositions(),expected);
assert.ok(legacyValuablesHtml().includes('data-import-valuables'));
renderAssets();restoreBridgeLocal(readBridgeLocal(),41);saveBridgeLocal();
assert.equal(requests.length,0);assert.deepEqual(DATA.valuables,[]);
assert.equal(valuableAssetIndex(),-1);
assert.equal(localStorage.getItem(BRIDGE_LOCAL_KEY),raw);
assert.equal(readBridgeLocal().valuablesMigrated,false);
BRIDGE_USER_ID=42;
assert.equal(readBridgeLocal(),null);assert.deepEqual(legacyValuablePositions(),[]);
assert.equal(legacyValuablesHtml(),'');await importLegacyValuables();assert.equal(requests.length,0);
""")

    def test_failed_import_preserves_original_and_confirmed_import_marks_only_scoped_receipt(self):
        self.run_js(r"""
const raw=seedLegacy();saveBridgeLocal();
const scopedBefore=localStorage.getItem(scopedKey(41));
const task=importLegacyValuables();
assert.equal(requests.length,1);assert.equal(requests[0].path,'/v1/valuables/import');
assert.equal(requests[0].options.method,'POST');
assert.equal(requests[0].options.headers['X-Rove-User-ID'],'41');
assert.deepEqual(JSON.parse(requests[0].options.body),{
  confirmed:true,positions:[{n:'Uhr',v:125},{n:'Rad',v:250}]});
assert.equal(localStorage.getItem(scopedKey(41)),scopedBefore);
requests[0].reject(new Error('synthetic offline'));await task;
assert.equal(localStorage.getItem(BRIDGE_LOCAL_KEY),raw);
assert.equal(localStorage.getItem(scopedKey(41)),scopedBefore);
assert.deepEqual(DATA.valuables,[]);assert.equal(legacyValuablePositions().length,2);
const retry=importLegacyValuables();
await importLegacyValuables();assert.equal(requests.length,2);
assert.deepEqual(JSON.parse(requests[1].options.body),JSON.parse(requests[0].options.body));
confirmed(1,[first,second]);await retry;
assert.deepEqual(DATA.valuables,[first,second]);assert.equal(localStorage.getItem(BRIDGE_LOCAL_KEY),raw);
const receipt=JSON.parse(localStorage.getItem(scopedKey(41)));
assert.equal(receipt.valuablesMigrated,true);
assert.deepEqual(receipt.assets,JSON.parse(scopedBefore).assets);
assert.deepEqual(legacyValuablePositions(),[]);assert.equal(legacyValuablesHtml(),'');
saveBridgeLocal();assert.equal(readBridgeLocal().valuablesMigrated,true);
await importLegacyValuables();assert.equal(requests.length,2);
assert.equal(localStorage.getItem(BRIDGE_LOCAL_KEY),raw);
assert.equal(storageWrites.filter(key=>key===BRIDGE_LOCAL_KEY).length,1);
""")

    def test_http_400_import_preserves_state_and_shows_validation_not_network_feedback(self):
        self.run_js(r"""
const snapshot=legacySnapshot();snapshot.assets[0].positions[0].v=-125;
const raw=seedLegacy(snapshot);applyServerValuables([second]);saveBridgeLocal();
const before=JSON.stringify(DATA), scopedBefore=localStorage.getItem(scopedKey(41));
const writesBefore=storageWrites.length;
const task=importLegacyValuables();
assert.equal(requests.length,1);assert.equal(requests[0].path,'/v1/valuables/import');
assert.equal(JSON.stringify(DATA),before);assert.equal(messages.length,0);
requests[0].resolve({ok:false,status:400,json:async()=>({ok:false,error:'invalid_valuable_value'})});
await task;
assert.equal(JSON.stringify(DATA),before);
assert.equal(localStorage.getItem(BRIDGE_LOCAL_KEY),raw);
assert.equal(localStorage.getItem(scopedKey(41)),scopedBefore);
assert.equal(storageWrites.length,writesBefore);assert.equal(readBridgeLocal().valuablesMigrated,false);
assert.deepEqual(legacyValuablePositions(),[{n:'Uhr',v:-125},{n:'Rad',v:250}]);
assert.deepEqual(messages,['Bitte pr\u00fcfe Bezeichnung und Sch\u00e4tzwert. Es wurde nichts \u00fcbernommen.']);
assert.equal(valuableBusy,false);assert.ok(mutationButtons().every(button=>!button.disabled));
assert.equal(persisted,0);assert.equal(buzzes,0);
""")

    def test_import_from_global_alone_creates_receipt_only_after_success(self):
        self.run_js(r"""
const raw=seedLegacy();const task=importLegacyValuables();
assert.equal(localStorage.getItem(scopedKey(41)),null);
respond(0,{ok:false,valuables:[]});await task;
assert.equal(localStorage.getItem(scopedKey(41)),null);
const retry=importLegacyValuables();confirmed(1,[first,second]);await retry;
const receipt=JSON.parse(localStorage.getItem(scopedKey(41)));
assert.equal(receipt.valuablesMigrated,true);assert.deepEqual(receipt.assets,JSON.parse(raw).assets);
assert.equal(localStorage.getItem(BRIDGE_LOCAL_KEY),raw);
""")

    def test_cross_owner_import_response_sets_no_migration_receipt(self):
        self.run_js(r"""
const raw=seedLegacy();const task=importLegacyValuables();
BRIDGE_USER_ID=42;const before=JSON.stringify(DATA);
confirmed(0,[first,second],41);await task;
assert.equal(JSON.stringify(DATA),before);assert.equal(localStorage.getItem(BRIDGE_LOCAL_KEY),raw);
assert.equal(localStorage.getItem(scopedKey(41)),null);assert.equal(localStorage.getItem(scopedKey(42)),null);
assert.equal(messages.length,0);assert.deepEqual(legacyValuablePositions(),[]);
""")

    def test_cookie_switched_import_identity_cannot_mark_legacy_as_migrated(self):
        self.run_js(r"""
const raw=seedLegacy();saveBridgeLocal();const scopedBefore=localStorage.getItem(scopedKey(41));
const before=JSON.stringify(DATA), writesBefore=storageWrites.length;
const task=importLegacyValuables();
assert.equal(requests[0].options.headers['X-Rove-User-ID'],'41');confirmed(0,[first,second],42);
await task;
assert.equal(BRIDGE_USER_ID,41);assert.equal(JSON.stringify(DATA),before);
assert.equal(localStorage.getItem(BRIDGE_LOCAL_KEY),raw);
assert.equal(localStorage.getItem(scopedKey(41)),scopedBefore);
assert.equal(storageWrites.length,writesBefore);assert.equal(readBridgeLocal().valuablesMigrated,false);
assert.equal(legacyValuablePositions().length,2);assert.equal(valuableBusy,false);
assert.ok(!messages.includes('Sachwerte übernommen.'));
""")

    def test_scoped_snapshot_precedence_and_owner_checks_fail_closed(self):
        self.run_js(r"""
const raw=seedLegacy();
const other=legacySnapshot(42);other.assets[0].positions=[{n:'Owner B object',v:700}];
seedLegacy(other,true);BRIDGE_USER_ID=42;
assert.deepEqual(legacyValuablePositions(),[{n:'Owner B object',v:700}]);
saveBridgeLocal();assert.equal(localStorage.getItem(BRIDGE_LOCAL_KEY),raw);
BRIDGE_USER_ID=41;
for(const bad of ['{broken',JSON.stringify(other),'null']){
  localStorage.setItem(scopedKey(41),bad);
  assert.equal(readBridgeLocal(),null);assert.deepEqual(legacyValuablePositions(),[]);
  await importLegacyValuables();
}
localStorage.removeItem(scopedKey(41));BRIDGE_USER_ID=null;
assert.equal(readBridgeLocal(),null);assert.deepEqual(legacyValuablePositions(),[]);
BRIDGE_USER_ID=41;APP_MODE='profile';assert.deepEqual(legacyValuablePositions(),[]);
assert.equal(requests.length,0);
""")

    def test_logout_preserves_unmigrated_global_and_scoped_copy_only_after_revocation(self):
        self.run_js(r"""
const raw=seedLegacy();const failed=logoutCurrentUser();
assert.equal(localStorage.getItem(scopedKey(41)),null);
respond(0,{ok:false},false);await failed;
assert.equal(BRIDGE_USER_ID,41);assert.equal(localStorage.getItem(BRIDGE_LOCAL_KEY),raw);
assert.equal(localStorage.getItem(scopedKey(41)),null);
const task=logoutCurrentUser();assert.equal(BRIDGE_USER_ID,41);
assert.equal(requests[1].path,'https://invalid.test/v1/auth/logout');
respond(1,{ok:true});await task;
assert.equal(BRIDGE_USER_ID,null);assert.equal(ROVE_API,null);
assert.equal(localStorage.getItem(BRIDGE_LOCAL_KEY),raw);
const saved=JSON.parse(localStorage.getItem(scopedKey(41)));
assert.deepEqual(saved.assets,JSON.parse(raw).assets);assert.equal(saved.valuablesMigrated,false);
assert.ok(navigation.includes('/synthetic-app'));
assert.equal(storageWrites.filter(key=>key===BRIDGE_LOCAL_KEY).length,1);
BRIDGE_USER_ID=41;assert.equal(legacyValuablePositions().length,2);
BRIDGE_USER_ID=42;assert.deepEqual(legacyValuablePositions(),[]);
""")

    def test_browser_cleanup_preserves_other_owner_global_and_scoped_data(self):
        self.run_js(r"""
const raw=seedLegacy();seedLegacy(legacySnapshot(41),true);seedLegacy(legacySnapshot(42),true);
localStorage.setItem('unrelated','keep');localStorage.setItem('rove-theme','remove');
await clearRoveBrowserData(42);
assert.equal(localStorage.getItem(BRIDGE_LOCAL_KEY),raw);
assert.notEqual(localStorage.getItem(scopedKey(41)),null);
assert.equal(localStorage.getItem(scopedKey(42)),null);assert.equal(localStorage.getItem('rove-theme'),null);
assert.equal(localStorage.getItem('unrelated'),'keep');
await clearRoveBrowserData();assert.equal(localStorage.getItem(BRIDGE_LOCAL_KEY),raw);
await clearRoveBrowserData(41);assert.equal(localStorage.getItem(BRIDGE_LOCAL_KEY),null);
assert.equal(localStorage.getItem(scopedKey(41)),null);
localStorage.setItem(BRIDGE_LOCAL_KEY,'{broken');await clearRoveBrowserData(41);
assert.equal(localStorage.getItem(BRIDGE_LOCAL_KEY),'{broken');assert.equal(requests.length,0);
""")

    def test_stale_canonical_fetch_cannot_overwrite_confirmed_mutation(self):
        self.run_js(r"""
applyServerValuables([first]);const refresh=fetchCanonicalAppState();
assert.equal(requests[0].path,'/v1/state');
const updated={...first,n:'Updated Uhr',v:500};
const mutation=requestValuables('/v1/valuables/'+first.id,'PATCH',updated);
confirmed(1,[updated]);assert.equal(await mutation,true);
respond(0,{ok:true,user_id:41,assets:[{name:'Girokonto',value:12500}],valuables:[first],netWorth:12500});
assert.equal(await refresh,true);assert.deepEqual(DATA.valuables,[updated]);
assert.equal(DATA.assets.find(isValuableAsset).value,500);assert.equal(DATA.netWorth,12500);
""")

    def test_empty_canonical_fetch_is_authoritative_and_foreign_fetch_is_ignored(self):
        self.run_js(r"""
const raw=seedLegacy();delete DATA.valuables;
restoreBridgeLocal(readBridgeLocal(),41);assert.ok(valuableAssetIndex()>=0);
const task=fetchCanonicalAppState();
respond(0,{ok:true,user_id:41,assets:[{name:'Girokonto',value:12500}],valuables:[],netWorth:12500});
assert.equal(await task,true);assert.deepEqual(DATA.valuables,[]);
assert.equal(valuableAssetIndex(),-1);assert.equal(localStorage.getItem(BRIDGE_LOCAL_KEY),raw);
const foreign=fetchCanonicalAppState();BRIDGE_USER_ID=42;const before=JSON.stringify(DATA);
respond(1,{ok:true,user_id:41,assets:[],valuables:[first],netWorth:1});
assert.equal(await foreign,false);assert.equal(JSON.stringify(DATA),before);
""")

    def test_canonical_identity_mismatch_is_rejected_without_local_owner_change(self):
        for identity, include_valuables in (('42', True), ('42', False), ('undefined', True)):
            with self.subTest(identity=identity, include_valuables=include_valuables):
                prefix = f"const responseOwner={identity}, includeValuables={str(include_valuables).lower()};\n"
                self.run_js(prefix + r"""
const cash=namedCashAccount();DATA.assets.unshift(cash);applyServerValuables([first]);
const before=JSON.stringify(DATA), writesBefore=storageWrites.length;
const task=fetchCanonicalAppState();
const foreign={ok:true,user_id:responseOwner,assets:[],netWorth:1,sts:{konto:1},tx:[],budgets:[]};
if(includeValuables)foreign.valuables=[second];
respond(0,foreign);
assert.equal(await task,false);assert.equal(BRIDGE_USER_ID,41);
assert.equal(JSON.stringify(DATA),before);assert.equal(storageWrites.length,writesBefore);
assert.ok(DATA.assets.includes(cash));
""")

    def test_legacy_canonical_response_without_identity_or_valuables_still_loads(self):
        self.run_js(r"""
delete DATA.valuables;const snapshot=legacySnapshot();
restoreBridgeLocal(snapshot,41);const legacy=DATA.assets.find(isValuableAsset);
const task=fetchCanonicalAppState();
respond(0,{ok:true,assets:[{name:'Girokonto',value:12500}],netWorth:12500});
assert.equal(await task,true);assert.equal(BRIDGE_USER_ID,41);
assert.equal(Object.prototype.hasOwnProperty.call(DATA,'valuables'),false);
assert.equal(DATA.assets.find(isValuableAsset),legacy);assert.equal(legacy.value,375);
""")

    def test_names_are_escaped_in_real_legacy_onboarding_and_detail_html(self):
        self.run_js(r"""
const name=`<img src=x onerror="evil()"> & 'watch'`;
const escaped='&lt;img src=x onerror=&quot;evil()&quot;&gt; &amp; &#39;watch&#39;';
const snapshot=legacySnapshot();snapshot.assets[0].positions=[{n:name,v:100}];seedLegacy(snapshot);
const legacy=legacyValuablesHtml();
assert.ok(legacy.includes(escaped));assert.ok(!legacy.includes(name));assert.ok(!legacy.includes('<img src=x'));
OB.step=5;OB.valuables=[{...first,n:name}];obRender();
const onboarding=document.getElementById('obBody').innerHTML;
assert.ok(onboarding.includes(escaped));assert.ok(!onboarding.includes(name));
assert.ok(onboarding.includes('aria-label="'+escaped+' entfernen"'));
openValuableForm([{...first,n:name}],0);
const detail=document.getElementById('dbody').innerHTML;
assert.ok(detail.includes(escaped));assert.ok(!detail.includes(name));
assert.ok(detail.includes('value="'+escaped+'"'));
assert.ok(detail.includes('aria-label="'+escaped+' bearbeiten"'));
assert.equal(document.getElementById('svName').value,name);assert.equal(requests.length,0);
""")

    def test_existing_profile_local_path_and_bridge_financial_truth_remain_separate(self):
        self.run_js(r"""
APP_MODE='profile';openValuableForm();setInputs('Local Uhr','500');
await addAssetPosition();assert.equal(requests.length,0);assert.equal(persisted,1);
assert.deepEqual(messages,['Sachwert hinzugefügt']);
assert.equal(DATA.assets.find(isValuableAsset).value,500);assert.equal(DATA.netWorth,13000);
APP_MODE='bridge';setCanonicalNetWorth(12500);
applyServerValuables([{...first,v:9000000}]);recalcNetWorth();
assert.equal(DATA.netWorth,12500);assert.deepEqual(PROFILE_META.netHistory,[]);
OB.wealth={giro:1000};OB.valuables=[{...first,v:9000000}];
assert.equal(obWealthSum(),1000);
assert.equal(obBuildAssets().find(a=>a.name==='Sachwerte').value,9000000);
""")


if __name__ == '__main__':
    unittest.main()
