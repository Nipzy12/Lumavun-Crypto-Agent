"""Independent evidence gates. Scores describe evidence, never profit or consent."""
from datetime import datetime, timezone
from urllib.parse import urlsplit
import hashlib
import json
import re

REWARD_CLASSES = {'CONFIRMED_TOKEN_REWARD', 'CONDITIONAL_TOKEN_REWARD', 'POINTS_ONLY',
                  'EDUCATIONAL_BADGE', 'SPECULATIVE_AIRDROP', 'UNKNOWN_REWARD'}
WEIGHTS = {'identity': 10, 'official_url': 10, 'tasks': 10, 'reward_type': 8,
           'reward_certainty': 7, 'mandatory_fees': 10, 'gas_costs': 5,
           'geographic_eligibility': 5, 'account_eligibility': 5,
           'start_date': 3, 'deadline': 4, 'availability': 3,
           'human_actions': 5, 'source_reliability': 5, 'freshness': 5, 'risk': 5}


def safe_url(value):
    try:
        u = urlsplit(value if isinstance(value, str) else '')
        return bool(u.scheme == 'https' and u.hostname and '.' in u.hostname and not u.username and not u.password and u.port in (None, 443))
    except ValueError:
        return False


def reward_class(record):
    kind = record.get('reward_type')
    text = str(record.get('reward_evidence') or '')
    checks = record.get('evidence_checks') or {}
    if not text or checks.get('reward') is False:
        return 'UNKNOWN_REWARD'
    if kind == 'EDUCATIONAL_BADGE':
        return 'EDUCATIONAL_BADGE'
    if kind == 'OFFCHAIN_POINTS' or record.get('campaign_type') == 'POINTS_PROGRAM':
        return 'POINTS_ONLY'
    if re.search(r'\b(?:rumou?red|speculat\w*|potential airdrop|may launch|may receive|could receive)\b', text, re.I) or record.get('campaign_type') == 'TESTNET_POTENTIAL':
        return 'SPECULATIVE_AIRDROP'
    if kind == 'TOKEN' and text and record.get('evidence_verified') is True:
        return 'CONFIRMED_TOKEN_REWARD' if record.get('reward_guaranteed') is True else 'CONDITIONAL_TOKEN_REWARD'
    return 'UNKNOWN_REWARD'


