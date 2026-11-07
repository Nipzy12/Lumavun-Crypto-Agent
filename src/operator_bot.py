"""Allowlisted read-only Telegram commands. No command can approve or execute work."""
from collections import Counter
import os
import re
import requests
from qualification import qualify
from operations import playbook
from triage import worklist
from telegram import notify


def reply(text, state, now=None):
    parts = text.strip().split()
    command = parts[0] if parts else ''
    queue = state.get('quest_queue', {})
    if command not in ('/status', '/worklist', '/opportunity', '/rewards', '/monitor'):
        return 'Read-only commands: /status /worklist /opportunity ID /rewards /monitor. Changes require explicit authenticated workflow authorization.'
    if command == '/opportunity':
        if len(parts) != 2 or parts[1] not in queue:
            return 'Use /opportunity with an ID from /worklist.'
        row = playbook(queue[parts[1]], now)
        # Deliberately omit task proofs, applicant-fit notes, drafts and identity.
        missing = qualify(queue[parts[1]], now)['missing_evidence']
        return '\n'.join([str(row['name'])[:100], row['lifecycle'] + ' | ' + row['qualification'],
                          row['official_url'] or 'Official URL unverified', row['kind'],
                          'Missing: ' + ', '.join(missing), 'Deadline: ' + str(row['deadline']),
                          'Reward: ' + row['reward_classification'] + ' (not received income)'])[:3200]
    if len(parts) != 1:
        return 'Unexpected arguments; commands are read-only.'
    if command == '/status':
        counts = Counter(qualify(q, now)['status'] for q in queue.values())
        lifecycle = Counter((q.get('operations') or {}).get('state', 'DISCOVERED') for q in queue.values())
        return 'Qualification: ' + ', '.join(f'{k}={v}' for k,v in sorted(counts.items())) + '\nLifecycle: ' + ', '.join(f'{k}={v}' for k,v in sorted(lifecycle.items()))
    if command == '/worklist':
        rows = worklist(queue, now)['candidates'][:5]
        eligible = [q for q in queue.values() if qualify(q, now)['status'] == 'ACTIONABLE'][:5]
        ready = 'Qualified for human review (no task authorization implied):\n' + '\n'.join(q['id'] + ' ' + str(q.get('name', ''))[:65] for q in eligible) + '\n' if eligible else ''
        return ready + 'REVIEW_REQUIRED — not eligible yet\n' + '\n'.join(f"{r['campaign_id']} {r['name'][:65]}\nMissing: {', '.join(r['missing_evidence'][:4])}" for r in rows)
    stores = state.get('reward_wallets', {}).values()
    if command == '/rewards':
        events = [e for s in stores for e in s.get('events', {}).values()]
        confirmed = { (e.get('signature'), e.get('asset')) for e in events if e.get('attribution') == 'CONFIRMED_QUEST_REWARD' and e.get('signature') }
        return f'Confirmed attributed receipts: {len(confirmed)}. Grant budgets, badges, advertised points and unverified transfers are not income.'
    monitors = [s.get('monitor', {}) for s in stores]
    return 'Read-only monitoring\n' + ('\n'.join(f"{m.get('status', 'UNKNOWN')} | last success: {m.get('last_success_at', 'never')} | gaps: {', '.join(m.get('gaps', []) + m.get('scope_gaps', []))}" for m in monitors) or 'No wallet monitoring state')


def poll(state, *, token=None, chat=None, allowed=None, post=requests.post, send=notify, checkpoint=None):
    mode = os.getenv('TELEGRAM_OPERATOR_MODE', 'polling')
    if mode == 'webhook' and os.getenv('TELEGRAM_WEBHOOK_VERIFIED') == 'true':
        return {'status': 'WEBHOOK_MODE', 'replied': 0}
    token = token if token is not None else os.getenv('TELEGRAM_BOT_TOKEN', '')
    chat = chat if chat is not None else os.getenv('TELEGRAM_CHAT_ID', '')
    raw = allowed if allowed is not None else os.getenv('TELEGRAM_OPERATOR_USER_IDS', '')
    # Fail closed: no implicit authorization from chat membership or bot access.
    if not token or not re.fullmatch(r'-?\d+', str(chat)) or not re.fullmatch(r'\d+(?:\s*,\s*\d+)*', raw):
        return {'status': 'DISABLED', 'reason': 'Explicit operator user allowlist and chat required', 'replied': 0}
    allowlist = set(x.strip() for x in raw.split(','))
    cursor = state.setdefault('telegram_operator', {'offset': 0})
    offset = cursor.get('offset', 0)
    offset = offset if type(offset) is int and offset >= 0 else 0
    try:
        response = post(f'https://api.telegram.org/bot{token}/getUpdates', json={'offset': offset, 'limit': 20, 'timeout': 0, 'allowed_updates': ['message']}, timeout=10, allow_redirects=False)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError('Invalid response')
        if data.get('ok') is not True or not isinstance(data.get('result'), list):
            raise ValueError('Invalid updates')
    except (requests.RequestException, ValueError, TypeError):
        return {'status': 'UNAVAILABLE', 'replied': 0}
    replied = 0
    for update in sorted(data['result'], key=lambda u: u.get('update_id', -1) if isinstance(u, dict) and type(u.get('update_id')) is int else -1):
        if not isinstance(update, dict) or type(update.get('update_id')) is not int or update['update_id'] < offset:
            continue
        message = update.get('message') or {}
        if not isinstance(message, dict):
            message = {}
        sender = message.get('from') or {}
        if not isinstance(sender, dict) or not isinstance(message.get('chat', {}), dict):
            message = {}
            sender = {}
        authorized = str((message.get('chat') or {}).get('id')) == str(chat) and str(sender.get('id')) in allowlist and sender.get('is_bot') is False and not message.get('sender_chat') and not message.get('forward_origin')
        # At-most-once dispatch: persist claim before sending. Ambiguous delivery
        # is never retried; the operator can issue a new command.
        offset = update['update_id'] + 1
        cursor['offset'] = offset
        if checkpoint:
            try:
                if not checkpoint(state, update['update_id']):
                    continue
            except (OSError, ValueError, TimeoutError):
                return {'status': 'PERSISTENCE_UNAVAILABLE', 'replied': replied}
        text = message.get('text')
        if authorized and isinstance(text, str) and text.startswith('/') and len(text) <= 200:
            result = send(token, str(chat), reply(text, state))
            replied += int(bool(result.get('sent')))
    return {'status': 'OK', 'replied': replied, 'delivery': 'at-most-once; retry a new command after ambiguous delivery'}


def claim_update(state, update_id):
    """Atomically reserve a reply across restarted or overlapping workers."""
    import json
    from pathlib import Path
    from state import state_lock, load_state_from_artifact, merge_states
    with state_lock(strict=True):
        path = Path('dedup_state.json')
        if path.exists():
            raw = json.loads(path.read_text())
            if not isinstance(raw, dict):
                raise ValueError('Invalid state')
        existing = load_state_from_artifact(read_only=True)
        if existing.get('telegram_operator', {}).get('offset', 0) > update_id:
            return False
        merged = merge_states(existing, state)
        merged['telegram_operator'] = {'offset': max(update_id + 1, merged.get('telegram_operator', {}).get('offset', 0))}
        tmp = path.with_suffix('.telegram.tmp')
        with tmp.open('w') as handle:
            json.dump(merged, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        return True
