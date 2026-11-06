"""Validate and merge V1.4 extensions inside the existing dedup artifact."""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation

from quests import STATES

QUEST_FIELDS = {
    'id', 'project', 'name', 'official_url', 'source', 'evidence_verified', 'evidence',
    'source_verification_status', 'start_at', 'end_at', 'tasks', 'reward_type', 'reward_status',
    'reward_guaranteed', 'reward_evidence', 'networks', 'wallet_requirements', 'mandatory_fees',
    'cost_status', 'cost_evidence', 'eligibility_restrictions', 'eligibility_status',
    'eligibility_evidence', 'risk_indicators', 'risk_explanation', 'risk_band', 'campaign_type',
    'announcement_url', 'evidence_checks', 'reasons', 'human_review',
    'evidence_requests', 'evidence_resolution', 'terms_digest', 'terms_review_required', 'terms_events',
    'qualification', 'qualification_schema', 'review_history', 'eligibility_text', 'task_archive', 'gas_status', 'gas_evidence',
    'platform_status', 'status', 'verified_at', 'first_seen', 'updated_at', 'last_observed', 'terms_hash',
}
TASK_FIELDS = {'id', 'text', 'approval_reasons', 'status', 'approval', 'completion'}
EVENT_FIELDS = {'asset', 'amount', 'observed_at', 'evidence', 'attribution', 'signature', 'slot', 'block_time', 'quest_id', 'attribution_evidence'}


def _json(value, depth=0):
    if depth > 8:
        return None
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if abs(value) != float('inf') and value == value else None
    if isinstance(value, list):
        return [_json(v, depth + 1) for v in value[:1000]]
    if isinstance(value, dict):
        return {k: _json(v, depth + 1) for k, v in value.items() if isinstance(k, str)}
    return None


def _proof(value, kind):
    if not isinstance(value, dict) or value.get('kind') != kind or not isinstance(value.get('evidence'), str) or not isinstance(value.get('at'), str):
        return None
    return {key: value[key] for key in ('kind', 'at', 'evidence')}


def clean_queue(value):
    result = {}
    if not isinstance(value, dict):
        return result
    for ident, record in value.items():
        if not isinstance(ident, str) or not isinstance(record, dict) or record.get('id') != ident or record.get('status') not in STATES or not isinstance(record.get('tasks'), dict):
            continue
        clean = {k: _json(v) for k, v in record.items() if k in QUEST_FIELDS}
        clean.setdefault('updated_at', '')
        clean.setdefault('reward_type', 'UNSPECIFIED')
        clean.setdefault('reward_status', 'unspecified')
        if not isinstance(clean.get('evidence_checks'), dict):
            clean['evidence_checks'] = {}
        clean['evidence_verified'] = record.get('evidence_verified') is True
        tasks = {}
        for tid, task in record['tasks'].items():
            if not isinstance(tid, str) or not isinstance(task, dict) or task.get('id') != tid or not isinstance(task.get('text'), str):
                continue
            entry = {k: _json(v) for k, v in task.items() if k in TASK_FIELDS}
            from quests import task_policy
            if not isinstance(entry.get('approval_reasons'), list):
                entry['approval_reasons'] = task_policy(task['text'])
            entry.setdefault('status', 'DISCOVERED')
            entry['approval'] = _proof(task.get('approval'), 'human_approval')
            entry['completion'] = _proof(task.get('completion'), 'human_confirmation')
            tasks[tid] = entry
        review = record.get('human_review')
        proof = _proof(review, 'human_review')
        clean['human_review'] = ({**proof, 'decision': review['decision']}
                                 if proof and review.get('decision') in ('accept', 'reject', 'defer') else None)
        from qualification import migrate_record, history_union
        # History is merged separately, without the generic list truncation.
        if 'review_history' in record:
            clean['review_history'] = history_union(record.get('review_history'))
        if not isinstance(clean.get('task_archive'), dict):
            clean['task_archive'] = {}
        from recovery import union_events
        for key in ('evidence_requests', 'terms_events'):
            clean[key] = union_events(record.get(key))
        clean['evidence_requests'] = [e for e in clean['evidence_requests'] if isinstance(e.get('url'), str) and e.get('status') in ('PENDING','CHECKED','UNSUPPORTED')]
        if not isinstance(clean.get('evidence_resolution'), dict):
            clean['evidence_resolution'] = {}
        resolution = clean['evidence_resolution']
        if not isinstance(resolution.get('failures', 0), int):
            resolution['failures'] = 0
        migrate_record(clean)
        clean['tasks'] = tasks
        result[ident] = clean
    return result