def qualify(record, now=None):
    from quests import parse_time
    now = now or datetime.now(timezone.utc)
    observed = parse_time(record.get('last_observed'))
    stamp = observed.isoformat() if observed else None
    url = record.get('official_url', '')
    source = record.get('source', '')
    trusted = record.get('source_verification_status')
    valid = safe_url(url) and safe_url(source)
    from sources import sanitize_url
    announcement = record.get('announcement_url')
    bound = safe_url(announcement) and sanitize_url(announcement) == sanitize_url(url)
    same_host = valid and bound and urlsplit(url).hostname == urlsplit(source).hostname
    galxe = valid and trusted == 'authorized_galxe_api' and urlsplit(url).hostname == 'app.galxe.com' and urlsplit(source).hostname == 'graphigo-business.prd.galaxy.eco'
    official = bool(valid and (trusted == 'official_feed' and same_host or galxe))
    fields = {}
    def field(name, status, explanation, evidence_url=None):
        fields[name] = {'status': status, 'evidence_url': evidence_url if safe_url(evidence_url) else (url if official else source if safe_url(source) else None),
                        'explanation': explanation, 'last_verified_at': stamp}
    field('official_url', 'PASS' if official else 'FAIL' if url else 'UNKNOWN', 'Official source host/authorized platform binding verified' if official else 'Missing or mismatched official campaign URL; a URL label alone is not evidence')
    field('identity', 'PASS' if official and record.get('project') and record.get('name') and record.get('evidence_verified') else 'UNKNOWN', 'Organizer and campaign must be attributable to the verified announcement')
    tasks = record.get('tasks') if isinstance(record.get('tasks'), dict) else {}
    field('tasks', 'PASS' if official and tasks else 'UNKNOWN', 'Published task checklist' if tasks else 'Missing official participation requirements')
    reward = reward_class(record)
    field('reward_type', 'PASS' if official and reward not in ('UNKNOWN_REWARD', 'SPECULATIVE_AIRDROP') else 'UNKNOWN', reward + ': ' + str(record.get('reward_evidence') or 'Missing explicit reward evidence'))
    # Conditional terms can be transparent without a guaranteed payout.
    certainty = record.get('reward_guaranteed')
    transparent = reward not in ('UNKNOWN_REWARD', 'SPECULATIVE_AIRDROP') and isinstance(certainty, bool)
    field('reward_certainty', 'PASS' if official and transparent else 'UNKNOWN', 'Guaranteed program terms' if certainty is True else 'Conditional; payout not guaranteed' if certainty is False else 'Reward conditions or certainty not established')
    cost = record.get('cost_status')
    field('mandatory_fees', 'FAIL' if cost == 'FAIL' else 'PASS' if official and cost == 'PASS' and record.get('cost_evidence') else 'UNKNOWN', str(record.get('cost_evidence') or 'Mandatory participation and claim fees are unknown'))
    gas = record.get('gas_status')
    if gas in ('PASS', 'FAIL') and not record.get('gas_evidence'):
        gas = 'UNKNOWN'
    if gas not in ('PASS', 'FAIL', 'UNKNOWN'):
        gas = 'PASS' if cost == 'PASS' and record.get('cost_evidence') else 'UNKNOWN'
    field('gas_costs', gas if gas == 'FAIL' or official else 'UNKNOWN', str(record.get('gas_evidence') or record.get('cost_evidence') if gas == 'PASS' else record.get('gas_evidence') or 'Gas payment requirements are not established'))
    eligibility = record.get('eligibility_status')
    restriction_text = str(record.get('eligibility_text') or '')
    for name in ('geographic_eligibility', 'account_eligibility'):
        restricted = bool(re.search(r'\b(?:residents?|citizens?|countries|geographic|jurisdiction|US only|EU only|country|region)\b' if name == 'geographic_eligibility' else r'\b(?:KYC|age|whitelist|allowlist|verified account|account required|wallet must)\b', restriction_text, re.I))
        checked = record.get('source_verification_status') == 'authorized_galxe_api' and 'Authorized credentialGroups' in str(record.get('eligibility_evidence'))
        field(name, 'FAIL' if eligibility == 'FAIL' else 'PASS' if official and eligibility == 'PASS' and record.get('eligibility_evidence') and (not restricted or checked) else 'UNKNOWN', ('Published restrictions require a participant-specific check: ' + restriction_text[:300]) if restricted and not checked else str(record.get('eligibility_evidence') or 'Restrictions and participant eligibility have not been established'))
    start = parse_time(record.get('start_at'))
    end = parse_time(record.get('end_at'), end=True)
    platform = record.get('platform_status')
    field('start_date', 'PASS' if official and start and start <= now else 'FAIL' if start and start > now else 'UNKNOWN', 'Verified start: ' + str(record.get('start_at') or 'not supplied'))
    field('deadline', 'FAIL' if end and end < now else 'PASS' if official and end else 'UNKNOWN', 'Verified deadline: ' + str(record.get('end_at') or 'not supplied; open-ended not assumed'))
    available = official and (platform == 'Active' or start and end and start <= now <= end)
    field('availability', 'FAIL' if platform in ('Expired', 'Deleted', 'Draft', 'CapReached', 'NotStarted') or end and end < now else 'PASS' if available else 'UNKNOWN', 'Platform status: ' + str(platform or 'no explicit status; checked date window'))
    human = sorted({r for task in tasks.values() for r in task.get('approval_reasons', [])})
    field('human_actions', 'PASS' if tasks and 'unclassified_action' not in human else 'UNKNOWN', 'Required human actions: ' + ', '.join(human) if human else 'Only documented read-only tasks; no external execution permission')
    field('source_reliability', 'PASS' if official else 'UNKNOWN', 'Verified official transport/provenance; not an assurance of project safety', source)
    field('freshness', 'PASS' if observed and 0 <= (now-observed).total_seconds() <= 86400 else 'UNKNOWN', 'Evidence must have been observed within 24 hours; future timestamps are invalid', source)
    field('risk', 'FAIL' if record.get('risk_band') == 'high' else 'PASS' if record.get('risk_band') in ('low', 'medium') else 'UNKNOWN', str(record.get('risk_explanation') or 'Risk assessment unavailable'))
    failed = [k for k,v in fields.items() if v['status'] == 'FAIL']
    missing = [k for k,v in fields.items() if v['status'] == 'UNKNOWN']
    review = record.get('human_review') or {}
    status = ('EXPIRED' if end and end < now or platform == 'Expired' else
              'BLOCKED' if failed or review.get('decision') == 'reject' or record.get('campaign_type') == 'NEWS_ONLY' else
              'REVIEW_REQUIRED' if missing or review.get('decision') == 'defer' or not record.get('evidence_verified') else 'ACTIONABLE')
    score = sum(WEIGHTS[k] for k,v in fields.items() if v['status'] == 'PASS')
    if record.get('risk_band') == 'medium':
        score = max(0, score - 5)
    return {'version': '1.4.2', 'status': status, 'score': score, 'reward_class': reward,
            'fields': fields, 'missing_evidence': missing, 'failed_conditions': failed,
            'evaluated_at': now.isoformat(), 'score_note': 'Evidence ranking only; gates apply independently. No earnings guarantee.'}


