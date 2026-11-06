"""Synthetic, deterministic quest fixtures; none represents a live payout."""
from copy import deepcopy
from datetime import datetime, timezone
import json

import pytest

from config_loader import load_config
from discovery import _build_campaign
from sources import RawItem, SourceSpec
from quests import from_campaign, update_queue, record_task_action, task_policy, queue_digest
from state import default_state, save_state_locally, load_state_from_artifact, merge_states

NOW = datetime(2026, 11, 6, 12, tzinfo=timezone.utc)
TEXT = ('Example announces a points program. Join the community to earn points. '
        'Submit your public wallet address. Participation is free. Open to everyone. '
        'Starts on 2026-11-01. Deadline 2026-12-01. Solana wallet required. '
        'Rewards are not guaranteed.')


def campaign(text=TEXT):
    spec = SourceSpec(id='example', url='https://example.test/feed', official=True)
    item = RawItem('Example announces Points campaign', text, 'https://example.test/quest/1',
                   'https://example.test/quest/1', spec.url, spec.id)
    return _build_campaign(item, spec, load_config())


def quest(text=TEXT):
    return deepcopy(campaign(text).quest_intelligence)


def test_real_feed_parser_to_quest_intelligence(requests_mock):
    from discovery import scan_sources
    from sources import reset_network_caches
    reset_network_caches()
    requests_mock.get('https://example.test/robots.txt', text='User-agent: *\nAllow: /')
    requests_mock.get('https://example.test/feed', text=f'<rss version="2.0"><channel><item><title>Example announces Points campaign</title><link>https://example.test/quest/1</link><description>{TEXT}</description></item></channel></rss>')
    found, health, errors = scan_sources([SourceSpec(id='example', url='https://example.test/feed', official=True)], min_interval=0)
    assert not errors and health[0].reachable
    q = found[0].quest_intelligence
    assert q['evidence_verified']
    assert q['project'] == 'Example'
    assert q['official_url'] == 'https://example.test/quest/1'
    assert q['start_at'] == '2026-11-01' and q['end_at'] == '2026-12-01'
    assert q['reward_type'] == 'OFFCHAIN_POINTS' and q['reward_guaranteed'] is False
    assert q['networks'] == ['Solana'] and q['wallet_requirements']
    assert len(q['tasks']) == 2
    assert q['cost_status'] == 'PASS' and q['eligibility_status'] == 'PASS'


@pytest.mark.parametrize('text,expected', [
    (TEXT, 'NEEDS_APPROVAL'),
    (TEXT.replace('Open to everyone.', ''), 'VERIFIED'),
    (TEXT.replace('Open to everyone.', 'Open to everyone except US residents.'), 'VERIFIED'),
    (TEXT.replace('Participation is free.', 'You must deposit 1 SOL.'), 'REJECTED'),
    (TEXT.replace('Participation is free.', 'Mint a badge; gas fees not stated.'), 'VERIFIED'),
    (TEXT.replace('2026-12-01', '2026-11-01'), 'EXPIRED'),
    (TEXT.replace('2026-11-01', '2026-11-20'), 'VERIFIED'),
])
def test_queue_eligibility_and_cost_gates(text, expected):
    state = default_state()
    q = quest(text)
    update_queue(state, [q], NOW)
    assert q['status'] == expected
    assert q['status'] != 'COMPLETED'


def test_news_never_enters_task_queue():
    c = campaign('The markets followed news about rewards yesterday.')
    assert c.opportunity_type == 'NEWS_ONLY'
    state = default_state()
    assert update_queue(state, [c.quest_intelligence], NOW) == {}


@pytest.mark.parametrize('text,reason', [
    ('Connect wallet', 'wallet'), ('Sign this message', 'wallet'),
    ('Authorize account', 'account'), ('Follow on Twitter', 'social'),
    ('Submit email', 'personal_information'), ('Claim rewards', 'claim'),
    ('Solve an unknown task', 'unclassified_action'),
])
def test_sensitive_task_requires_human_approval(text, reason):
    assert reason in task_policy(text)


