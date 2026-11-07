import {test} from 'node:test';
import assert from 'node:assert/strict';
import worker,{OperatorStore} from './index.mjs';
import {validate,render} from './schema.mjs';
const secret='s'.repeat(40),token='12345:'+ 'a'.repeat(30);
function snapshot(){return {schema_version:1,generated_at:new Date().toISOString(),opportunities:[{id:'a'.repeat(24),qualification:'REVIEW_REQUIRED',lifecycle:'EVIDENCE_REVIEW',official_url:'https://example.org/campaign',reward_class:'UNKNOWN_REWARD',missing:['gas_costs'],deadline:null}],confirmed_receipts:0,monitor:{status:'ZERO_TRANSFERS',last_success_at:null,gaps:['TOKEN_ACCOUNT_HISTORY_NOT_ENUMERATED']}};}
class Storage {
  map=new Map(); tail=Promise.resolve(); alarm=null;
  async get(k){return structuredClone(this.map.get(k));}
  async put(k,v){this.map.set(k,structuredClone(v));}
  async delete(k){this.map.delete(k);}
  async transaction(f){const before=this.tail;let release;this.tail=new Promise(r=>release=r);await before;try{return await f(this);}finally{release();}}
  async getAlarm(){return this.alarm;}async setAlarm(v){this.alarm=v;}
  async list(){return new Map([...this.map].filter(([k])=>k.startsWith('update:')));}
}
function setup(){const storage=new Storage();const env={TELEGRAM_WEBHOOK_SECRET:secret,SNAPSHOT_WRITE_TOKEN:secret,TELEGRAM_BOT_TOKEN:token,OPERATOR_USER_IDS:'123',PERMITTED_CHAT_ID:'-456'};const obj=new OperatorStore({storage},env);env.OPERATOR={idFromName:x=>x,get:()=>obj};return {storage,env,obj};}
const update=(id=1,changes={})=>({update_id:id,message:{date:Math.floor(Date.now()/1000),from:{id:123,is_bot:false},chat:{id:-456},text:'/status',...changes}});
const req=(body,path='/telegram',headers={})=>new Request('https://worker.example'+path,{method:path==='/snapshot'?'PUT':'POST',headers:{'X-Telegram-Bot-Api-Secret-Token':secret,Authorization:'Bearer '+secret,...headers},body:JSON.stringify(body)});
async function sending(f){const old=globalThis.fetch;let calls=0;globalThis.fetch=async()=>{calls++;return Response.json({ok:true,result:{message_id:1,chat:{id:-456}}});};try{await f(()=>calls);}finally{globalThis.fetch=old;}}

