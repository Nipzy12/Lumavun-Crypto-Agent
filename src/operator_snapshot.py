"""Public projection only: never serialize state, notes, wallet identities or amounts."""
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit
from qualification import WEIGHTS
from operations import STATES
from source_security import validate_public_url

QUALIFICATIONS = {'ACTIONABLE', 'REVIEW_REQUIRED', 'BLOCKED', 'EXPIRED'}
REWARDS = {'CONFIRMED_TOKEN_REWARD','CONDITIONAL_TOKEN_REWARD','POINTS_ONLY','EDUCATIONAL_BADGE','SPECULATIVE_AIRDROP','UNKNOWN_REWARD'}
MONITORS = {'OK','ZERO_TRANSFERS','RPC_UNAVAILABLE','PARTIAL','BACKOFF','INVALID_STATE','UNKNOWN'}
GAPS = {'PAGINATION_PENDING','REQUEST_BUDGET_REACHED','CURSOR_NOT_FOUND','UNRESOLVED_HISTORY_OR_TRANSACTION','TOKEN_ACCOUNT_HISTORY_NOT_ENUMERATED','MALFORMED_PENDING_HISTORY'}


def timestamp(value):
    try:
        d = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return d.astimezone(timezone.utc).isoformat() if d.tzinfo else None
    except (ValueError, TypeError, AttributeError):
        return None


def export_snapshot(report):
    generated = timestamp(report.get('scan_time_utc'))
    if not generated:
        raise ValueError('Valid production observation timestamp required')
    rows = []
    for cid,q in sorted(report.get('quest_queue', {}).items()):
        import re
        if not re.fullmatch(r'[a-f0-9]{20,64}', cid):
            continue
        qualification = q.get('qualification') or {}
        status = qualification.get('status')
        if status not in QUALIFICATIONS:
            status = 'REVIEW_REQUIRED'
        url = q.get('official_url')
        official = None
        if (qualification.get('fields', {}).get('official_url', {}).get('status') == 'PASS' and validate_public_url(url)):
            u = urlsplit(url)
            # Queries/fragments may contain access credentials or participant identifiers.
            official = urlunsplit((u.scheme, u.netloc, u.path, '', ''))
        lifecycle = (q.get('operations') or {}).get('state')
        rows.append({'id':cid, 'qualification':status, 'lifecycle':lifecycle if lifecycle in STATES else 'EVIDENCE_REVIEW',
                     'official_url':official, 'reward_class':qualification.get('reward_class') if qualification.get('reward_class') in REWARDS else 'UNKNOWN_REWARD',
                     'missing':sorted(set(qualification.get('missing_evidence', [])) & set(WEIGHTS)),
                     'deadline':timestamp(q.get('end_at'))})
    if len(rows) > 1000:
        raise ValueError('Snapshot capacity exceeded; do not silently truncate candidates')
    tracking = report.get('reward_tracking') or {}
    monitor = tracking.get('monitor') or {}
    # Counts alone, no transaction identifiers, balances, wallet addresses or assets.
    receipts = {(r.get('signature'),r.get('asset')) for r in tracking.get('actual_rewards_received', [])
                if r.get('attribution') == 'CONFIRMED_QUEST_REWARD' and r.get('signature') and r.get('asset')}
    return {'schema_version':1, 'generated_at':generated, 'opportunities':rows,
            'confirmed_receipts':len(receipts), 'monitor':{
                'status':monitor.get('status') if monitor.get('status') in MONITORS else 'UNKNOWN',
                'last_success_at':timestamp(monitor.get('last_success_at')),
                'gaps':sorted((set(monitor.get('gaps', [])) | set(monitor.get('scope_gaps', []))) & GAPS)}}
