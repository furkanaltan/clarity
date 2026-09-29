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
const downloads=[];
const document={getElementById:el,querySelectorAll:()=>[],body:{appendChild(){}},createElement:()=>({click(){downloads.push(this.download);},remove(){}})};
const escapeAccountHtml=value=>String(value??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
let DATA={vertraege:[]};const eur2=value=>`${Number(value).toFixed(2)} €`;
let BRIDGE_USER_ID=1,calls=[],fail=false,resolveRequest,abortRequest;
const apiReady=()=>true;
function apiFetch(path,options){calls.push({path,method:options.method,payload:options.body?JSON.parse(options.body):null});
 return new Promise((resolve,reject)=>{
  resolveRequest=data=>resolve({ok:!fail,blob:async()=>data.blob,json:async()=>fail?{ok:false,error:'cancellation_review_changed',...data}:{ok:true,...data}});
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
assert.equal(cancellationBusy,false);assert.ok(el('vksBody').innerHTML.includes('Bereit zum Senden'));
assert.ok(el('vksBody').innerHTML.includes('Der Vertrag bleibt unverändert'));
assert.ok(!el('vksBody').innerHTML.includes('id="vksConfirm"'));
""")

    def test_changed_form_invalidates_consent_until_saved_and_reviewed(self):
        self.run_js("""
cancellationCase={...initial};el('vksConsent').checked=true;
el('cancellationsheet').listeners.input({target:{id:'vksSender',closest:selector=>selector==='#vksReviewForm'}});
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
assert.equal(cancellationCase.status,'READY_TO_SEND');assert.ok(el('vksBody').innerHTML.includes('Bereit zum Senden'));
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

    def test_ready_requires_separate_send_consent_and_single_busy_request(self):
        self.run_js("""
cancellationCase={...initial,status:'READY_TO_SEND',send_available:true,email_sender:'info@getrove.de',reply_to:'user@example.test'};
renderCancellation();assert.ok(el('vksBody').innerHTML.includes('Antworten an'));
assert.ok(el('vksBody').innerHTML.includes('info@getrove.de'));assert.equal(el('vksSend').disabled,true);
assert.equal(await mutateCancellation('send'),false);assert.equal(calls.length,0);
el('vksSendConsent').checked=true;cancellationControls();assert.equal(el('vksSend').disabled,false);
const first=mutateCancellation('send',{confirmed:true,notice_sha256:'notice-hash'});
assert.equal(await mutateCancellation('send'),false);assert.equal(calls.length,1);
resolveRequest({case:{...initial,status:'SENT',messages:[{direction:'outbound',recipient:'cancel@example.test',transport_status:'accepted',provider_message_id:'receipt',sent_at:'2026-09-28 12:00:00'}]}});
await first;assert.equal(cancellationBusy,false);assert.ok(el('vksBody').innerHTML.includes('noch nicht belegt'));
assert.ok(!el('vksBody').innerHTML.includes('id="vksSend"'));assert.ok(el('vksBody').innerHTML.includes('Anbieterantwort dokumentieren'));
""")

    def test_send_timeout_is_fail_closed_and_reopening_only_reads_status(self):
        self.run_js("""
cancellationCase={...initial,status:'READY_TO_SEND',send_available:true};el('vksSendConsent').checked=true;
const sending=mutateCancellation('send');timer();assert.equal(await sending,false);
assert.equal(cancellationCase.status,'SENDING');assert.ok(!el('vksBody').innerHTML.includes('id="vksSend"'));
const reload=openCancellation('contract-1');resolveRequest({cases:[{...initial,status:'SENT'}]});await reload;
assert.equal(calls.length,2);assert.equal(calls[1].method,'GET');assert.equal(cancellationCase.status,'SENT');
""")

    def test_disabled_transport_and_ambiguous_failure_have_no_send_control(self):
        self.run_js("""
cancellationCase={...initial,status:'READY_TO_SEND',send_available:false};el('vksSendConsent').checked=true;renderCancellation();
assert.equal(el('vksSend').disabled,true);assert.equal(await mutateCancellation('send'),false);
cancellationCase={...initial,status:'FAILED',retry_allowed:false,error_code:'transport_outcome_unknown'};renderCancellation();
assert.ok(!el('vksBody').innerHTML.includes('id="vksSend"'));assert.ok(!el('vksBody').innerHTML.includes('id="vksCancel"'));
assert.ok(el('vksBody').innerHTML.includes('Kein erneuter Versand'));
""")

    def test_provider_response_is_escaped_and_confirmation_is_separate(self):
        self.run_js("""
const attack='<img src=x onerror=alert(1)>';
cancellationCase={...initial,status:'PROVIDER_RESPONSE',messages:[{id:'inbound',direction:'inbound',sender:attack,subject:attack,body_text:attack,received_at:'2026-09-28 12:00:00',body_sha256:'response-hash'}]};
renderCancellation();const html=el('vksBody').innerHTML;assert.ok(!html.includes('<img'));assert.ok(html.includes('&lt;img'));
assert.ok(html.includes('nicht automatisch verifiziert'));assert.equal(el('vksResponseConfirm').disabled,true);
assert.equal(await mutateCancellation('confirm_response'),false);
el('vksResponseConsent').checked=true;el('vksResponseOutcome').value='termination_confirmed';
el('cancellationsheet').listeners.click({target:{closest:selector=>selector==='#vksResponseConfirm'}});
assert.deepEqual(calls[0].payload.response,{message_id:'inbound',body_sha256:'response-hash',confirmed:true,outcome:'termination_confirmed',end_date:null});
resolveRequest({case:{...initial,status:'TERMINATION_CONFIRMED',confirmed_end_date:null}});
await new Promise(resolve=>setImmediate(resolve));assert.ok(el('vksBody').innerHTML.includes('Nicht mitgeteilt'));
""")

    def test_delivery_request_has_no_forged_evidence_or_revision(self):
        self.run_js("""
cancellationCase={...initial,status:'SENT'};const checking=mutateCancellation('delivery');
assert.deepEqual(calls[0].payload,{action:'delivery'});resolveRequest({case:{...initial,status:'SENT'}});await checking;
assert.ok(el('vksFeedback').textContent.includes('Noch kein Zustellnachweis'));
""")

    def test_brevo_delivery_states_are_short_german_and_never_claim_termination(self):
        self.run_js("""
cancellationCase={...initial,status:'SENT',status_label:'Zustellung verzögert',messages:[
 {direction:'outbound',recipient:'cancel@example.test',transport_status:'deferred',provider_message_id:'receipt'}
],events:[{event_type:'brevo_delivery_deferred',created_at:'2026-09-29 12:00:00'}]};renderCancellation();
assert.ok(el('vksBody').innerHTML.includes('Zustellung verzögert'));
assert.ok(el('vksBody').innerHTML.includes('Brevo meldet eine verzögerte Zustellung'));
assert.ok(el('vksBody').innerHTML.includes('Rov.E versendet nichts erneut'));
assert.ok(!el('vksBody').innerHTML.includes('deferred'));
cancellationCase={...initial,status:'DELIVERY_RECORDED',status_label:'Zugestellt',messages:[
 {direction:'outbound',recipient:'cancel@example.test',transport_status:'delivered',provider_message_id:'receipt',delivered_at:'2026-09-29 12:00:00'}
]};renderCancellation();
assert.ok(el('vksBody').innerHTML.includes('Zugestellt'));
assert.ok(el('vksBody').innerHTML.includes('Kündigungsbestätigung liegt damit noch nicht vor'));
""")

    def test_design_header_shows_confirmed_contract_cost_and_annual_potential(self):
        self.run_js("""
DATA.vertraege=[{cat:'Abos',items:[{id:'contract-1',n:'Beispielanbieter',a:6.99}]}];
cancellationCase={...initial,status:'DELIVERY_RECORDED',status_label:'Zugestellt'};renderCancellation();
const html=el('vksBody').innerHTML;
for(const text of ['Beispielanbieter','Abos','Zugestellt','Monatliche Kosten','6.99 €'])assert.ok(html.includes(text),text);
assert.ok(html.includes('Sparpotenzial pro Jahr'));assert.ok(html.includes('83.88 €'));
assert.ok(!html.includes('Anteil an Fixkosten'));assert.ok(!html.includes('Nächstmögliche Entlastung ab'));
assert.ok(!html.includes('termination_confirmed'));
assert.equal((html.match(/vks-primary/g)||[]).length,0);
""")

    def test_annual_potential_uses_only_known_payment_cadence(self):
        self.run_js("""
for(const [cadence,amount,expected] of [
 ['monthly',64,768],['quarterly',50,200],['half-yearly',50,100],
 ['yearly',50,50],['weekly',10,520],['biweekly',10,260],['bimonthly',10,60]
])assert.equal(cancellationAnnualAmount(amount,cadence),expected,cadence);
assert.equal(cancellationAnnualAmount(64,'unknown'),null);
assert.equal(cancellationAnnualAmount(64,'constructor'),null);
assert.equal(cancellationAnnualAmount(64,null),null);
assert.equal(cancellationAnnualAmount(null,'monthly'),null);
assert.equal(cancellationAnnualAmount(0,'monthly'),null);
""")

    def test_financial_card_uses_monthly_plan_fixed_cost_basis_and_omits_missing_values(self):
        self.run_js("""
DATA.vertraege=[{cat:'Abos',items:[{id:'contract-1',n:'Beispielanbieter',a:64},{id:'contract-2',a:1536}]}];
DATA.monthlyPlan={fixedCosts:1600};
cancellationCase={...initial,status:'READY_TO_SEND'};renderCancellation();
let html=el('vksBody').innerHTML;
for(const text of ['Monatliche Kosten','64.00 €','Sparpotenzial pro Jahr','768.00 €','Anteil an Fixkosten','4 %'])
 assert.ok(html.includes(text),text);
assert.ok(!html.includes('Nächstmögliche Entlastung ab'));
DATA.vertraege[0].items[1].a=1500;renderCancellation();html=el('vksBody').innerHTML;
assert.ok(!html.includes('Anteil an Fixkosten'));
DATA.vertraege[0].items[1].a=1536;
DATA.monthlyPlan={};renderCancellation();html=el('vksBody').innerHTML;
assert.ok(!html.includes('Anteil an Fixkosten'));
DATA.monthlyPlan={fixedCosts:0};renderCancellation();html=el('vksBody').innerHTML;
assert.ok(!html.includes('Anteil an Fixkosten'));
""")

    def test_relief_date_requires_confirmed_valid_contract_end(self):
        self.run_js("""
DATA.vertraege=[{cat:'Abos',items:[{id:'contract-1',n:'Beispielanbieter',a:64}]}];
cancellationCase={...initial,status:'SENT',confirmed_end_date:'2026-11-01'};renderCancellation();
assert.ok(!el('vksBody').innerHTML.includes('Nächstmögliche Entlastung ab'));
cancellationCase={...initial,status:'TERMINATION_CONFIRMED',confirmed_end_date:'2026-11-01'};renderCancellation();
assert.ok(el('vksBody').innerHTML.includes('Nächstmögliche Entlastung ab'));
assert.ok(el('vksBody').innerHTML.includes('01.11.2026'));
cancellationCase={...initial,status:'TERMINATION_CONFIRMED',confirmed_end_date:'2026-02-30'};renderCancellation();
assert.ok(!el('vksBody').innerHTML.includes('Nächstmögliche Entlastung ab'));
""")

    def test_missing_contract_amount_hides_financial_card_without_placeholders(self):
        self.run_js("""
DATA.vertraege=[{cat:'Abos',items:[{id:'contract-1',n:'Beispielanbieter',a:null}]}];
cancellationCase={...initial,status:'DRAFT'};renderCancellation();const html=el('vksBody').innerHTML;
assert.ok(!html.includes('vks-impact'));assert.ok(!html.includes('Monatliche Kosten'));
assert.ok(!html.includes('Sparpotenzial'));assert.ok(!html.includes('nicht verfügbar'));
""")

    def test_long_provider_name_is_kept_and_can_wrap(self):
        self.run_js("""
const name='StreamWerk International Premium Familie '+('Vertragsname '.repeat(8));
cancellationCase={...initial,provider_name:name};renderCancellation();const html=el('vksBody').innerHTML;
assert.ok(html.includes(name));assert.ok(!html.includes('…'));
""")

    def test_timeline_uses_event_time_in_berlin_and_hides_internal_states_and_transport_ids(self):
        self.run_js("""
cancellationCase={...initial,status:'SENT',events:[
 {event_type:'sent',created_at:'2026-09-29 11:00:00'},
 {event_type:'brevo_delivery_sent',created_at:'2026-09-29 12:00:00'},
 {event_type:'brevo_delivery_delivered',created_at:'2026-09-29 12:00:00'},
 {event_type:'sending',created_at:'2026-09-29 12:30:00'},
 {event_type:'unknown_private_state',created_at:'2026-09-29 12:40:00'}
],messages:[{direction:'outbound',transport_status:'delivered',provider_message_id:'private-message-id',body_sha256:'private-fingerprint',sent_at:'2026-09-29 12:00:00',delivered_at:'2026-09-29 12:30:00'}]};
renderCancellation();const html=el('vksBody').innerHTML;
assert.ok(html.includes('Gesendet'));assert.ok(html.includes('Zugestellt'));assert.ok(html.includes('Anbieterbestätigung ausstehend'));
const timelineHtml=html.split('<ol class="vks-timeline">')[1]||'';
assert.equal(timelineHtml.split('>Gesendet</span>').length-1,1);
assert.ok(html.includes('29.09.2026, 14:00'));
assert.ok(!html.includes('2026-09-29 11:00:00'));assert.ok(!html.includes('UTC'));
assert.ok(!html.includes('private-message-id'));assert.ok(!html.includes('private-fingerprint'));
assert.ok(!html.includes('unknown_private_state'));assert.ok(!html.includes('Versand gestartet'));
""")

    def test_only_next_action_is_prominent_and_provider_response_is_explicitly_user_documented(self):
        self.run_js("""
cancellationCase={...initial,status:'READY_TO_SEND',send_available:true,email_sender:'info@getrove.de',reply_to:'user@example.test'};
renderCancellation();assert.equal((el('vksBody').innerHTML.match(/vks-primary/g)||[]).length,1);
        assert.ok(el('vksBody').innerHTML.includes('id="vksSend"'));
        cancellationCase={...initial,status:'SENT',messages:[{direction:'outbound',transport_status:'accepted'}]};
        renderCancellation();const sendHtml=el('vksBody').innerHTML;
        assert.equal((sendHtml.match(/vks-primary/g)||[]).length,1);
        assert.ok(sendHtml.includes('<summary class="vks-action-summary vks-primary">Anbieterantwort dokumentieren</summary>'));
        cancellationCase={...initial,status:'PROVIDER_RESPONSE',messages:[{direction:'inbound',sender:'service@example.test',subject:'Antwort',body_text:'Wir melden uns.',received_at:'2026-09-29 12:00:00'}]};
renderCancellation();const html=el('vksBody').innerHTML;
assert.equal((html.match(/vks-primary/g)||[]).length,1);
assert.ok(html.includes('Antwort des Anbieters'));assert.ok(html.includes('Vom Nutzer dokumentiert'));
assert.ok(html.includes('nicht automatisch verifiziert'));assert.ok(html.includes('id="vksResponseConfirm"'));
""")

    def test_vks_design_remains_scoped_responsive_and_accessible(self):
        self.assertIn('#cancellationsheet .vks-header', self.source)
        self.assertIn('@media(max-width:380px)', self.source)
        self.assertIn('@media(prefers-reduced-transparency:reduce),(prefers-contrast:more)', self.source)
        self.assertIn('@media(prefers-reduced-motion:reduce)', self.source)
        self.assertIn('#cancellationsheet .vks-button:focus-visible', self.source)
        self.assertIn('overflow-wrap:anywhere', self.source)

    def test_invalidated_server_case_replaces_old_confirmation(self):
        self.run_js("""
cancellationCase={...initial,status:'READY_TO_SEND',send_available:true};el('vksSendConsent').checked=true;
const sending=mutateCancellation('send',{confirmed:true,notice_sha256:initial.notice_sha256});fail=true;
resolveRequest({case:{...initial,status:'REVIEW_REQUIRED',revision:3,generated_notice_text:'Changed provider notice',user_confirmed_at:null}});
assert.equal(await sending,false);assert.equal(cancellationCase.status,'REVIEW_REQUIRED');assert.equal(cancellationCase.revision,3);
assert.ok(el('vksBody').innerHTML.includes('Changed provider notice'));assert.ok(!el('vksBody').innerHTML.includes('id="vksSend"'));
""")

    def test_response_form_uses_actual_date_and_does_not_auto_confirm(self):
        self.run_js("""
cancellationCase={...initial,status:'SENT'};
el('vksResponseSender').value='service@example.test';el('vksResponseSubject').value='Antwort';
el('vksResponseBody').value='Wir bestätigen.';el('vksResponseDate').value='2026-09-28T12:00:00';
el('cancellationsheet').listeners.submit({target:{id:'vksResponseForm'},preventDefault(){}});
assert.equal(calls[0].payload.action,'response');assert.equal(calls[0].payload.response.sender,'service@example.test');
assert.ok(calls[0].payload.response.received_at.endsWith('Z'));assert.equal(calls[0].payload.confirmed,undefined);
resolveRequest({case:{...initial,status:'PROVIDER_RESPONSE'}});await new Promise(resolve=>setImmediate(resolve));
assert.equal(cancellationCase.status,'PROVIDER_RESPONSE');
""")

    def test_followup_reminds_but_never_automatically_sends(self):
        self.run_js("""
cancellationCase={...initial,status:'FOLLOW_UP_DUE',reminder:{due:true,manual_review_due:false},messages:[{direction:'outbound',transport_status:'accepted'}]};
renderCancellation();assert.equal(calls.length,0);assert.ok(el('vksBody').innerHTML.includes('Nachfrage vorbereiten'));
assert.ok(el('vksBody').innerHTML.includes('keine rechtliche Frist'));assert.ok(!el('vksBody').innerHTML.includes('id="vksSend"'));
el('cancellationsheet').listeners.click({target:{closest:selector=>selector==='#vksFollowup'}});
assert.equal(calls[0].payload.action,'followup');
resolveRequest({case:{...cancellationCase,status:'FOLLOW_UP_PREPARED',followups:[{body_text:'Nachfrage zum ursprünglichen Termin'}]}});
await new Promise(resolve=>setImmediate(resolve));assert.ok(el('vksBody').innerHTML.includes('Nicht durch Rov.E versendet.'));
assert.equal(calls.length,1);
""")

    def test_manual_review_and_final_states_show_next_actions_not_internal_names(self):
        self.run_js("""
for(const status of ['MANUAL_REVIEW_REQUIRED','TERMINATION_CONFIRMED','FAILED','CANCELLED']){
 cancellationCase={...initial,status};renderCancellation();
 assert.ok(!el('vksBody').innerHTML.includes(status));assert.ok(el('vksBody').innerHTML.includes('Kündigungsakte als PDF'));
 assert.ok(!el('vksBody').innerHTML.includes('id="vksFollowup"'));
}
cancellationCase={...initial,status:'MANUAL_REVIEW_REQUIRED',messages:[{direction:'outbound',transport_status:'accepted'}]};renderCancellation();
assert.ok(el('vksBody').innerHTML.includes('Anbieterantwort dokumentieren'));
cancellationCase={...initial,status:'SENT'};renderCancellation();assert.ok(el('vksBody').innerHTML.includes('noch nicht belegt'));
""")

    def test_prepared_followup_and_timeline_are_escaped(self):
        self.run_js("""
cancellationCase={...initial,status:'FOLLOW_UP_PREPARED',followups:[{body_text:'<img src=x onerror=alert(1)>'}],events:[{event_type:'follow_up_prepared',created_at:'<img src=x>'}]};
renderCancellation();assert.ok(!el('vksBody').innerHTML.includes('<img'));assert.ok(el('vksBody').innerHTML.includes('&lt;img'));
assert.ok(el('vksBody').innerHTML.includes('Nachfrage vorbereitet'));
assert.equal(calls.length,0);
""")

    def test_private_pdf_download_is_single_busy_get(self):
        self.run_js("""
cancellationCase={...initial};const first=downloadCancellationFile();
assert.equal(await downloadCancellationFile(),false);assert.equal(calls.length,1);
assert.equal(calls[0].method,'GET');assert.ok(calls[0].path.endsWith('/pdf'));assert.equal(calls[0].payload,null);
resolveRequest({blob:new Blob(['%PDF-test'],{type:'application/pdf'})});assert.equal(await first,true);
assert.deepEqual(downloads,['RovE_Kuendigungsakte.pdf']);assert.equal(cancellationBusy,false);
""")

    def test_pdf_finishing_after_user_change_does_not_download(self):
        self.run_js("""
cancellationCase={...initial};const pending=downloadCancellationFile();BRIDGE_USER_ID=2;
resolveRequest({blob:new Blob(['%PDF-test'])});assert.equal(await pending,false);assert.deepEqual(downloads,[]);
""")

    def test_pdf_error_does_not_leave_busy_or_fake_success(self):
        self.run_js("""
cancellationCase={...initial};const pending=downloadCancellationFile();fail=true;
resolveRequest({error:'cancellation_pdf_unavailable'});assert.equal(await pending,false);
assert.equal(cancellationBusy,false);assert.deepEqual(downloads,[]);assert.ok(el('vksFeedback').textContent.includes('nicht verfügbar'));
""")

    def test_internal_mail_gate_copy_is_distinct_from_public_send(self):
        self.run_js("""
cancellationCase={...initial,status:'READY_TO_SEND',send_available:false,send_gate_error:'cancellation_live_not_approved'};
renderCancellation();assert.ok(el('vksBody').innerHTML.includes('Öffentlicher Mailversand ist noch nicht freigegeben'));
cancellationCase={...initial,status:'READY_TO_SEND',send_available:true,mail_mode:'test'};
renderCancellation();assert.ok(el('vksBody').innerHTML.includes('Interner Mailtest'));
""")

    def test_confirmed_end_copy_does_not_auto_delete_or_adjust_plan(self):
        self.run_js("""
cancellationCase={...initial,status:'TERMINATION_CONFIRMED',contract_ended:true,confirmed_end_date:'2020-01-01'};
renderCancellation();assert.ok(el('vksBody').innerHTML.includes('Beendet laut bestätigtem Enddatum'));
assert.ok(el('vksBody').innerHTML.includes('Monatsplan bleibt unverändert'));
""")

    def test_unusual_end_date_warns_but_does_not_block_user_confirmation(self):
        self.run_js("""
assert.equal(cancellationEndDateLooksUnusual('2031-01-01',new Date(2026,0,1)),false);
assert.equal(cancellationEndDateLooksUnusual('2031-01-02',new Date(2026,0,1)),true);
cancellationCase={...initial,status:'PROVIDER_RESPONSE',revision:7,messages:[
 {direction:'outbound',transport_status:'accepted'},
 {direction:'inbound',id:'reply-1',body_sha256:'reply-hash'}
]};
renderCancellation();el('vksEndDate').value='2099-01-01';
el('cancellationsheet').listeners.input({target:{id:'vksEndDate',closest:()=>false}});
assert.equal(el('vksEndDateWarning').hidden,false);
el('vksResponseOutcome').value='termination_confirmed';el('vksResponseConsent').checked=true;cancellationControls();
assert.equal(el('vksResponseConfirm').disabled,false);
el('cancellationsheet').listeners.click({target:{closest:selector=>selector==='#vksResponseConfirm'}});
assert.equal(calls[0].payload.action,'confirm_response');assert.equal(calls[0].payload.response.end_date,'2099-01-01');
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
