"""Observed source utility, not safety, profitability or payout likelihood."""
from datetime import datetime, timezone
from qualification import qualify


def coverage_report(specs, health, queue, previous_ids, rewards, now=None):
    now = now or datetime.now(timezone.utc)
    by_id = {s.id:s for s in specs}
    qualities, candidates, education = [], {}, {}
    official_available = 0
    for h in health:
        spec = by_id.get(h.source_id)
        kind = spec.source_class if spec else 'MIXED'
        observations = h.observations
        nonedu = []
        for original in observations:
            record = {**original, 'last_observed':now.isoformat()}
            q = qualify(record, now)
            if kind == 'EDUCATION' or q['reward_class'] == 'EDUCATIONAL_BADGE':
                education[record['id']] = record
                continue
            nonedu.append(record)
            if record.get('campaign_type') != 'NEWS_ONLY':
                candidates[record['id']] = queue.get(record['id'], record)
        distinct = {r['id']:r for r in nonedu if r.get('campaign_type') != 'NEWS_ONLY'}
        verified = sum(r.get('evidence_verified') is True for r in distinct.values())
        measured = [qualify(r,now) for r in distinct.values()]
        completeness = sum(sum(f['status']=='PASS' for f in q['fields'].values())/len(q['fields']) for q in measured)/len(measured) if measured else 0
        nominated = [r for r in nonedu if r.get('discovery_keyword_hit')]
        rejected = sum(r.get('campaign_type')=='NEWS_ONLY' for r in nominated)
        false_positive_rate = rejected/len(nominated) if nominated else None
        yield_rate = verified/len(nonedu) if nonedu else 0
        score = round(40*yield_rate + 30*completeness + 20*int(h.reachable) + (10*(1-false_positive_rate) if false_positive_rate is not None else 0))
        if h.reachable and spec and spec.official and kind in ('CAMPAIGN','MIXED'):
            official_available += 1
        qualities.append({'source_id':h.source_id,'source_url':h.url,'source_class':kind,'available':h.reachable,
                          'quality_score':None if kind=='EDUCATION' else min(100,max(0,score)),
                          'noneducational_entries_scanned':len(nonedu),'verified_campaigns':verified,
                          'verified_campaign_yield':yield_rate,'field_completeness':completeness,
                          'keyword_nominations':len(nominated),'false_positive_rate':false_positive_rate,
                          'false_positive_definition':'Keyword-nominated entries rejected as NEWS_ONLY; a measured heuristic, not adjudicated scam prevalence',
                          'scope':'Current scan only; no observations means no yield/completeness credit'})
    gaps = {}
    classes = {}
    for r in candidates.values():
        q = qualify(r,now)
        classes[q['reward_class']] = classes.get(q['reward_class'],0)+1
        if q['status']=='REVIEW_REQUIRED':
            for field in q['missing_evidence']:
                gaps[field] = gaps.get(field,0)+1
    return {'version':'1.4.4','observed_at':now.isoformat(),'official_campaign_sources_available':official_available,
            'newly_discovered_campaigns':sum(k not in previous_ids for k in candidates),
            'noneducational_candidates':len(candidates),
            'verified_candidates':sum(r.get('evidence_verified') is True for r in candidates.values()),
            'actionable_candidates':sum(qualify(r,now)['status']=='ACTIONABLE' for r in candidates.values()),
            'review_required_by_missing_field':gaps,'confirmed_received_rewards':len(rewards.get('actual_rewards_received',[])),
            'educational_only_entries':len(education),'reward_classes':classes,
            'token_reward_candidates':sum(v for k,v in classes.items() if k in ('CONFIRMED_TOKEN_REWARD','CONDITIONAL_TOKEN_REWARD')),
            'source_quality':qualities,'note':'Education excluded from campaign yield and monetary discovery counts. Points, speculation and token program allocations are not income; only attributed receipts count as received rewards. Quality does not change qualification gates.'}
