import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path


FRONTEND = Path(__file__).resolve().parent / "frontend" / "index.html"
CASE = {
    "id": "case-1", "contract_id": "contract-1", "contract_name": "Beispielanbieter",
    "provider_name": "Beispielanbieter", "status": "REVIEW_REQUIRED", "revision": 2,
    "sender_name": "Test Nutzer", "sender_address": "Musterweg 1", "recipient": "Kundenservice",
    "contract_reference": "REF-123", "timing_choice": "next_possible", "cancellation_target_date": None,
    "generated_notice_text": "hiermit kündige ich zum nächstmöglichen Zeitpunkt.",
    "notice_sha256": "notice-hash", "missing_fields": [], "contract_changed": False,
}


class FrontendContractCancellationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = FRONTEND.read_text(encoding="utf-8")
        cls.js = cls.source.split("// ===================== CONTRACT CANCELLATION V1 =====================", 1)[1].split(
            "// ===================== END CONTRACT CANCELLATION V1 =====================", 1)[0]

    def run_js(self, body):
        script = """
const assert=require('node:assert/strict');
const initial=INITIAL;
const nodes={};
function el(id){return nodes[id]||(nodes[id]={id,value:'',checked:false,disabled:false,hidden:false,textContent:'',innerHTML:'',
 dataset:{},listeners:{},classList:{contains:()=>true,remove(){}},addEventListener(type,fn){this.listeners[type]=fn;}});}
const document={getElementById:el,querySelectorAll:()=>[]};
const escapeAccountHtml=value=>String(value??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
let BRIDGE_USER_ID=1,calls=[],fail=false,resolveRequest,abortRequest;
const apiReady=()=>true;
function apiFetch(path,options){calls.push({path,method:options.method,payload:options.body?JSON.parse(options.body):null});
 return new Promise((resolve,reject)=>{
  resolveRequest=data=>resolve({ok:!fail,json:async()=>fail?{ok:false,error:'cancellation_review_changed'}:{ok:true,...data}});
  options.signal.addEventListener('abort',()=>reject(new Error('aborted')));});}
let timer;const setTimeout=fn=>{timer=fn;return 1;};const clearTimeout=()=>{};
function openOnly(){};function showToast(value){el('toast').textContent=value;}
function openContract(name,reference){el('back').value=JSON.stringify({name,reference});}
const window={confirm:()=>true};
""".replace("INITIAL", json.dumps(CASE))
        script += self.js + "\n(async()=>{\n" + body + "\n})().catch(error=>{console.error(error);process.exitCode=1;});"
        result = subprocess.run([shutil.which("node") or "node"], input=script, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_review_shows_all_confirmation_facts_and_no_send_control(self):
        self.run_js("""
cancellationCase={...initial};renderCancellation();const html=el('vksBody').innerHTML;
for(const text of ['Beispielanbieter','REF-123','Kundenservice','Zum nächstmöglichen Zeitpunkt','Kündigungstext','Kündigung bestätigen'])
 assert.ok(html.includes(text),text);
assert.ok(html.includes('Es wird noch nichts versendet.'));assert.ok(!html.includes('Mail senden'));
assert.equal(el('vksConfirm').disabled,true);
""")

    def test_unknown_contact_and_dates_never_offer_confirmation(self):
        self.run_js("""
cancellationCase={...initial,recipient:'',timing_choice:null,missing_fields:['recipient','timing_choice']};renderCancellation();
assert.ok(!el('vksBody').innerHTML.includes('id="vksConfirm"'));
assert.ok(el('vksBody').innerHTML.includes('Bitte wählen'));
assert.ok(el('vksBody').innerHTML.includes('keine geprüfte Frist'));
""")

    def test_review_text_and_all_user_fields_are_escaped(self):
        self.run_js("""
const attack='<img src=x onerror=alert(1)>';
cancellationCase={...initial,provider_name:attack,sender_name:attack,recipient:attack,contract_reference:attack,generated_notice_text:attack};
renderCancellation();assert.ok(!el('vksBody').innerHTML.includes('<img'));
assert.ok(el('vksBody').innerHTML.includes('&lt;img'));
""")

    def test_busy_blocks_duplicate_confirmation_and_renders_confirmed_server_state(self):
        self.run_js("""
cancellationCase={...initial};el('vksConsent').checked=true;
const first=mutateCancellation('confirm',{confirmed:true,notice_sha256:initial.notice_sha256});
assert.equal(cancellationBusy,true);assert.equal(el('vksConfirm').disabled,true);
assert.equal(await mutateCancellation('confirm'),false);assert.equal(calls.length,1);
assert.deepEqual(calls[0].payload,{action:'confirm',revision:2,confirmed:true,notice_sha256:'notice-hash'});
resolveRequest({case:{...initial,status:'READY_TO_SEND'}});assert.equal(await first,true);
assert.equal(cancellationBusy,false);assert.ok(el('vksBody').innerHTML.includes('Bereit zum Versand'));
assert.ok(el('vksBody').innerHTML.includes('Der Vertrag bleibt unverändert'));
assert.ok(!el('vksBody').innerHTML.includes('id="vksConfirm"'));
""")

    def test_changed_form_invalidates_consent_until_saved_and_reviewed(self):
        self.run_js("""
cancellationCase={...initial};el('vksConsent').checked=true;
el('cancellationsheet').listeners.input({target:{id:'vksSender',closest:()=>true}});
assert.equal(cancellationReviewDirty,true);assert.equal(el('vksConsent').checked,false);
assert.equal(await mutateCancellation('confirm'),false);assert.equal(calls.length,0);
const save=mutateCancellation('review',{review:{sender_name:'Changed'}});
resolveRequest({case:{...initial,revision:3,sender_name:'Changed'}});await save;
assert.equal(cancellationReviewDirty,false);assert.equal(cancellationCase.revision,3);
assert.equal(el('vksConfirm').disabled,true);
""")

    def test_existing_active_case_reopens_without_creation_request(self):
        self.run_js("""
const opening=openCancellation('contract-1');resolveRequest({cases:[{...initial,status:'READY_TO_SEND'}]});await opening;
assert.equal(calls.length,1);assert.equal(calls[0].method,'GET');
assert.equal(cancellationCase.status,'READY_TO_SEND');assert.ok(el('vksBody').innerHTML.includes('Bereit zum Versand'));
""")

    def test_start_is_single_request_and_stale_user_response_cannot_create_or_replace_case(self):
        self.run_js("""
const opening=openCancellation('contract-1');assert.equal(calls.length,1);
await openCancellation('contract-1');assert.equal(calls.length,1);
BRIDGE_USER_ID=2;resolveRequest({cases:[]});await opening;
assert.equal(calls.length,1);assert.equal(cancellationCase,null);assert.equal(cancellationBusy,false);
""")

    def test_timeout_releases_busy_and_never_claims_confirmation(self):
        self.run_js("""
cancellationCase={...initial};el('vksConsent').checked=true;
const saving=mutateCancellation('confirm',{confirmed:true,notice_sha256:initial.notice_sha256});timer();
assert.equal(await saving,false);assert.equal(cancellationBusy,false);
assert.equal(cancellationCase.status,'REVIEW_REQUIRED');assert.equal(el('vksFeedback').dataset.error,'true');
""")

    def test_session_reset_clears_sensitive_dom_and_old_reply_cannot_unlock_new_request(self):
        self.run_js("""
cancellationCase={...initial};el('vksBody').innerHTML='private';el('vksConsent').checked=true;
const old=mutateCancellation('confirm');const oldResolve=resolveRequest;
resetCancellationView();assert.equal(cancellationCase,null);assert.equal(el('vksBody').innerHTML,'');
const next=openCancellation('other-contract');assert.equal(cancellationBusy,true);
oldResolve({case:{...initial,status:'READY_TO_SEND'}});await old;
assert.equal(cancellationBusy,true);assert.equal(cancellationCase,null);
resolveRequest({cases:[{...initial,contract_id:'other-contract'}]});await next;
assert.equal(cancellationCase.contract_id,'other-contract');assert.equal(cancellationBusy,false);
""")

    def test_failed_revision_keeps_last_confirmed_server_state(self):
        self.run_js("""
cancellationCase={...initial};el('vksConsent').checked=true;fail=true;
const saving=mutateCancellation('confirm');resolveRequest({});assert.equal(await saving,false);
assert.deepEqual(cancellationCase,initial);assert.ok(el('vksFeedback').textContent.includes('erneut'));
""")

    def test_entry_is_scoped_sheet_scrolls_and_closes_with_existing_navigation(self):
        self.assertIn('if(c) openCancellation(c.dataset.contractId)', self.source)
        self.assertIn('id="vdCancel" data-vn="${contractName}"${contractReferenceAttrs(v)}', self.source)
        self.assertIn('#cancellationsheet{max-height:', self.source)
        self.assertIn('overflow-y:auto;overscroll-behavior:contain', self.source)
        blocked = re.search(r'const SWIPE_BLOCKED_SHEET_IDS=Object.freeze\(\[(.*?)\]\)', self.source, re.S).group(1)
        self.assertIn('"cancellationsheet"', blocked)
        close = self.source.split('function closeAllSheetsSoft(){', 1)[1].split('function openOnly', 1)[0]
        self.assertIn('"cancellationsheet"', close)

    def test_all_inline_scripts_have_valid_syntax(self):
        scripts = re.findall(r'<script\b[^>]*>(.*?)</script>', self.source, re.S)
        self.assertEqual(len(scripts), 4)
        for index, script in enumerate(scripts, 1):
            result = subprocess.run([shutil.which("node") or "node", "--check"], input=script, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, f"Script {index}: {result.stderr}")


if __name__ == "__main__":
    unittest.main()
