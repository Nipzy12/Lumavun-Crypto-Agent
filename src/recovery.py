"""Evidence gap analysis and bounded revalidation through approved public transports."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import uuid

from qualification import qualify, safe_url
from sources import sanitize_url, fetch_source

PARTICIPANT = {'geographic_eligibility', 'account_eligibility'}
TERM_FIELDS = ('official_url', 'project', 'name', 'start_at', 'end_at', 'tasks', 'reward_type',
               'reward_evidence', 'reward_guaranteed', 'cost_status', 'gas_status', 'eligibility_text',
               'eligibility_status', 'platform_status', 'risk_indicators')


def terms_data(record):
    data = {key: record.get(key) for key in TERM_FIELDS}
    data['tasks'] = sorted(t.get('text', '') for t in (record.get('tasks') or {}).values())
    return data


def terms(record):
    return hashlib.sha256(json.dumps(terms_data(record), sort_keys=True).encode()).hexdigest()


def union_events(*values):
    events = {}
    for value in values:
        for event in value if isinstance(value, list) else []:
            if isinstance(event, dict) and isinstance(event.get('id'), str):
                previous = events.get(event['id'])
                if previous is None or str(event.get('updated_at', event.get('at', ''))) > str(previous.get('updated_at', previous.get('at', ''))):
                    events[event['id']] = deepcopy(event)
    return sorted(events.values(), key=lambda e: (str(e.get('at', '')), e['id']))


def gap_analysis(record, now=None):
    q = qualify(record, now)
    missing = q['missing_evidence']
    import re
    text = str(record.get('eligibility_text') or '')
    patterns = {'geographic_eligibility': r'\b(?:residents?|citizens?|country|countries|jurisdiction|US only|EU only|region)\b',
                'account_eligibility': r'\b(?:KYC|verified account|whitelist|allowlist|wallet must|wallet required|account required|age)\b'}
    participant = [f for f in missing if f in PARTICIPANT and
                   (re.search(patterns[f], text, re.I) or 'Authorized credentialGroups' in str(record.get('eligibility_evidence')))]
    public = [f for f in missing if f not in participant]
    steps = []
    for field in missing:
        steps.append({'field': field, 'scope': 'PARTICIPANT_SPECIFIC' if field in participant else 'PUBLIC_EVIDENCE',
                      'next_action': ('Check published restrictions and the actual participant through an authorized read-only eligibility API; do not submit personal information here'
                                      if field in participant else 'Obtain an explicit current official statement for ' + field + '; submit its URL for independent revalidation'),
                      'explanation': q['fields'][field]['explanation']})
    additional = []
    if record.get('terms_review_required'):
        additional.append('Review changed official terms')
    if (record.get('human_review') or {}).get('decision') == 'defer':
        additional.append('Resolve deferred human review')
    return {'candidate_id': record['id'], 'official_url': record.get('official_url'),
            'qualification_status': q['status'], 'missing_fields': missing, 'public_fields': public,
            'participant_specific_fields': participant, 'minimum_missing_set': missing,
            'minimum_set_explanation': 'Every mandatory gate is conjunctive: all listed UNKNOWN fields must be resolved; FAIL conditions and human decisions also remain binding',
            'failed_conditions': q['failed_conditions'], 'additional_actions': additional,
            'public_evidence_resolved': all(v['status'] == 'PASS' for k,v in q['fields'].items() if k not in participant),
            'freshness': q['fields']['freshness'], 'verification_steps': steps}


def submit_url(record, url, now=None):
    now = now or datetime.now(timezone.utc)
    if not safe_url(url) or len(url) > 2048:
        raise ValueError('A valid HTTPS official evidence URL is required')
    # Submission is a request, never source evidence or consent.
    for event in record.get('evidence_requests', []):
        if event.get('url') == url and event.get('status') == 'PENDING':
            return event
    event = {'id': uuid.uuid4().hex, 'url': url, 'at': now.isoformat(), 'updated_at': now.isoformat(), 'status': 'PENDING'}
    record['evidence_requests'] = union_events(record.get('evidence_requests'), [event])
    record['updated_at'] = now.isoformat()
    return event


def revalidate(state, specs, config, observations=None, now=None, fetch=fetch_source, prior_qualifications=None, candidate_ids=None):
    """No arbitrary page GETs. Re-read supported official feeds/APIs, not HTML.

    Current scan observations are independently fetched evidence. The 6h cache
    only skips extra requests; it never refreshes a previous observation time.
    """
    from quests import parse_time, update_queue, refresh_status
    from discovery import _build_campaign
    now = now or datetime.now(timezone.utc)
    official = {s.url: s for s in specs if s.enabled and s.official and s.parser in ('feed', 'bankless_lessons', 'wp_rest_posts')}
    observed = {q['id']: q for q in observations or []}
    queue = state.setdefault('quest_queue', {})
    fetched = {}
    outcomes = []
    policy = config.get('source_policy', {})
    for ident in list(queue):
        if candidate_ids is not None and ident not in candidate_ids:
            continue
        record = queue[ident]
        pending = [e for e in record.get('evidence_requests', []) if e.get('status') == 'PENDING']
        if qualify(record, now)['status'] != 'REVIEW_REQUIRED' and not pending:
            continue
        source = record.get('source')
        allowed_urls = {sanitize_url(u) for u in (record.get('official_url'), record.get('announcement_url'), source) if safe_url(u)}
        supported = source in official
        accepted = []
        for request in pending:
            if not supported or sanitize_url(request['url']) not in allowed_urls:
                request.update(status='UNSUPPORTED', updated_at=now.isoformat(), reason='No approved adapter binds this submitted URL to this campaign; no request made')
            else:
                accepted.append(request)
        prior = record.get('evidence_resolution') or {}
        retry = parse_time(prior.get('retry_at'))
        cached = parse_time(prior.get('checked_at'))
        candidate = observed.get(ident)
        status, detail = 'UNSUPPORTED', 'No reviewed official feed/API binding; arbitrary page fetching is disabled'
        if supported:
            if candidate is not None:
                status, detail = 'REVALIDATED', 'Independently fetched current scan observation'
            elif retry and retry > now:
                status, detail = 'BACKOFF', 'Retry deferred until ' + retry.isoformat()
            elif cached and 0 <= (now-cached).total_seconds() < 21600:
                status, detail = 'CACHED', 'Recent revalidation reused without refreshing evidence timestamps'
            else:
                if source not in fetched:
                    import requests
                    class NoRedirectSession:
                        def get(self, url, **kwargs):
                            return requests.get(url, allow_redirects=False, **kwargs)
                    result = fetch(official[source], session=NoRedirectSession(), timeout=int(policy.get('request_timeout_seconds', 20)),
                                   min_interval=float(policy.get('min_interval_seconds', 1.5)), respect_robots=True,
                                   user_agent=str(policy.get('user_agent', 'LUMAVUN-Crypto-Agent/1.4.3')))
                    mapped = {}
                    if result.reachable:
                        for item in result.items:
                            c = _build_campaign(item, official[source], config)
                            if c:
                                mapped[c.campaign_id] = c.quest_intelligence
                    fetched[source] = result, mapped
                result, mapped = fetched[source]
                candidate = mapped.get(ident)
                if not result.reachable:
                    status, detail = 'SOURCE_UNREACHABLE', result.error or 'Unavailable'
                elif candidate is None:
                    status, detail = 'NOT_IN_SOURCE', 'Absent from this feed/API window; absence does not prove withdrawal'
                else:
                    status, detail = 'REVALIDATED', 'Official feed/API matched the existing canonical campaign ID'
        resolution = dict(prior)
        resolution.update(status=status, detail=detail, updated_at=now.isoformat(), source_url=source)
        if status == 'REVALIDATED':
            before = (prior_qualifications or {}).get(ident) or qualify(record, now)
            fresh = deepcopy(candidate)
            fresh['evidence_requests'] = union_events(record.get('evidence_requests'))
            # Observations supplied by the current scan have already updated the queue.
            if ident not in observed:
                update_queue(state, [fresh], now)
            record = queue[ident]
            resolution.update(checked_at=now.isoformat(), observation_url=record.get('announcement_url'), failures=0, retry_at=None,
                              resolved_fields=[k for k,v in qualify(record, now)['fields'].items() if v['status'] == 'PASS' and before['fields'][k]['status'] != 'PASS'])
            for request in record.get('evidence_requests', []):
                if request['id'] in {e['id'] for e in accepted}:
                    request.update(status='CHECKED', updated_at=now.isoformat(), reason='Supported official transport checked independently; qualification may still be UNKNOWN')
        elif status in ('SOURCE_UNREACHABLE', 'NOT_IN_SOURCE'):
            failures = min(int(prior.get('failures', 0)) + 1, 8)
            delay = min(21600, 300 * 2 ** (failures-1))
            import re
            match = re.search(r'retry-after (\d+)s', detail, re.I)
            if match:
                delay = max(delay, int(match.group(1)))
            resolution.update(failures=failures, retry_at=(now+timedelta(seconds=delay)).isoformat())
        record['evidence_resolution'] = resolution
        record['updated_at'] = now.isoformat()
        refresh_status(record, now)
        outcomes.append({'candidate_id': ident, **resolution})
    return outcomes


def recovery_report(queue, rewards, now=None):
    now = now or datetime.now(timezone.utc)
    rows = [gap_analysis(q, now) for q in queue.values()]
    review = [r for r in rows if r['qualification_status'] == 'REVIEW_REQUIRED']
    groups = {}
    for row in review:
        for field in row['missing_fields']:
            groups.setdefault(field, []).append(row['candidate_id'])
    from collections import Counter
    classes = Counter(qualify(q, now)['reward_class'] for q in queue.values())
    return {'version':'1.4.3', 'generated_at':now.isoformat(), 'scope':'All persisted candidates, including historical candidates; not only the latest feed window',
            'total_candidates':len(rows), 'qualification_counts':{s:sum(r['qualification_status']==s for r in rows) for s in ('ACTIONABLE','REVIEW_REQUIRED','BLOCKED','EXPIRED')},
            'review_required_by_missing_field':{k:{'count':len(v), 'candidate_ids':v} for k,v in sorted(groups.items())},
            'all_public_evidence_resolved':sum(r['public_evidence_resolved'] for r in rows),
            'awaiting_participant_verification':sum(bool(r['participant_specific_fields']) for r in review),
            'only_participant_verification_remaining':sum(r['public_evidence_resolved'] and bool(r['participant_specific_fields']) and not r['additional_actions'] for r in review),
            'reward_classes':dict(classes), 'confirmed_received_rewards':len(rewards.get('actual_rewards_received', [])),
            'income_note':'Badges, points, speculative allocations and promised rewards are not received income', 'candidates':rows,
            'resolution_outcomes':[{'candidate_id':q['id'], **q['evidence_resolution']} for q in queue.values() if isinstance(q.get('evidence_resolution'),dict)]}
