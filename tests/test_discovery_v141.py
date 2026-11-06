"""Deterministic fixtures, never live reward or earnings claims."""
from copy import deepcopy
from datetime import timedelta
import json
import subprocess
import sys
from pathlib import Path

import pytest
from test_quests_v14 import quest, NOW, TEXT
from diagnostics import candidate_reasons, discovery_report
from discovery import SourceHealth, _build_campaign
from quests import update_queue, review_candidate
from state import default_state, save_state_locally, load_state_from_artifact, merge_states
from sources import SourceSpec, parse_bankless_lessons, SourceError
from config_loader import load_config


def codes(q):
    return {r['code'] for r in candidate_reasons(q, now=NOW)}


@pytest.mark.parametrize('changes,expected', [
    ({'evidence_verified': False, 'evidence_checks': {'official': False, 'reward': False}}, {'MISSING_OFFICIAL_EVIDENCE', 'MISSING_REWARD_EVIDENCE'}),
    ({'cost_status': 'UNKNOWN'}, {'UNKNOWN_COSTS'}),
    ({'cost_status': 'FAIL'}, {'MANDATORY_PAYMENT'}),
    ({'end_at': '2026-01-01'}, {'EXPIRED_CAMPAIGN'}),
    ({'eligibility_status': 'UNKNOWN'}, {'ELIGIBILITY_UNKNOWN'}),
    ({'campaign_type': 'NEWS_ONLY'}, {'NEWS_ONLY'}),
    ({'status': 'NEEDS_APPROVAL'}, {'AWAITING_HUMAN_REVIEW'}),
    ({'tasks': {}}, {'MISSING_PARTICIPATION_REQUIREMENTS'}),
])
def test_structured_reasons(changes, expected):
    q = quest(); q.update(changes)
    assert expected <= codes(q)
    assert all(r['detail'] and r['next_action'] for r in candidate_reasons(q, now=NOW))


@pytest.mark.parametrize('field,value', [('cost_status', 'UNKNOWN'), ('cost_status', 'FAIL'),
    ('evidence_verified', False), ('eligibility_status', 'UNKNOWN'), ('end_at', '2020-01-01')])
def test_accept_cannot_override_gates(field, value):
    q = quest(); q[field] = value
    state = default_state(); update_queue(state, [q], NOW)
    with pytest.raises(ValueError, match='Cannot accept'):
        review_candidate(state, q['id'], 'accept', 'Reviewed manually', NOW)
    assert not q.get('human_review')


def test_accept_does_not_approve_tasks():
    q = quest(); state = default_state(); update_queue(state, [q], NOW)
    review_candidate(state, q['id'], 'accept', 'Reviewed all campaign evidence', NOW)
    assert q['status'] == 'NEEDS_APPROVAL'
    assert all(t['approval'] is None for t in q['tasks'].values())


@pytest.mark.parametrize('decision,status', [('reject', 'REJECTED'), ('defer', 'VERIFIED')])
def test_review_survives_artifact_restore_and_duplicate_scan(tmp_path, monkeypatch, decision, status):
    monkeypatch.chdir(tmp_path)
    q = quest(); state = default_state(); update_queue(state, [q], NOW)
    stale = deepcopy(state)
    review_candidate(state, q['id'], decision, 'Reviewed official evidence manually', NOW + timedelta(seconds=1))
    save_state_locally(state)
    restored = load_state_from_artifact()
    merged = merge_states(restored, stale)
    update_queue(merged, [quest()], NOW + timedelta(minutes=1))
    actual = merged['quest_queue'][q['id']]
    assert actual['status'] == status
    assert actual['human_review']['decision'] == decision
    assert len(merged['quest_queue']) == 1


def test_review_listing_does_not_rewrite_corrupt_artifact(tmp_path):
    artifact = tmp_path / 'dedup_state.json'; artifact.write_text('{broken')
    script = Path(__file__).resolve().parents[1] / 'scripts/quest_queue.py'
    run = subprocess.run([sys.executable, str(script), 'review'], cwd=tmp_path, capture_output=True, text=True)
    assert run.returncode == 0 and json.loads(run.stdout) == []
    assert artifact.read_text() == '{broken'
    assert list(tmp_path.iterdir()) == [artifact]


