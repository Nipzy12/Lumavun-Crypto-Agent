"""Optimistic concurrency and idempotency for untrusted operator URL requests."""
from datetime import datetime, timezone
import hashlib
import json
import re
from recovery import submit_url
from triage import revision


def apply_request(state, request):
    allowed = {'campaign_id', 'url', 'expected_revision', 'request_id', 'reviewer'}
    if set(request) != allowed or any(not isinstance(v, str) or not v or len(v) > 2048 for v in request.values()):
        raise ValueError('Only bounded URL request fields are accepted; no submitted facts')
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}', request['request_id']):
        raise ValueError('Invalid request ID')
    if not re.fullmatch(r'[a-f0-9]{64}', request['expected_revision']):
        raise ValueError('Invalid revision')
    record = state['quest_queue'][request['campaign_id']]
    digest = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
    history = record.get('operator_history', [])
    for event in history:
        if event['id'] == request['request_id']:
            if event['payload_hash'] != digest:
                raise ValueError('Request ID already used with different payload')
            return event
    at = datetime.now(timezone.utc).isoformat()
    outcome = 'CONFLICT'
    if request['expected_revision'] == revision(record):
        # Even a plausible official URL is only queued for the existing independent
        # authorized-source resolver. It cannot set any qualification field.
        submit_url(record, request['url'])
        outcome = 'PENDING_REVALIDATION'
    event = {'id': request['request_id'], 'payload_hash': digest, 'at': at,
             'reviewer': request['reviewer'], 'url': request['url'], 'status': outcome,
             'expected_revision': request['expected_revision'],
             'reason': 'Official source must independently substantiate facts' if outcome != 'CONFLICT' else 'State changed; obtain the latest worklist revision'}
    record['operator_history'] = history + [event]
    return event