test('strict snapshot schema excludes private extras and unknown values',()=>{
 assert.ok(validate(snapshot()));
 for(const mutation of [s=>s.token='secret',s=>s.opportunities[0].notes='private',s=>s.monitor.wallet='private',s=>s.opportunities[0].official_url='https://user:secret@example.org',s=>s.confirmed_receipts=-1,s=>s.schema_version=2,s=>s.generated_at=new Date(Date.now()+600000).toISOString()]){const s=snapshot();mutation(s);assert.equal(validate(s),false);}
});
test('all commands are readonly and stale data cannot imply current eligibility',()=>{
 const s=snapshot(),before=structuredClone(s);
 for(const cmd of ['/status','/worklist','/opportunity '+s.opportunities[0].id,'/rewards','/monitor','/approve'])assert.ok(render(cmd,s));
 assert.deepEqual(s,before);s.generated_at=new Date(Date.now()-90000000).toISOString();assert.match(render('/worklist',s),/STALE/);assert.match(render('/status',null),/unavailable/);
});
test('webhook secret and snapshot authentication fail closed',async()=>{
 const {env}=setup();assert.equal((await worker.fetch(req(update(),'/telegram',{'X-Telegram-Bot-Api-Secret-Token':'wrong'}),env)).status,403);
 assert.equal((await worker.fetch(req(snapshot(),'/snapshot',{Authorization:'Bearer wrong'}),env)).status,403);
});
test('operator identity, chat, forwarded anonymous and bot messages rejected',async()=>sending(async calls=>{
 const {env}=setup();
 for(const change of [{from:{id:999,is_bot:false}},{chat:{id:999}},{forward_origin:{}},{forward_from:{}},{sender_chat:{id:-456}},{from:{id:123,is_bot:true}},{via_bot:{}},{date:1}])assert.equal((await worker.fetch(req(update(1,change)),env)).status,200);
 assert.equal(calls(),0);
}));
test('concurrent duplicate deliveries send at most once',async()=>sending(async calls=>{
 const {env,storage}=setup();await storage.put('snapshot',snapshot());
 const results=await Promise.all(Array.from({length:20},()=>worker.fetch(req(update()),env)));
 assert.equal(calls(),1);assert.ok(results.every(r=>r.status===200));
 assert.equal((await storage.get('update:1')).outcome,'SENT');
}));
test('snapshot writes reject stale or conflicting overwrite',async()=>{
 const {env,storage}=setup(),s=snapshot();assert.equal((await worker.fetch(req(s,'/snapshot'),env)).status,200);
 assert.equal((await worker.fetch(req(s,'/snapshot'),env)).status,200);
 const old=structuredClone(s);old.generated_at=new Date(Date.now()-10000).toISOString();assert.equal((await worker.fetch(req(old,'/snapshot'),env)).status,409);
 const conflicting=structuredClone(s);conflicting.confirmed_receipts=99;assert.equal((await worker.fetch(req(conflicting,'/snapshot'),env)).status,409);
 assert.deepEqual(await storage.get('snapshot'),s);
});
test('storage failure before claim is retriable without sending',async()=>sending(async calls=>{
 const {env,storage}=setup();storage.get=async()=>{throw Error('fixture');};assert.equal((await worker.fetch(req(update()),env)).status,503);assert.equal(calls(),0);
}));
test('ambiguous send timeout is never retried',async()=>{
 const {env,storage}=setup();const original=fetch;let calls=0;globalThis.fetch=async()=>{calls++;throw Error('fixture timeout');};
 try{assert.equal(await (await worker.fetch(req(update()),env)).text(),'DELIVERY_UNCONFIRMED');await worker.fetch(req(update()),env);assert.equal(calls,1);assert.equal(await storage.get('last_delivery'),undefined);}finally{globalThis.fetch=original;}
});
test('Telegram rejection is recorded without a duplicate retry',async()=>{
 const {env,storage}=setup();const original=fetch;globalThis.fetch=async()=>Response.json({ok:false},{status:429});
 try{assert.equal(await (await worker.fetch(req(update()),env)).text(),'TELEGRAM_RATE_LIMIT');assert.equal((await storage.get('last_outcome')).outcome,'TELEGRAM_RATE_LIMIT');}finally{globalThis.fetch=original;}
});
test('missing snapshot produces explicit unavailable reply',async()=>sending(async calls=>{const {env}=setup();await worker.fetch(req(update()),env);assert.equal(calls(),1);}));
test('bounded bodies and missing configuration are rejected',async()=>{const {env}=setup();assert.equal((await worker.fetch(req({padding:'x'.repeat(17000)}),env)).status,400);env.OPERATOR_USER_IDS='';assert.equal((await worker.fetch(req(update()),env)).status,503);});
test('claimed update survives a worker restart and expired replay stays rejected',async()=>sending(async calls=>{
 const {storage,env}=setup();await worker.fetch(req(update()),env);env.OPERATOR.get=()=>new OperatorStore({storage},env);await worker.fetch(req(update()),env);
 await worker.fetch(req(update(2,{date:Math.floor(Date.now()/1000)-90000})),env);assert.equal(calls(),1);
}));
test('redirects do not forward bot credentials',async()=>{
 const {env}=setup();const old=fetch;globalThis.fetch=async (url,options)=>{assert.equal(options.redirect,'manual');return new Response(null,{status:302,headers:{location:'https://untrusted.example'}});};
 try{assert.equal(await (await worker.fetch(req(update()),env)).text(),'TELEGRAM_REJECTED');}finally{globalThis.fetch=old;}
});
test('private command text is not persisted in health metadata',async()=>sending(async()=>{
 const {env,storage}=setup();await worker.fetch(req(update(1,{text:'/PRIVATE_PARTICIPANT_NOTE'})),env);
 assert.equal((await storage.get('last_delivery')).command,'UNSUPPORTED');assert.ok(!JSON.stringify([...storage.map]).includes('PRIVATE_PARTICIPANT_NOTE'));
}));
test('expired replay remains blocked after claim cleanup',async()=>sending(async calls=>{
 const {env,storage,obj}=setup();await storage.put('update:1',{at:Date.now()-180000000,outcome:'SENT'});await obj.alarm();assert.equal(await storage.get('update:1'),undefined);
 await worker.fetch(req(update(1,{date:Math.floor(Date.now()/1000)-90000})),env);assert.equal(calls(),0);
}));
