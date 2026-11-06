"""Human-operated lifecycle. No external action or wallet authorization is executed."""
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from qualification import qualify
from triage import classify, revision

STATES = ('DISCOVERED', 'EVIDENCE_REVIEW', 'ELIGIBLE', 'READY_FOR_HUMAN', 'IN_PROGRESS',
          'SUBMITTED', 'AWAITING_RESULT', 'REWARD_VERIFIED', 'BLOCKED', 'EXPIRED', 'WITHDRAWN', 'CANCELLED')
NEXT = {'EVIDENCE_REVIEW': 'ELIGIBLE', 'ELIGIBLE': 'READY_FOR_HUMAN',
        'READY_FOR_HUMAN': 'IN_PROGRESS', 'IN_PROGRESS': 'SUBMITTED', 'SUBMITTED': 'AWAITING_RESULT'}


def operation_revision(record):
    return hashlib.sha256(json.dumps([revision(record), record.get('operations'), record.get('operation_history', [])], sort_keys=True).encode()).hexdigest()


def migrate(record):
    if not isinstance(record.get('operations'), dict):
        record['operations'] = {'state': 'DISCOVERED', 'version': '1.5'}
    if record['operations'].get('state') not in STATES:
        record['operations']['state'] = 'EVIDENCE_REVIEW'
    if not isinstance(record.get('operation_history'), list):
        record['operation_history'] = []
    return record['operations']


def _event(record, target, reason, actor, now, request_id=None):
    ops = migrate(record)
    event = {'id': request_id or hashlib.sha256(f"{record['id']}:{now}:{target}".encode()).hexdigest(),
             'at': now, 'previous_status': ops['state'], 'new_status': target, 'reason': reason, 'actor': actor}
    record['operation_history'].append(event)
    ops.update(state=target, updated_at=now)
    record['updated_at'] = now
    return event


def sync(record, receipts=(), now=None):
    now = now or datetime.now(timezone.utc)
    ops = migrate(record)
    status = ops['state']
    eligible_receipts = [r for r in receipts if r.get('quest_id') == record['id'] and r.get('signature')
                         and r.get('attribution') == 'CONFIRMED_QUEST_REWARD' and Decimal(r.get('amount', '0')) > 0]
    if status == 'AWAITING_RESULT' and eligible_receipts:
        _event(record, 'REWARD_VERIFIED', 'Finalized attributed receipt: ' + eligible_receipts[0]['signature'], 'reconciler', now.isoformat())
    elif status not in ('SUBMITTED', 'AWAITING_RESULT', 'REWARD_VERIFIED', 'CANCELLED', 'WITHDRAWN'):
        q = qualify(record, now)
        target = 'EXPIRED' if q['status'] == 'EXPIRED' else 'BLOCKED' if q['status'] == 'BLOCKED' else 'EVIDENCE_REVIEW' if q['status'] != 'ACTIONABLE' or status in ('DISCOVERED', 'BLOCKED', 'EXPIRED') else status
        if target != status:
            _event(record, target, 'Current qualification: ' + q['status'], 'qualification', now.isoformat())
    return ops


def transition(record, target, *, authorized, actor, reason, expected_revision, request_id, now=None):
    if authorized is not True or not actor or not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
        raise ValueError('Explicit operator authorization, actor and bounded evidence/reason required')
    if target not in STATES or target == 'REWARD_VERIFIED' or not request_id or len(request_id) > 100:
        raise ValueError('Invalid human transition')
    payload_hash = hashlib.sha256(json.dumps([target, actor, reason, expected_revision], sort_keys=True).encode()).hexdigest()
    for event in record.get('operation_history', []):
        if event.get('id') == request_id:
            if event.get('payload_hash') != payload_hash:
                raise ValueError('Request ID reused')
            return event
    if expected_revision != operation_revision(record):
        raise ValueError('State conflict: inspect a fresh playbook')
    now = now or datetime.now(timezone.utc)
    ops = migrate(record)
    current = ops['state']
    if current in ('REWARD_VERIFIED', 'CANCELLED', 'WITHDRAWN'):
        raise ValueError('Terminal lifecycle; history cannot be reopened')
    if target not in ('CANCELLED', 'WITHDRAWN'):
        if NEXT.get(current) != target:
            raise ValueError('Lifecycle stages cannot be skipped')
        if target != 'AWAITING_RESULT' and qualify(record, now)['status'] != 'ACTIONABLE':
            raise ValueError('All mandatory qualification gates must pass')
        grant = classify(record, qualify(record, now))['reward_type'] == 'grant'
        plan = ops.get('grant_plan', {})
        if grant and (not plan.get('applicant_fit_reviewed') or not plan.get('technical_requirements') or not plan.get('deliverables') or not plan.get('proposal_draft')):
            raise ValueError('Grant fit review and human-authored application plan required')
        if target == 'SUBMITTED':
            if reason.strip().startswith(('http://', 'https://')) and ' ' not in reason.strip():
                raise ValueError('Opening a URL is not submission evidence')
            if not grant and (not record.get('tasks') or any(not t.get('completion') or (t.get('approval_reasons') and not t.get('approval')) for t in record['tasks'].values())):
                raise ValueError('Human-confirmed task completion proofs required')
    event = _event(record, target, reason.strip(), actor, now.isoformat(), request_id)
    event['payload_hash'] = payload_hash
    return event


