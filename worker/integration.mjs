// Real local workerd + SQLite Durable Object; Telegram transport is a fixture.
import {Miniflare,convertV4MiniflareOptions} from 'miniflare';
import assert from 'node:assert/strict';
const secret='s'.repeat(40);let deliveries=0;
const mf=new Miniflare(convertV4MiniflareOptions({modules:true,scriptPath:new URL('./dist/index.js',import.meta.url).pathname,
 compatibilityDate:'2026-01-01',durableObjects:{OPERATOR:{className:'OperatorStore',useSQLite:true}},
 bindings:{TELEGRAM_WEBHOOK_SECRET:secret,SNAPSHOT_WRITE_TOKEN:secret,TELEGRAM_BOT_TOKEN:'123:'+ 'a'.repeat(30),OPERATOR_USER_IDS:'123',PERMITTED_CHAT_ID:'-456'},
 outboundService:async request=>{assert.equal(new URL(request.url).hostname,'api.telegram.org');deliveries++;return Response.json({ok:true,result:{message_id:7,chat:{id:-456}}});}
}));
try {
 const s={schema_version:1,generated_at:new Date().toISOString(),opportunities:[],confirmed_receipts:0,monitor:{status:'ZERO_TRANSFERS',last_success_at:null,gaps:['TOKEN_ACCOUNT_HISTORY_NOT_ENUMERATED']}};
 let r=await mf.dispatchFetch('https://worker.example/snapshot',{method:'PUT',headers:{Authorization:'Bearer '+secret},body:JSON.stringify(s)});assert.equal(r.status,200);
 const update={update_id:100,message:{date:Math.floor(Date.now()/1000),text:'/status',from:{id:123,is_bot:false},chat:{id:-456}}};
 const replies=await Promise.all(Array.from({length:10},()=>mf.dispatchFetch('https://worker.example/telegram',{method:'POST',headers:{'X-Telegram-Bot-Api-Secret-Token':secret},body:JSON.stringify(update)})));
 assert.ok(replies.every(r=>r.status===200));const outcomes=await Promise.all(replies.map(r=>r.text()));assert.equal(deliveries,1,JSON.stringify(outcomes));
 const health=await (await mf.dispatchFetch('https://worker.example/health',{headers:{Authorization:'Bearer '+secret}})).json();assert.equal(health.last_delivery.confirmed,true);
 console.log('Local workerd/SQLite integration passed: publication, 10 concurrent deliveries, one mocked Telegram response, health confirmation. NOT production delivery.');
} finally {await mf.dispose();}
