import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path


FRONTEND = Path(__file__).resolve().parent / "frontend" / "index.html"
BUFFER = {"available_cash": 4600, "monthly_basis": 2000, "basis_type": "fixed_costs",
          "covered_months": 2.3, "target_amount": 6000, "target_months": 3, "gap": 1400}


class FrontendBufferTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = FRONTEND.read_text(encoding="utf-8")
        cls.buffer_js = cls.source.split("// ===================== PUFFER =====================", 1)[1].split(
            "// ===================== CASHFLOW-ANALYSE =====================", 1)[0]
        cls.amount_js = re.search(r"function parseGermanAmount\(raw\)\{.*?\n\}", cls.source, re.S).group()

    def run_js(self, body):
        node = shutil.which("node")
        self.assertIsNotNone(node)
        script = """
const assert=require('node:assert/strict');
const initial=INITIAL;
const DATA={buffer:{...initial},goals:[{id:'untouched',cur:300}]};
const nodes={};
function el(id){return nodes[id]||(nodes[id]={textContent:'',innerHTML:'',dataset:{},hidden:true,disabled:false,value:'',
  classList:{contains:()=>true},focus(){},listeners:{},addEventListener(t,fn){this.listeners[t]=fn;}});}
const document={getElementById:el,querySelectorAll:()=>[el('bufferTargetInput'),el('bufferRemove'),el('bufferEdit')]};
const eur2=value=>Number(value).toLocaleString('de-DE',{style:'currency',currency:'EUR'});
let BRIDGE_USER_ID=1,requests=[],responseBuffer={...initial,target_amount:8000,target_months:4,gap:3400};
let resolveRequest,failed=false,timeoutCallback;
const setTimeout=fn=>{timeoutCallback=fn;return 1;};
const clearTimeout=()=>{};
const apiReady=()=>true;
function apiFetch(path,options){requests.push({path,payload:JSON.parse(options.body)});return new Promise((resolve,reject)=>{
  options.signal.addEventListener('abort',()=>reject(new Error('aborted')));
  resolveRequest=()=>resolve({ok:!failed,json:async()=>failed?{ok:false,error:'failed'}:{ok:true,buffer:responseBuffer}});
});}
const refreshAppDataFromServer=async()=>true;
const go=()=>{};
""".replace("INITIAL", json.dumps(BUFFER))
        script += self.amount_js + self.buffer_js + "\n(async()=>{\n" + body + "\n})().catch(e=>{console.error(e);process.exitCode=1;});"
        result = subprocess.run([node], input=script, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_real_coverage_target_and_gap_are_visible(self):
        self.run_js("""
renderBuffer();
const html=el('bufferContent').innerHTML;
assert.ok(html.includes('2,3 Monate'));
assert.ok(html.includes('3,0 Monaten'));
assert.ok(html.includes('4.600,00'));
assert.ok(html.includes('6.000,00'));
assert.ok(html.includes('1.400,00'));
assert.ok(html.includes('role="progressbar"'));
""")

    def test_no_target_has_no_gap_progress_or_warning(self):
        self.run_js("""
DATA.buffer={...initial,target_amount:null,target_months:null,gap:null};renderBuffer();
const html=el('bufferContent').innerHTML;
assert.ok(html.includes('Noch kein persönliches Puffer-Ziel festgelegt.'));
assert.ok(!html.includes('progressbar'));assert.ok(!html.includes('Noch offen'));
assert.ok(html.includes('Ziel festlegen'));
""")

    def test_no_cost_basis_keeps_cash_but_does_not_invent_months(self):
        self.run_js("""
DATA.buffer={...initial,monthly_basis:0,covered_months:null,target_months:null};renderBuffer();
const html=el('bufferContent').innerHTML;
assert.ok(html.includes('4.600,00'));
assert.ok(html.includes('Hinterlege deine monatlichen Fixkosten'));
assert.ok(!html.includes('0,0 Monate'));assert.ok(!html.includes('entspricht rund'));
""")

    def test_missing_server_data_is_not_replaced_by_local_assets(self):
        self.run_js("""
DATA.buffer=null;DATA.netWorth=500000;DATA.assets=[{value:99999}];renderBuffer();
assert.ok(!el('bufferContent').innerHTML.includes('99999'));
assert.ok(!el('bufferContent').innerHTML.includes('0,00'));
assert.ok(el('bufferContent').innerHTML.includes('Kontodaten geladen'));
""")

    def test_save_uses_confirmed_server_state_and_blocks_duplicates_and_stale_refresh(self):
        self.run_js("""
const revision=bufferStateRevision;
el('bufferTargetInput').value='8.000';
const save=saveBufferTarget();
assert.equal(bufferWriteInFlight,true);assert.equal(el('bufferTargetInput').disabled,true);
assert.equal(await saveBufferTarget(),false);assert.equal(requests.length,1);
assert.deepEqual(requests[0],{path:'/v1/profile',payload:{buffer_target_amount:8000}});
assert.equal(applyServerBuffer({...initial,target_amount:100},revision),false);
resolveRequest();assert.equal(await save,true);
assert.equal(DATA.buffer.target_amount,8000);assert.equal(DATA.buffer.gap,3400);
assert.ok(el('bufferContent').innerHTML.includes('8.000,00'));
assert.equal(applyServerBuffer(initial,revision),false);
assert.equal(DATA.buffer.target_amount,8000);
assert.deepEqual(DATA.goals,[{id:'untouched',cur:300}]);
assert.equal(bufferWriteInFlight,false);assert.equal(el('bufferTargetInput').disabled,false);
""")

    def test_failed_save_keeps_confirmed_data_and_reenables_editor(self):
        self.run_js("""
failed=true;el('bufferTargetInput').value='8.000';el('bufferTargetForm').hidden=false;
const save=saveBufferTarget();resolveRequest();assert.equal(await save,false);
assert.deepEqual(DATA.buffer,initial);assert.equal(el('bufferTargetForm').hidden,false);
assert.equal(el('bufferStatus').dataset.error,'true');assert.equal(bufferWriteInFlight,false);
assert.equal(el('bufferTargetInput').disabled,false);
""")

    def test_remove_and_reopen_use_server_preference(self):
        self.run_js("""
responseBuffer={...initial,target_amount:null,target_months:null,gap:null};
const save=saveBufferTarget(true);assert.equal(requests[0].payload.buffer_target_amount,null);
resolveRequest();assert.equal(await save,true);editBufferTarget();
assert.equal(el('bufferTargetInput').value,'');assert.equal(el('bufferRemove').hidden,true);
assert.ok(!el('bufferContent').innerHTML.includes('progressbar'));
""")

    def test_network_timeout_releases_busy_without_claiming_a_saved_target(self):
        self.run_js("""
el('bufferTargetInput').value='8.000';const save=saveBufferTarget();
timeoutCallback();assert.equal(await save,false);
assert.deepEqual(DATA.buffer,initial);assert.equal(bufferWriteInFlight,false);
assert.equal(el('bufferTargetInput').disabled,false);
assert.ok(el('bufferStatus').textContent.includes('nicht bestätigt'));
assert.equal(requests.length,1);
""")

    def test_late_save_for_previous_user_does_not_replace_buffer(self):
        self.run_js("""
el('bufferTargetInput').value='8.000';const save=saveBufferTarget();
BRIDGE_USER_ID=2;DATA.buffer={...initial,target_amount:100};
resolveRequest();assert.equal(await save,false);
assert.equal(DATA.buffer.target_amount,100);assert.equal(bufferWriteInFlight,false);
""")

    def test_invalid_target_never_starts_write(self):
        self.run_js("""
for(const raw of ['', '-20', '0', 'garbage', '1.000.001']){
  el('bufferTargetInput').value=raw;assert.equal(await saveBufferTarget(),false);
}
assert.equal(requests.length,0);assert.deepEqual(DATA.buffer,initial);
""")

    def test_progress_is_capped_and_negative_cash_not_described_as_positive_reserve(self):
        self.run_js("""
DATA.buffer={...initial,available_cash:7000,gap:0};renderBuffer();
assert.ok(el('bufferContent').innerHTML.includes('aria-valuenow="100"'));
DATA.buffer={...initial,available_cash:-100,covered_months:-0.05,gap:6100};renderBuffer();
assert.ok(el('bufferContent').innerHTML.includes('kein positiver Puffer'));
assert.ok(el('bufferContent').innerHTML.includes('aria-valuenow="0"'));
""")

    def test_authenticated_initial_and_refresh_state_both_carry_buffer(self):
        self.assertIn('data-action="buffer">Puffer</button>', self.source)
        self.assertIn('applyServerBuffer(data.buffer,bufferRevisionAtFetch)', self.source)
        self.assertIn('applyServerBuffer(b.buffer,bufferRevisionAtLoad)', self.source)
        self.assertIn('if(tab==="buffer") renderBuffer();', self.source)
        self.assertNotIn('localStorage', self.buffer_js)
        self.assertNotIn('/v1/goals', self.buffer_js)


if __name__ == "__main__":
    unittest.main()
