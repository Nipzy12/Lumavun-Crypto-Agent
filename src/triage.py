"""Conservative review prioritization; never changes qualification or earnings."""
import hashlib
import json
import re
from qualification import qualify
from recovery import gap_analysis, terms


def revision(record):
    keys = ('tasks', 'human_review', 'review_history', 'evidence_requests', 'terms_review_required',
            'evidence_verified', 'source_verification_status', 'cost_evidence', 'gas_evidence', 'eligibility_evidence')
    payload = {key: record.get(key) for key in keys}
    payload['terms'] = terms(record)
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def classify(record, qualification=None):
    q = qualification or qualify(record)
    fields = q['fields']
    costs = [fields[k]['status'] for k in ('mandatory_fees', 'gas_costs')]
    cost = 'PAYMENT_REQUIRED' if 'FAIL' in costs else 'FREE_VERIFIED' if costs == ['PASS', 'PASS'] and fields['official_url']['status'] == 'PASS' else 'COST_UNKNOWN'
    text = ' '.join(str(record.get(k, '')) for k in ('name', 'reward_evidence', 'provenance', 'campaign_type')).lower()
    grant = bool(re.search(r'\b(grant|grants|foundation mission|total allocation|program budget)\b', text))
    reward_class = q['reward_class']
    reward = 'grant' if grant else 'nonmonetary' if reward_class in ('POINTS_ONLY', 'EDUCATIONAL_BADGE') else 'token' if reward_class in ('CONFIRMED_TOKEN_REWARD', 'CONDITIONAL_TOKEN_REWARD') else 'monetary' if record.get('reward_type') in ('CASH', 'FIAT') and record.get('reward_evidence') and record.get('evidence_verified') else 'unknown'
    community_grant = grant and bool(re.search(r'\b(community|contributions|governance|moderation|documentation|bounty board)\b', text))
    developer_grant = grant and bool(re.search(r'\b(developer|technical|tooling|integration|infrastructure)\b', text))
    model = 'community contribution' if community_grant else 'developer grant' if developer_grant else 'unknown' if grant else 'education' if reward_class == 'EDUCATIONAL_BADGE' else 'community contribution' if re.search(r'\bcommunity contribution\b', text) else 'individual quest' if fields['tasks']['status'] == 'PASS' and record.get('evidence_verified') else 'unknown'
    return {'participation_cost': cost, 'reward_type': reward, 'participation_model': model}


def worklist(queue, now=None):
    rows = []
    for cid, record in queue.items():
        q = qualify(record, now)
        if q['status'] != 'REVIEW_REQUIRED':
            continue
        axes = classify(record, q)
        gaps = gap_analysis(record, now)
        checks = {
            'official_source': q['fields']['official_url']['status'] == 'PASS',
            'participation_path': q['fields']['tasks']['status'] == 'PASS',
            'verified_free': axes['participation_cost'] == 'FREE_VERIFIED',
            'explicit_reward': q['fields']['reward_type']['status'] == 'PASS',
            'no_known_disqualification': not q['failed_conditions'],
            'simple_quest': axes['participation_model'] == 'individual quest' and axes['reward_type'] in ('token', 'monetary'),
        }
        steps = gaps['verification_steps']
        deferred = 'Program grant: verify applicant fit and allocation terms; budget is not an individual reward.' if axes['reward_type'] == 'grant' else 'Educational or off-chain reward; excluded from simple-quest earnings.' if axes['reward_type'] == 'nonmonetary' else ''
        rows.append({'campaign_id': cid, 'name': record.get('name', ''), 'official_url': record.get('official_url', '') if checks['official_source'] else '',
                     'qualification_status': 'REVIEW_REQUIRED', **axes, 'expected_revision': revision(record),
                     'missing_evidence': q['missing_evidence'], 'failed_conditions': q['failed_conditions'],
                     'evidence': q['fields'], 'verification_steps': steps,
                     'proposed_manual_verification_step': steps[0] if steps else 'Review changed terms or existing human deferral; never override unknown facts.',
                     'deferral_reason': deferred, 'priority': sum(checks.values()), 'priority_rationale': checks})
    rows.sort(key=lambda r: (-r['priority'], r['campaign_id']))
    return {'version': '1.4.5', 'review_required_count': len(rows), 'candidates': rows,
            'note': 'Priority is review effort guidance, never actionable status or expected income.'}