def test_duplicate_tasks_and_human_completion(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    state = default_state()
    q = quest(TEXT + ' Join the community to earn points.')
    update_queue(state, [q, deepcopy(q)], NOW)
    assert len(state['quest_queue']) == 1
    q = state['quest_queue'][q['id']]
    assert len(q['tasks']) == 2
    for tid in q['tasks']:
        with pytest.raises(ValueError, match='approval'):
            record_task_action(state, q['id'], tid, 'complete', 'Performed the task manually', NOW)
        with pytest.raises(ValueError, match='URL alone'):
            record_task_action(state, q['id'], tid, 'approve', q['official_url'], NOW)
        record_task_action(state, q['id'], tid, 'approve', 'I approve this specific task', NOW)
        record_task_action(state, q['id'], tid, 'complete', 'Manually verified completion on the official platform', NOW)
        with pytest.raises(ValueError):
            record_task_action(state, q['id'], tid, 'complete', 'Duplicate attempt', NOW)
    assert q['status'] == 'COMPLETED'
    save_state_locally(state)
    restored = load_state_from_artifact()
    update_queue(restored, [quest()], NOW)
    assert restored['quest_queue'][q['id']]['status'] == 'COMPLETED'
    assert queue_digest(restored['quest_queue'])['completed_tasks'] == 2


def test_approval_does_not_complete_or_execute():
    state = default_state()
    q = quest()
    update_queue(state, [q], NOW)
    for tid in q['tasks']:
        record_task_action(state, q['id'], tid, 'approve', 'Human approval', NOW)
    assert q['status'] == 'READY'
    assert not any(t['completion'] for t in q['tasks'].values())


def test_changed_terms_invalidate_approval():
    state = default_state()
    q = quest()
    update_queue(state, [q], NOW)
    tid = next(iter(q['tasks']))
    record_task_action(state, q['id'], tid, 'approve', 'Approve original terms', NOW)
    changed = quest(TEXT.replace('2026-12-01', '2026-12-02'))
    update_queue(state, [changed], NOW)
    assert changed['tasks'][tid]['approval'] is None


def test_expiry_without_fresh_source_and_expired_actions_blocked():
    state = default_state()
    q = quest()
    update_queue(state, [q], NOW)
    later = datetime(2027, 1, 1, tzinfo=timezone.utc)
    update_queue(state, [], later)
    assert q['status'] == 'EXPIRED'
    with pytest.raises(ValueError):
        record_task_action(state, q['id'], next(iter(q['tasks'])), 'approve', 'Approval', later)


def test_corrupt_nested_state_is_sanitized(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from pathlib import Path
    Path('dedup_state.json').write_text(json.dumps({'version': '1.3', 'quest_queue': {'bad': {'tasks': []}}, 'reward_wallets': {'bad': {'assets': [], 'events': 'bad'}}}))
    restored = load_state_from_artifact()
    assert restored['quest_queue'] == {}
    assert restored['reward_wallets']['bad']['events'] == {}
    assert restored['version'] == '1.4'


def test_concurrent_records_merge_without_losing_approval():
    left, right = default_state(), default_state()
    q = quest()
    update_queue(left, [q], NOW)
    update_queue(right, [quest()], NOW)
    tid = next(iter(q['tasks']))
    record_task_action(left, q['id'], tid, 'approve', 'Human review', NOW)
    merged = merge_states(left, right)
    assert merged['quest_queue'][q['id']]['tasks'][tid]['approval']


def test_stale_verification_requires_refresh():
    state = default_state()
    q = quest()
    update_queue(state, [q], NOW)
    update_queue(state, [], datetime(2026, 11, 8, tzinfo=timezone.utc))
    assert q['status'] == 'VERIFIED'


def test_queue_cli_records_only_decision(tmp_path, monkeypatch):
    import subprocess
    import sys
    from pathlib import Path
    monkeypatch.chdir(tmp_path)
    state = default_state()
    q = quest()
    # Use current time, with a future deadline, for the actual CLI check.
    q['start_at'] = '2020-01-01'
    q['end_at'] = '2099-12-01'
    update_queue(state, [q])
    save_state_locally(state)
    script = Path(__file__).resolve().parents[1] / 'scripts' / 'quest_queue.py'
    tid = next(iter(q['tasks']))
    result = subprocess.run([sys.executable, str(script), 'approve', '--quest', q['id'], '--task', tid, '--evidence', 'Reviewed this task manually'], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert 'no external action executed' in result.stdout
    restored = load_state_from_artifact()
    assert restored['quest_queue'][q['id']]['tasks'][tid]['approval']
    assert not restored['quest_queue'][q['id']]['tasks'][tid]['completion']


def test_each_coordinated_task_has_its_own_identity():
    from quests import task_checklist
    tasks = task_checklist(['Follow the account, join Discord and submit your address.'], 'quest')
    assert len(tasks) == 3
    assert len({t['id'] for t in tasks.values()}) == 3
    assert all(t['approval_reasons'] for t in tasks.values())


def test_public_read_task_can_be_ready_but_is_not_automatically_complete():
    from quests import task_checklist
    q = quest()
    q['tasks'] = task_checklist(['Read the public documentation'], q['id'])
    update_queue(default_state(), [q], NOW)
    assert q['status'] == 'READY'
    assert not next(iter(q['tasks'].values()))['completion']