def history_union(*histories):
    result = {}
    for history in histories:
        for item in history if isinstance(history, list) else []:
            if not isinstance(item, dict) or item.get('decision') not in ('accept', 'reject', 'defer') or not isinstance(item.get('at'), str):
                continue
            item = dict(item)
            ident = item.get('id') or hashlib.sha256(json.dumps(item, sort_keys=True).encode()).hexdigest()
            item['id'] = ident
            result.setdefault(ident, item)
    return sorted(result.values(), key=lambda item: (item['at'], item['id']))


def migrate_record(record):
    """Additive and idempotent; never stamps old evidence as freshly checked."""
    history = history_union(record.get('review_history'))
    review = record.get('human_review')
    if 'review_history' not in record and isinstance(review, dict) and review.get('decision') in ('accept', 'reject', 'defer'):
        history = history_union([{**review, 'reason': 'Migrated V1.4.1 review; prior transition was not recorded',
                                  'previous_status': None, 'new_status': None}])
    record['review_history'] = history
    record['qualification_schema'] = '1.4.2'
    if not isinstance(record.get('qualification'), dict):
        record['qualification'] = {}
    return record


def shortlist(queue):
    groups = {k: [] for k in ('ACTIONABLE', 'REVIEW_REQUIRED', 'BLOCKED', 'EXPIRED', 'EDUCATIONAL')}
    for record in sorted(queue.values(), key=lambda q: (-q.get('qualification', {}).get('score', 0), q['id'])):
        q = record.get('qualification', {})
        group = 'EDUCATIONAL' if q.get('reward_class') == 'EDUCATIONAL_BADGE' else q.get('status', 'REVIEW_REQUIRED')
        groups[group].append({'id': record['id'], 'name': record.get('name', ''),
                              'official_url': record.get('official_url') if q.get('fields', {}).get('official_url', {}).get('status') == 'PASS' else None,
                              'status': q.get('status'), 'score': q.get('score', 0), 'reward_class': q.get('reward_class'),
                              'missing': q.get('missing_evidence', []), 'failed': q.get('failed_conditions', [])})
    return groups
