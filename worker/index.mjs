import {validate,render} from './schema.mjs';
const response=(status,text)=>new Response(text,{status,headers:{'Content-Type':'text/plain; charset=utf-8','Cache-Control':'no-store'}});
async function secretEqual(a,b) {
  if(typeof a!=='string'||typeof b!=='string'||b.length<32||a.length>256)return false;
  const digest=async v=>new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(v)));
  const [x,y]=await Promise.all([digest(a),digest(b)]);let diff=0;for(let i=0;i<x.length;i++)diff|=x[i]^y[i];return diff===0;
}
async function authorized(request,env) {
  const path=new URL(request.url).pathname;
  if(path==='/telegram'&&request.method==='POST')return secretEqual(request.headers.get('X-Telegram-Bot-Api-Secret-Token'),env.TELEGRAM_WEBHOOK_SECRET);
  if((path==='/snapshot'&&request.method==='PUT')||(path==='/health'&&request.method==='GET'))return typeof env.SNAPSHOT_WRITE_TOKEN==='string'&&env.SNAPSHOT_WRITE_TOKEN.length>=32&&secretEqual(request.headers.get('Authorization'),'Bearer '+env.SNAPSHOT_WRITE_TOKEN);
  return false;
}
async function jsonBody(request,limit) {
  if(!request.body)throw Error('BODY');const reader=request.body.getReader();let size=0;const chunks=[];
  for(;;){const {done,value}=await reader.read();if(done)break;size+=value.length;if(size>limit){await reader.cancel();throw Error('SIZE');}chunks.push(value);}
  const bytes=new Uint8Array(size);let offset=0;for(const c of chunks){bytes.set(c,offset);offset+=c.length;}return JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes));
}
export default {
  async fetch(request,env) {
    if(!await authorized(request,env))return response(403,'DENIED');
    try {return await env.OPERATOR.get(env.OPERATOR.idFromName('production-v1')).fetch(request);}
    catch{return response(503,'STORAGE_UNAVAILABLE; retry later');}
  }
};
export class OperatorStore {
  constructor(ctx,env){this.ctx=ctx;this.env=env;}
  async fetch(request) {
    if(!await authorized(request,this.env))return response(403,'DENIED');
    const path=new URL(request.url).pathname,store=this.ctx.storage,now=Date.now();
    try {
      if(path==='/health') {
        const s=await store.get('snapshot'),delivery=await store.get('last_delivery'),outcome=await store.get('last_outcome');
        return Response.json({schema_version:1,snapshot_at:s?.generated_at||null,stale:!s||now-Date.parse(s.generated_at)>86400000,last_delivery:delivery||null,last_outcome:outcome||null},{headers:{'Cache-Control':'no-store'}});
      }
      if(path==='/snapshot') {
        let s;try{s=await jsonBody(request,524288);}catch{return response(400,'INVALID_SNAPSHOT');}
        if(!validate(s,now))return response(400,'INVALID_SNAPSHOT');
        const outcome=await store.transaction(async tx=>{
          const old=await tx.get('snapshot');
          if(old&&Date.parse(old.generated_at)>Date.parse(s.generated_at))return 'STALE_WRITE';
          if(old&&Date.parse(old.generated_at)===Date.parse(s.generated_at))return JSON.stringify(old)===JSON.stringify(s)?'OK':'CONFLICT';
          await tx.put('snapshot',s);return 'OK';
        });
        return response(outcome==='OK'?200:409,outcome);
      }
      if(!/^\d+(?:\s*,\s*\d+)*$/.test(this.env.OPERATOR_USER_IDS||'')||! /^-?\d+$/.test(this.env.PERMITTED_CHAT_ID||'')||!/^\d+:[A-Za-z0-9_-]{20,}$/.test(this.env.TELEGRAM_BOT_TOKEN||''))return response(503,'OPERATOR_NOT_CONFIGURED');
      let update;try{update=await jsonBody(request,16384);}catch{return response(400,'INVALID_UPDATE');}
      const m=update?.message,users=new Set(this.env.OPERATOR_USER_IDS.split(',').map(x=>x.trim()));
      if(!Number.isSafeInteger(update?.update_id)||update.update_id<0||!m||!users.has(String(m.from?.id))||String(m.chat?.id)!==this.env.PERMITTED_CHAT_ID||m.from?.is_bot!==false||m.sender_chat||m.forward_origin||m.forward_from||m.forward_from_chat||m.forward_date||m.is_automatic_forward||m.via_bot||m.from?.username==='GroupAnonymousBot')return response(200,'IGNORED');
      if(!Number.isSafeInteger(m.date)||now-m.date*1000>86400000||m.date*1000>now+60000)return response(200,'EXPIRED_UPDATE');
      if(typeof m.text!=='string'||m.text.length>200||!m.text.startsWith('/'))return response(200,'IGNORED');
      // Read before claiming: storage read failures are safe for Telegram to retry.
      const snapshot=await store.get('snapshot');
      const text=render(m.text,snapshot,now).slice(0,3500);
      const key='update:'+update.update_id;
      const claim=await store.transaction(async tx=>{
        if(await tx.get(key))return 'DUPLICATE';
        const bucket=Math.floor(now/60000),rate=await tx.get('rate');
        if(rate?.bucket===bucket&&rate.count>=30)return 'RATE_LIMIT';
        await tx.put('rate',{bucket,count:rate?.bucket===bucket?rate.count+1:1});
        await tx.put(key,{at:now,outcome:'CLAIMED'});return 'CLAIMED';
      });
      if(claim==='RATE_LIMIT')return response(429,'RATE_LIMIT');
      if(claim==='DUPLICATE')return response(200,'DUPLICATE');
      // From this point, never retry a send. Telegram provides no sendMessage
      // idempotency key. A timeout/crash can mean delivery succeeded remotely.
      let outcome='DELIVERY_UNCONFIRMED';
      try {
        const result=await fetch(`https://api.telegram.org/bot${this.env.TELEGRAM_BOT_TOKEN}/sendMessage`,{
          method:'POST',redirect:'manual',signal:AbortSignal.timeout(5000),headers:{'Content-Type':'application/json'},
          body:JSON.stringify({chat_id:this.env.PERMITTED_CHAT_ID,text,disable_web_page_preview:true})
        });
        const body=result.status>=300&&result.status<400?{ok:false}:await result.json();
        if(result.ok&&body.ok===true&&Number.isSafeInteger(body.result?.message_id)&&String(body.result?.chat?.id)===this.env.PERMITTED_CHAT_ID)outcome='SENT';
        else outcome=result.status===429?'TELEGRAM_RATE_LIMIT':'TELEGRAM_REJECTED';
      } catch { /* Never log API URLs, tokens, chat identities or request bodies. */ }
      try {
        await store.put(key,{at:now,outcome});
        if(outcome==='SENT')await store.put('last_delivery',{at:new Date(now).toISOString(),command:['/status','/worklist','/opportunity','/rewards','/monitor'].includes(m.text.split(/\s+/)[0])?m.text.split(/\s+/)[0]:'UNSUPPORTED',confirmed:true});
        await store.put('last_outcome',{at:new Date(now).toISOString(),outcome});
        if(!await store.getAlarm())await store.setAlarm(now+3600000);
      } catch {return response(200,'CLAIMED; delivery status persistence unavailable');}
      return response(200,outcome);
    } catch{return response(503,'STORAGE_UNAVAILABLE; retry later');}
  }
  async alarm(){
    const s=this.ctx.storage,start=await s.get('cleanup_cursor');
    const batch=await s.list({prefix:'update:',limit:1000,...(start?{startAfter:start}:{})});
    for(const [key,entry] of batch)if(Date.now()-entry.at>172800000)await s.delete(key);
    if(batch.size===1000)await s.put('cleanup_cursor',[...batch.keys()].at(-1));else await s.delete('cleanup_cursor');
    await s.setAlarm(Date.now()+(batch.size===1000?300000:86400000));
  }
}
