"""Run the real account request function with synthetic transport responses."""
import subprocess
import unittest
from pathlib import Path
from test_frontend_valuables import function


class AccountCreateInteractionTests(unittest.TestCase):
    def test_retry_double_click_owner_and_payload_rules(self):
        source = Path(__file__).with_name("frontend").joinpath("index.html").read_text()
        script = r'''
const assert=require('node:assert/strict');
let FINANCIAL_ACCOUNT_BUSY=false, BRIDGE_USER_ID=1, appDataRefreshInFlight=false;
const PENDING_ACCOUNT_CREATES=new Map(), calls=[], messages=[];
const apiReady=()=>true, showToast=text=>messages.push(text);
const refreshAppDataFromServer=async()=>{};
let reply=async()=>{throw new Error('synthetic network failure');};
const apiFetch=async(path,options)=>{calls.push({path,...options,body:JSON.parse(options.body)});return reply();};
'''+function(source, "financialAccountRequest")+r'''
(async()=>{
  const payload={type:'checking',name:'Synthetic',balance:100};
  assert.equal(await financialAccountRequest('/v1/financial-accounts','POST',payload),false);
  const first=calls[0].body.request_id;
  assert.ok(first);
  assert.equal(calls[0].headers['X-Rove-User-ID'],'1');
  await financialAccountRequest('/v1/financial-accounts','POST',{...payload,balance:101});
  assert.equal(calls.length,1,'An unconfirmed create must not be replaced with changed data');
  reply=async()=>({ok:true,status:200,json:async()=>({ok:true,user_id:1,accountId:123})});
  assert.ok(await financialAccountRequest('/v1/financial-accounts','POST',payload));
  assert.equal(calls[1].body.request_id,first);
  assert.equal(PENDING_ACCOUNT_CREATES.size,0);
  let release;
  reply=()=>new Promise(resolve=>release=resolve);
  const pending=financialAccountRequest('/v1/financial-accounts','POST',payload);
  assert.equal(await financialAccountRequest('/v1/financial-accounts','POST',payload),false);
  assert.equal(calls.length,3);
  release({ok:true,status:200,json:async()=>({ok:true,user_id:1,accountId:124})});
  await pending;
  reply=async()=>({ok:false,status:400,json:async()=>({ok:false,error:'invalid_financial_account_name'})});
  assert.equal(await financialAccountRequest('/v1/financial-accounts','POST',payload),false);
  assert.equal(PENDING_ACCOUNT_CREATES.size,0,'A definite validation rejection allows correction');
  reply=async()=>({ok:true,status:200,json:async()=>({ok:true,user_id:2,accountId:125})});
  assert.equal(await financialAccountRequest('/v1/financial-accounts','POST',payload),false);
  assert.equal(PENDING_ACCOUNT_CREATES.size,1,'A foreign acknowledgement cannot complete a create');
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
        run = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=20)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
