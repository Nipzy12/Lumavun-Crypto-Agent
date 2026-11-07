const qualification = ['ACTIONABLE','REVIEW_REQUIRED','BLOCKED','EXPIRED'];
const lifecycle = ['DISCOVERED','EVIDENCE_REVIEW','ELIGIBLE','READY_FOR_HUMAN','IN_PROGRESS','SUBMITTED','AWAITING_RESULT','REWARD_VERIFIED','BLOCKED','EXPIRED','WITHDRAWN','CANCELLED'];
const rewards = ['CONFIRMED_TOKEN_REWARD','CONDITIONAL_TOKEN_REWARD','POINTS_ONLY','EDUCATIONAL_BADGE','SPECULATIVE_AIRDROP','UNKNOWN_REWARD'];
const fields = ['identity','official_url','tasks','reward_type','reward_certainty','mandatory_fees','gas_costs','geographic_eligibility','account_eligibility','start_date','deadline','availability','human_actions','source_reliability','freshness','risk'];
const gaps = ['PAGINATION_PENDING','REQUEST_BUDGET_REACHED','CURSOR_NOT_FOUND','UNRESOLVED_HISTORY_OR_TRANSACTION','TOKEN_ACCOUNT_HISTORY_NOT_ENUMERATED','MALFORMED_PENDING_HISTORY'];
const exact = (o, keys) => o && typeof o === 'object' && !Array.isArray(o) && Object.keys(o).sort().join() === [...keys].sort().join();
const date = v => typeof v === 'string' && v.length <= 40 && /^\d{4}-\d\d-\d\dT.*(?:Z|\+00:00)$/.test(v) && Number.isFinite(Date.parse(v));
export function validate(s, now=Date.now()) {
  if (!exact(s,['schema_version','generated_at','opportunities','confirmed_receipts','monitor']) || s.schema_version !== 1 || !date(s.generated_at) || Date.parse(s.generated_at)>now+300000) return false;
  if (!Number.isSafeInteger(s.confirmed_receipts) || s.confirmed_receipts<0 || !Array.isArray(s.opportunities) || s.opportunities.length>1000) return false;
  const ids=new Set();
  for(const r of s.opportunities) {
    if(!exact(r,['id','qualification','lifecycle','official_url','reward_class','missing','deadline']) || typeof r.id!=='string' || !/^[a-f0-9]{20,64}$/.test(r.id) || ids.has(r.id)) return false;
    ids.add(r.id);
    if(!qualification.includes(r.qualification)||!lifecycle.includes(r.lifecycle)||!rewards.includes(r.reward_class)||!Array.isArray(r.missing)||r.missing.length>16||r.missing.some(x=>!fields.includes(x))||!(r.deadline===null||date(r.deadline))) return false;
    if(r.official_url!==null) {
      try {const u=new URL(r.official_url); if(typeof r.official_url!=='string'||r.official_url.length>2048||u.protocol!=='https:'||u.username||u.password||u.search||u.hash||u.port||!u.hostname.includes('.')||/^[\d.:\[\]]+$/.test(u.hostname)||/(?:\.local|\.internal|\.localhost)$/.test(u.hostname))return false;} catch{return false;}
    }
  }
  const m=s.monitor;
  return exact(m,['status','last_success_at','gaps'])&&['OK','ZERO_TRANSFERS','RPC_UNAVAILABLE','PARTIAL','BACKOFF','INVALID_STATE','UNKNOWN'].includes(m.status)&&(m.last_success_at===null||date(m.last_success_at))&&Array.isArray(m.gaps)&&m.gaps.length<=6&&m.gaps.every(x=>gaps.includes(x));
}
export function render(text,s,now=Date.now()) {
  if(!s||!validate(s,now)) return 'Production snapshot unavailable. No current qualification can be confirmed.';
  if(now-Date.parse(s.generated_at)>86400000) return `STALE snapshot (${s.generated_at}). No current eligibility can be confirmed; wait for a successful discovery scan.`;
  const [command,id,...extra]=text.trim().split(/\s+/);
  const prefix=`Snapshot: ${s.generated_at}\n`;
  if(extra.length||(id&&command!=='/opportunity'))return 'Read-only command syntax invalid.';
  if(command==='/status') return prefix+['ACTIONABLE','REVIEW_REQUIRED','BLOCKED','EXPIRED'].map(k=>`${k}: ${s.opportunities.filter(r=>r.qualification===k).length}`).join('\n');
  if(command==='/worklist')return prefix+s.opportunities.filter(r=>['ACTIONABLE','REVIEW_REQUIRED'].includes(r.qualification)).slice(0,5).map(r=>`${r.id} | ${r.qualification} | ${r.lifecycle}\n${r.official_url||'Official URL unverified'}\nMissing: ${r.missing.join(', ')||'none; explicit human authorization still required'}`).join('\n');
  if(command==='/opportunity') {const r=s.opportunities.find(r=>r.id===id);return r?prefix+`${r.id}\n${r.qualification} | ${r.lifecycle}\n${r.official_url||'Official URL unverified'}\nReward: ${r.reward_class} (not income)\nDeadline: ${r.deadline||'UNKNOWN'}\nMissing: ${r.missing.join(', ')||'none; human authorization still required'}`:'Use /opportunity ID from /worklist.';}
  if(command==='/rewards')return prefix+`Confirmed attributed receipts: ${s.confirmed_receipts}. Points, badges, grant budgets and unverified incoming assets are not income.`;
  if(command==='/monitor')return prefix+`${s.monitor.status}\nLast success: ${s.monitor.last_success_at||'never'}\nCoverage gaps: ${s.monitor.gaps.join(', ')||'See address-history scope; not a full wallet audit.'}`;
  return 'Read-only commands: /status /worklist /opportunity ID /rewards /monitor. Reviews and approvals require the authenticated operator workflow.';
}