def test_scan_statistics_and_duplicates():
    q = quest(); state = default_state(); update_queue(state, [q], NOW)
    news = deepcopy(q); news['id'] = 'news'; news['campaign_type'] = 'NEWS_ONLY'
    health = SourceHealth(url='https://example.test/feed', source_id='test', reachable=True, entries_received=100, entries_analyzed=3)
    failed = SourceHealth(url='https://unreachable.test/feed', source_id='bad', reachable=False, error='HTTP 429')
    report = discovery_report([q, deepcopy(q), news], [health, failed], state['quest_queue'], {q['id']}, {}, NOW)
    stats = report['statistics']
    assert stats['articles_received'] == 100 and stats['articles_scanned'] == 3
    assert stats['quest_candidates'] == stats['officially_verified'] == stats['actionable_quests'] == 1
    assert stats['news_rejected'] == 1 and stats['duplicate_observations'] == 2
    assert stats['confirmed_rewards'] == 0
    assert report['source_failures'][0]['reasons'][0]['code'] == 'SOURCE_UNREACHABLE'


SPEC = SourceSpec(id='bankless', url='https://app.banklessacademy.com/api/lessons', kind='json', parser='bankless_lessons', official=True)
LESSON = {'name': 'Wallet Basics', 'type': 'LESSON', 'publicationStatus': 'publish',
          'quest': 'WalletBasics', 'badgeId': 1,
          'lessonLinks': {'en': 'https://app.banklessacademy.com/lessons/wallet-basics'},
          'learningActions': 'Create and manage your own wallet\nConnect your wallet to a web3 website'}


def test_documented_lesson_extraction_keeps_unknown_fees():
    row = {**LESSON, 'slides': [{'content': 'Participation is free. Open to everyone. Buy tokens.'}]}
    items = parse_bankless_lessons(json.dumps([row]).encode(), SPEC)
    q = _build_campaign(items[0], SPEC, load_config()).quest_intelligence
    assert q['evidence_verified'] and q['project'] == 'Bankless Academy'
    assert q['reward_type'] == 'EDUCATIONAL_BADGE'
    assert q['cost_status'] == q['eligibility_status'] == 'UNKNOWN'
    assert len(q['tasks']) == 2 and q['reward_guaranteed'] is None
    assert q['evidence_checks']['reward']
    state = default_state(); update_queue(state, [q], NOW)
    assert q['status'] == 'VERIFIED'


@pytest.mark.parametrize('changes', [{'publicationStatus': 'deprecated'}, {'type': 'ARTICLE'}, {'quest': None}, {'lessonLinks': {'en': 'https://evil.test/quest'}}])
def test_bankless_excludes_unmaintained_nonquest_or_external(changes):
    assert parse_bankless_lessons(json.dumps([{**LESSON, **changes}]).encode(), SPEC) == []


def test_bankless_missing_tasks_or_reward_is_unverified():
    for changes, expected in [({'learningActions': ''}, 'MISSING_PARTICIPATION_REQUIREMENTS'), ({'badgeId': None}, 'MISSING_REWARD_EVIDENCE')]:
        item = parse_bankless_lessons(json.dumps([{**LESSON, **changes}]).encode(), SPEC)[0]
        q = _build_campaign(item, SPEC, load_config()).quest_intelligence
        assert not q['evidence_verified'] and q['campaign_type'] == 'UNVERIFIED'
        assert expected in codes(q)


def test_bankless_bad_schema_is_source_failure():
    with pytest.raises(SourceError):
        parse_bankless_lessons(b'{}', SPEC)


def test_bankless_explicit_mandatory_payment_is_not_unknown():
    row = {**LESSON, 'learningActions': 'You must deposit 1 ETH.'}
    item = parse_bankless_lessons(json.dumps([row]).encode(), SPEC)[0]
    q = _build_campaign(item, SPEC, load_config()).quest_intelligence
    assert q['cost_status'] == 'FAIL'
    assert 'MANDATORY_PAYMENT' in codes(q)


def test_daily_discovery_digest_is_idempotent_after_restore(tmp_path, monkeypatch):
    import main
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'mock-token')
    monkeypatch.setenv('TELEGRAM_CHAT_ID', 'mock-chat')
    monkeypatch.setattr(main, 'scan_sources', lambda *a, **kw: ([], [], []))
    monkeypatch.setattr(main, 'read_wallet', lambda *a, **kw: {})
    sent = []
    def send(token, chat, messages):
        sent.extend(messages)
        return {'sent': True, 'messages': len(messages), 'sent_count': len(messages), 'error': None}
    monkeypatch.setattr(main, 'send_messages', send)
    assert main.main() == 0
    first = list(sent)
    assert any('Artikel dipindai: 0' in text and 'reward terkonfirmasi: 0' in text for text in sent)
    artifact = Path('dedup_state.json').read_bytes()
    Path('dedup_state.json').unlink()
    # Models downloading the persisted artifact into the next workflow runner.
    Path('dedup_state.json').write_bytes(artifact)
    assert main.main() == 0
    assert sent == first