def playbook(record, now=None):
    q = qualify(record, now)
    axes = classify(record, q)
    ops = record.get('operations') or {'state': 'DISCOVERED'}
    execution = q['status'] == 'ACTIONABLE' and ops.get('state') in ('ELIGIBLE', 'READY_FOR_HUMAN', 'IN_PROGRESS', 'SUBMITTED', 'AWAITING_RESULT')
    from recovery import gap_analysis
    result = {'campaign_id': record['id'], 'name': record.get('name'), 'lifecycle': ops['state'],
              'qualification': q['status'], 'expected_revision': operation_revision(record),
              'kind': 'HUMAN_ACTION_CHECKLIST' if execution else 'EVIDENCE_GATHERING_ONLY',
              'official_url': record.get('official_url') if q['fields']['official_url']['status'] == 'PASS' else None,
              'deadline': record.get('end_at'), 'reward_classification': q['reward_class'], **axes,
              'eligibility_conditions': record.get('eligibility_restrictions', []),
              'wallet_requirements': record.get('wallet_requirements', []),
              'required_human_interactions': sorted({reason for task in record.get('tasks', {}).values() for reason in task.get('approval_reasons', [])}),
              'eligibility_evidence': {k: q['fields'][k] for k in ('geographic_eligibility', 'account_eligibility')},
              'fees': {k: q['fields'][k] for k in ('mandatory_fees', 'gas_costs')},
              'completion_evidence': 'Human confirmation for each task and submission receipt; reward verification requires a finalized attributed wallet transfer.',
              'checklist': [dict(text=t['text'], approval_required=t.get('approval_reasons', []), completed=bool(t.get('completion'))) for t in record.get('tasks', {}).values()] if execution else gap_analysis(record, now)['verification_steps']}
    if not execution and not result['checklist']:
        result['checklist'] = [{'next_action': 'Review qualification and applicant fit; explicitly authorize ELIGIBLE before execution. Blocked or terminal outcomes cannot execute.', 'failed_conditions': q['failed_conditions']}]
    if axes['reward_type'] == 'grant':
        result['grant_track'] = {'budget_is_income': False, 'plan': ops.get('grant_plan', {}),
                                 'planning_steps': ['Review actual applicant fit', 'Verify technical requirements', 'Draft proposal with truthful claims and evidence', 'Plan deliverables and milestones', 'Verify submission deadline', 'Human approval before application; record receipt afterwards'] if execution else ['Verify applicant fit against official conditions', 'Obtain official requirements, selection criteria and deadline', 'Resolve missing evidence before application execution']}
    return result


def save_grant_plan(record, plan, *, authorized, actor, reason, expected_revision, request_id, now=None):
    if authorized is not True or not actor or not reason or not request_id or len(request_id) > 100:
        raise ValueError('Explicit authorized plan review required')
    if set(plan) != {'applicant_fit_reviewed', 'technical_requirements', 'proposal_draft', 'deliverables'} or type(plan['applicant_fit_reviewed']) is not bool or any(not isinstance(plan[k], str) or len(plan[k]) > 8000 for k in ('technical_requirements', 'proposal_draft', 'deliverables')):
        raise ValueError('Invalid grant planning fields; do not include private participant information')
    if classify(record)['reward_type'] != 'grant':
        raise ValueError('Grant track only')
    fingerprint = hashlib.sha256(json.dumps([plan, actor, reason, expected_revision], sort_keys=True).encode()).hexdigest()
    for event in record.get('operation_history', []):
        if event.get('id') == request_id:
            if event.get('payload_hash') != fingerprint:
                raise ValueError('Request ID reused')
            return event
    if expected_revision != operation_revision(record):
        raise ValueError('State conflict')
    ops = migrate(record)
    ops['grant_plan'] = dict(plan)
    at = (now or datetime.now(timezone.utc)).isoformat()
    event = _event(record, ops['state'], reason, actor, at, request_id)
    event.update(kind='grant_plan_review', payload_hash=fingerprint, plan=dict(plan))
    return event
