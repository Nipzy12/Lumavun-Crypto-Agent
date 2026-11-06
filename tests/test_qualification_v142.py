"""Synthetic evidence fixtures; no mocked payout is reported as actual earnings."""
from copy import deepcopy
from datetime import timedelta
import json
import pytest
from test_quests_v14 import quest, NOW
from qualification import qualify, reward_class, migrate_record, shortlist, WEIGHTS
from quests import update_queue, review_candidate
from state import default_state, save_state_locally, load_state_from_artifact, merge_states
from telegram import build_daily_summary


def qualified(**changes):
    q = quest(); q.update(changes)
    state = default_state(); update_queue(state, [q], NOW)
    return q, state


def test_independent_fields_and_deterministic_score():
    q, _ = qualified()
    first = qualify(q, NOW)
    assert first == qualify(q, NOW)
    assert first['status'] == 'ACTIONABLE' and first['score'] == 100
    assert sum(WEIGHTS.values()) == 100
    assert set(first['fields']) == set(WEIGHTS)
    for field in first['fields'].values():
        assert field['status'] == 'PASS'
        assert field['evidence_url'].startswith('https://')
        assert field['explanation'] and field['last_verified_at'] == NOW.isoformat()


@pytest.mark.parametrize('changes,field,status', [
    ({'reward_evidence': ''}, 'reward_type', 'UNKNOWN'),
    ({'cost_status': 'UNKNOWN'}, 'mandatory_fees', 'UNKNOWN'),
    ({'cost_status': 'FAIL'}, 'mandatory_fees', 'FAIL'),
    ({'eligibility_text': 'Open to everyone. US only.'}, 'geographic_eligibility', 'UNKNOWN'),
    ({'eligibility_text': 'Verified account required. KYC required.'}, 'account_eligibility', 'UNKNOWN'),
    ({'eligibility_status': 'FAIL'}, 'account_eligibility', 'FAIL'),
    ({'end_at': '2020-01-01'}, 'deadline', 'FAIL'),
    ({'start_at': None}, 'start_date', 'UNKNOWN'),
    ({'end_at': None}, 'deadline', 'UNKNOWN'),
    ({'official_url': 'https://example.test.evil.com/quest'}, 'official_url', 'FAIL'),
    ({'official_url': 'https://example.test@evil.com/quest'}, 'official_url', 'FAIL'),
    ({'official_url': 'http://example.test/quest'}, 'official_url', 'FAIL'),
])
def test_missing_or_contradictory_evidence_never_actionable(changes, field, status):
    q, _ = qualified(**changes)
    result = qualify(q, NOW)
    assert result['fields'][field]['status'] == status
    assert result['status'] != 'ACTIONABLE'


def test_geography_and_account_are_independent():
    q, _ = qualified(eligibility_text='Open to everyone. US only.')
    result = qualify(q, NOW)
    assert result['fields']['geographic_eligibility']['status'] == 'UNKNOWN'
    assert result['fields']['account_eligibility']['status'] == 'PASS'


def test_stale_or_future_evidence_is_unknown():
    q, _ = qualified()
    for time in (NOW-timedelta(days=2), NOW+timedelta(days=1)):
        q['last_observed'] = time.isoformat()
        assert qualify(q, NOW)['fields']['freshness']['status'] == 'UNKNOWN'
    assert qualify(q, NOW)['fields']['freshness']['last_verified_at'] == q['last_observed']


@pytest.mark.parametrize('kind,evidence,guaranteed,expected', [
    ('EDUCATIONAL_BADGE', 'Educational badge ID 1', None, 'EDUCATIONAL_BADGE'),
    ('OFFCHAIN_POINTS', 'Earn points; potential airdrop later', None, 'POINTS_ONLY'),
    ('TOKEN', 'May receive tokens in a potential airdrop', None, 'SPECULATIVE_AIRDROP'),
    ('TOKEN', 'Receive 100 tokens after qualifying', False, 'CONDITIONAL_TOKEN_REWARD'),
    ('TOKEN', 'Receive 100 tokens; guaranteed by published terms', True, 'CONFIRMED_TOKEN_REWARD'),
    ('TOKEN', '', None, 'UNKNOWN_REWARD'),
])
def test_reward_classes_are_not_received_earnings(kind, evidence, guaranteed, expected):
    q, _ = qualified(reward_type=kind, reward_evidence=evidence, reward_guaranteed=guaranteed, campaign_type='FREE_QUEST')
    assert reward_class(q) == expected
    assert 'actual_rewards_received' not in q