def clean_wallets(value):
    result = {}
    if not isinstance(value, dict):
        return result
    for ident, record in value.items():
        if not isinstance(ident, str) or not isinstance(record, dict):
            continue
        clean = {'assets': {}, 'events': {}, 'signatures': {}, 'token_baseline': record.get('token_baseline') is True, 'updated_at': str(record.get('updated_at', ''))}
        for asset, observation in (record.get('assets') or {}).items() if isinstance(record.get('assets'), dict) else []:
            try:
                if isinstance(asset, str) and isinstance(observation, dict) and Decimal(str(observation.get('amount'))).is_finite():
                    clean['assets'][asset] = {'amount': str(observation['amount']), 'at': str(observation.get('at', ''))}
            except (InvalidOperation, ValueError):
                pass
        for eid, event in (record.get('events') or {}).items() if isinstance(record.get('events'), dict) else []:
            try:
                if isinstance(eid, str) and isinstance(event, dict) and isinstance(event.get('asset'), str) and Decimal(str(event.get('amount'))).is_finite() and isinstance(event.get('attribution'), str):
                    clean['events'][eid] = {k: _json(v) for k, v in event.items() if k in EVENT_FIELDS}
            except (InvalidOperation, ValueError):
                pass
        if isinstance(record.get('signatures'), dict):
            clean['signatures'] = {k: v for k, v in record['signatures'].items() if isinstance(k, str) and isinstance(v, str)}
        result[ident] = clean
    return result


def merge_extensions(base, update):
    queue = clean_queue(base.get('quest_queue'))
    for ident, record in clean_queue(update.get('quest_queue')).items():
        old = queue.get(ident)
        if not old:
            queue[ident] = record
            continue
        winner, other = (record, old) if record['updated_at'] >= old['updated_at'] else (old, record)
        winner = deepcopy(winner)
        from recovery import union_events
        for key in ('evidence_requests', 'terms_events'):
            winner[key] = union_events(winner.get(key), other.get(key))
        resolutions = [r for r in (winner.get('evidence_resolution'), other.get('evidence_resolution')) if isinstance(r, dict) and r]
        winner['evidence_resolution'] = max(resolutions, key=lambda r: str(r.get('updated_at', ''))) if resolutions else {}
        from qualification import history_union
        winner['review_history'] = history_union(winner.get('review_history'), other.get('review_history'))
        winner['task_archive'] = {**(other.get('task_archive') or {}), **(winner.get('task_archive') or {}),
                                  **{k:v for k,v in other['tasks'].items() if k not in winner['tasks']}}
        if winner.get('terms_hash') == other.get('terms_hash'):
            reviews = [r for r in (winner.get('human_review'), other.get('human_review')) if r]
            if reviews:
                winner['human_review'] = max(reviews, key=lambda r: r['at'])
        for tid, task in winner['tasks'].items():
            previous = other['tasks'].get(tid, {})
            for key in ('approval', 'completion'):
                if key == 'approval' and winner.get('terms_hash') != other.get('terms_hash'):
                    continue
                candidates = [v for v in (task.get(key), previous.get(key)) if v]
                if candidates:
                    task[key] = max(candidates, key=lambda v: v['at'])
        queue[ident] = winner
    wallets = clean_wallets(base.get('reward_wallets'))
    for ident, record in clean_wallets(update.get('reward_wallets')).items():
        old = wallets.get(ident)
        if not old:
            wallets[ident] = record
            continue
        for asset, observation in record['assets'].items():
            if observation['at'] >= old['assets'].get(asset, {}).get('at', ''):
                old['assets'][asset] = observation
        for eid, event in record['events'].items():
            previous = old['events'].get(eid)
            if not previous or previous.get('attribution') != 'CONFIRMED_QUEST_REWARD':
                old['events'][eid] = event
        old['signatures'].update(record['signatures'])
        old['token_baseline'] |= record['token_baseline']
        old['updated_at'] = max(old['updated_at'], record['updated_at'])
    return {'quest_queue': queue, 'reward_wallets': wallets}