def test_high_score_cannot_override_risk():
    q, _ = qualified(risk_band='high')
    result = qualify(q, NOW)
    assert result['score'] == 95 and result['status'] == 'BLOCKED'
    assert result['failed_conditions'] == ['risk']


def test_expired_status_overrides_ranking():
    q, _ = qualified(end_at='2020-01-01')
    assert q['qualification']['status'] == 'EXPIRED'


def test_history_is_append_only_and_merge_safe(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    q, state = qualified()
    review_candidate(state, q['id'], 'defer', 'Need a second review', NOW, reason='Verify terms', reviewer='operator-a')
    earlier = deepcopy(state)
    review_candidate(state, q['id'], 'accept', 'Checked current official evidence', NOW+timedelta(seconds=1), reason='All conditions established', reviewer='operator-b')
    history = deepcopy(q['review_history'])
    assert len(history) == 2
    assert history[0]['previous_status'] == 'ACTIONABLE' and history[0]['new_status'] == 'REVIEW_REQUIRED'
    assert history[1]['previous_status'] == 'REVIEW_REQUIRED' and history[1]['new_status'] == 'ACTIONABLE'
    assert history[0]['reviewer'] == 'operator-a' and history[0]['reason'] == 'Verify terms'
    save_state_locally(state)
    restored = merge_states(load_state_from_artifact(), earlier)
    assert restored['quest_queue'][q['id']]['review_history'] == history
    update_queue(restored, [quest()], NOW+timedelta(minutes=1))
    assert restored['quest_queue'][q['id']]['review_history'] == history
    assert all(t['approval'] is None for t in restored['quest_queue'][q['id']]['tasks'].values())


def test_review_accept_cannot_supply_unknown_reward():
    q, state = qualified(reward_evidence='')
    with pytest.raises(ValueError, match='Cannot accept'):
        review_candidate(state, q['id'], 'accept', 'I approve anyway', NOW)
    assert q['review_history'] == []


def test_migration_preserves_progress_and_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    q, state = qualified()
    q.pop('qualification'); q.pop('qualification_schema'); q.pop('review_history')
    q['human_review'] = {'kind':'human_review', 'decision':'defer', 'evidence':'Prior review', 'at':NOW.isoformat()}
    task = next(iter(q['tasks'].values()))
    task['approval'] = {'kind':'human_approval','evidence':'Approved manually','at':NOW.isoformat()}
    task['completion'] = {'kind':'human_confirmation','evidence':'Completed manually','at':NOW.isoformat()}
    state['notifications']['existing'] = {'sent_at':NOW.isoformat(),'kind':'opportunity'}
    Path = __import__('pathlib').Path
    Path('dedup_state.json').write_text(json.dumps(state))
    restored = load_state_from_artifact(); record = restored['quest_queue'][q['id']]
    assert record['tasks'][task['id']]['completion'] == task['completion']
    assert len(record['review_history']) == 1
    before = deepcopy(record); migrate_record(record); assert record == before
    assert 'existing' in restored['notifications']
    record['qualification'] = None; record['review_history'] = None
    save_state_locally(restored)
    again = load_state_from_artifact()['quest_queue'][q['id']]
    assert again['tasks'][task['id']]['approval'] == task['approval']
    assert again['tasks'][task['id']]['completion'] == task['completion']


def test_changed_task_preserves_history_in_archive():
    q, state = qualified()
    original = deepcopy(q['tasks'])
    incoming = quest(); incoming['tasks'] = {}
    update_queue(state, [incoming], NOW)
    assert state['quest_queue'][q['id']]['task_archive'] == original


def test_shortlist_separates_badges_and_missing_evidence():
    q, state = qualified(reward_type='EDUCATIONAL_BADGE', campaign_type='FREE_QUEST', reward_evidence='Badge 1', cost_status='UNKNOWN')
    groups = shortlist(state['quest_queue'])
    assert len(groups['EDUCATIONAL']) == 1 and not groups['ACTIONABLE']
    text = build_daily_summary('now', {}, [], [], [], quest_summary={'shortlist':groups}, reward_summary={'actual_rewards_received':[]})
    assert 'Pendidikan — bukan penghasilan' in text and q['official_url'] in text
    assert 'mandatory_fees' in text and 'gas_costs' in text
    assert 'atribusi terverifikasi (kumulatif): 0' in text


def test_malformed_optional_task_fields_do_not_drop_completion(tmp_path, monkeypatch):
    from pathlib import Path
    monkeypatch.chdir(tmp_path)
    q, state = qualified()
    task = next(iter(q['tasks'].values()))
    task['completion'] = {'kind':'human_confirmation','evidence':'Performed manually','at':NOW.isoformat()}
    task['approval_reasons'] = None
    q['evidence_checks'] = ['invalid optional field']
    q['qualification'] = 'invalid optional field'
    Path('dedup_state.json').write_text(json.dumps(state))
    restored = load_state_from_artifact()
    update_queue(restored, [], NOW)
    actual = restored['quest_queue'][q['id']]['tasks'][task['id']]
    assert actual['completion'] == task['completion'] and actual['approval_reasons']


def test_new_qualification_does_not_attribute_promised_rewards():
    from test_rewards_v14 import wallet, ADDRESS, no_transfers
    from rewards import update_rewards
    q, state = qualified(reward_type='TOKEN', campaign_type='FREE_QUEST', reward_evidence='Receive 100 tokens', reward_guaranteed=True)
    assert q['qualification']['reward_class'] == 'CONFIRMED_TOKEN_REWARD'
    result = update_rewards(state, wallet(), ADDRESS, fetch=no_transfers)
    assert result['actual_rewards_received'] == []


def test_official_url_must_match_observed_campaign_path():
    q, _ = qualified(official_url='https://example.test/unobserved-campaign')
    assert q['qualification']['fields']['official_url']['status'] == 'FAIL'


def test_task_approval_cannot_override_unknown_dates():
    from quests import record_task_action
    q, state = qualified(start_at=None)
    with pytest.raises(ValueError, match='not eligible'):
        record_task_action(state, q['id'], next(iter(q['tasks'])), 'approve', 'Reviewed manually', NOW)


def test_digest_remains_one_compact_message():
    q, state = qualified()
    groups = shortlist(state['quest_queue'])
    item = groups['ACTIONABLE'][0]
    for name in groups:
        groups[name] = [{**item, 'name': '長'*300, 'official_url':'https://example.test/'+'x'*3000}]
    text = build_daily_summary('now', {}, [], [], ['x'*1000]*3, quest_summary={'shortlist':groups})
    assert len(text.encode('utf-8')) <= 3800


def test_gas_sponsorship_cannot_approve_unknown_mandatory_fees():
    q, _ = qualified(cost_status='UNKNOWN', gas_status='PASS', gas_evidence='Explicit sponsored gas')
    assert q['qualification']['fields']['gas_costs']['status'] == 'PASS'
    assert q['qualification']['fields']['mandatory_fees']['status'] == 'UNKNOWN'
    assert q['qualification']['status'] == 'REVIEW_REQUIRED'


def test_deposit_does_not_prove_paid_gas():
    q, _ = qualified(cost_status='FAIL')
    assert q['qualification']['fields']['gas_costs']['status'] == 'UNKNOWN'
    assert q['qualification']['status'] == 'BLOCKED'


def test_badge_type_does_not_establish_reward_certainty():
    q, _ = qualified(reward_type='EDUCATIONAL_BADGE', campaign_type='FREE_QUEST', reward_evidence='Badge 1', reward_guaranteed=None)
    assert q['qualification']['fields']['reward_type']['status'] == 'PASS'
    assert q['qualification']['fields']['reward_certainty']['status'] == 'UNKNOWN'


def test_negated_gas_payment_is_not_a_failure():
    from test_quests_v14 import quest, TEXT
    q = quest(TEXT + ' No gas fee required.')
    assert q['gas_status'] == 'PASS'


def test_gas_status_without_evidence_is_unknown():
    q, _ = qualified(gas_status='PASS', gas_evidence=None)
    assert q['qualification']['fields']['gas_costs']['status'] == 'UNKNOWN'
