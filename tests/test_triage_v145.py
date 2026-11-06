"""Synthetic fixtures; never real campaigns or receipts."""
from copy import deepcopy
import pytest
from test_qualification_v142 import qualified, NOW
from triage import classify, worklist, revision
from qualification import qualify
from manual_evidence import apply_request
from state import merge_states
from quests import update_queue


def request(q, **changes):
    return dict(campaign_id=q['id'], url=q['official_url'], expected_revision=revision(q), request_id='fixture-1', reviewer='fixture', **changes)


def test_grant_is_not_airdrop_or_individual_income():
    q, _ = qualified(name='Developer grant', reward_type='TOKEN', reward_evidence='Total allocation 100000 OP grant budget')
    axes = classify(q, qualify(q, NOW))
    assert axes['reward_type'] == 'grant'
    assert axes['participation_model'] == 'developer grant'
    q.update(name='Individual quest', campaign_type='CONFIRMED_AIRDROP', reward_evidence='Receive 10 OP tokens')
    assert classify(q, qualify(q, NOW))['reward_type'] == 'token'


@pytest.mark.parametrize('changes,expected', [({'gas_status':'UNKNOWN'}, 'COST_UNKNOWN'), ({'cost_status':'UNKNOWN'}, 'COST_UNKNOWN'), ({'cost_status':'FAIL'}, 'PAYMENT_REQUIRED'), ({}, 'FREE_VERIFIED')])
def test_independent_cost(changes, expected):
    q, _ = qualified(**changes)
    assert classify(q, qualify(q, NOW))['participation_cost'] == expected


def test_eligibility_remains_unknown_with_free_cost():
    q, state = qualified(eligibility_status='UNKNOWN')
    w = worklist(state['quest_queue'], NOW)
    assert w['review_required_count'] == 1
    row = w['candidates'][0]
    assert row['qualification_status'] == 'REVIEW_REQUIRED'
    assert row['proposed_manual_verification_step']
    assert 'account_eligibility' in row['missing_evidence']


def test_import_never_verifies_and_replay_is_idempotent():
    q, state = qualified(cost_status='UNKNOWN')
    record = state['quest_queue'][q['id']]
    before = qualify(record, NOW)
    payload = request(record)
    first = apply_request(state, payload)
    assert first['status'] == 'PENDING_REVALIDATION'
    assert apply_request(state, payload) == first
    assert len(record['operator_history']) == 1
    assert qualify(record, NOW) == before
    with pytest.raises(ValueError):
        apply_request(state, dict(payload, cost_status='PASS'))
    with pytest.raises(ValueError):
        apply_request(state, dict(payload, url='https://forged.example/'))


def test_state_conflict_preserves_newer_facts():
    q, state = qualified(cost_status='UNKNOWN')
    record = state['quest_queue'][q['id']]
    payload = request(record)
    record['cost_status'] = 'FAIL'
    assert apply_request(state, payload)['status'] == 'CONFLICT'
    assert record['cost_status'] == 'FAIL'
    assert not record.get('evidence_requests')


def test_histories_survive_discovery_and_merges():
    q, state = qualified(cost_status='UNKNOWN')
    old = deepcopy(state)
    apply_request(state, request(state['quest_queue'][q['id']]))
    update_queue(state, [deepcopy(q)], NOW)
    merged = merge_states(old, state)
    assert len(merged['quest_queue'][q['id']]['operator_history']) == 1
    assert len(merged['quest_queue'][q['id']]['evidence_requests']) == 1


def test_educational_is_nonmonetary():
    q, _ = qualified(reward_type='EDUCATIONAL_BADGE')
    assert classify(q, qualify(q, NOW))['reward_type'] == 'nonmonetary'


def test_top_five_review_digest():
    from telegram import build_daily_summary
    q, _ = qualified(cost_status='UNKNOWN')
    queue = {}
    for i in range(7):
        row = deepcopy(q)
        row.update(id=str(i), name=f'Fixture candidate {i}')
        queue[str(i)] = row
    w = worklist(queue, NOW)
    text = build_daily_summary('fixture', {}, [], [], [], quest_summary={'review_worklist': w})
    assert text.count('| REVIEW_REQUIRED |') == 5
    assert 'Fixture candidate 5' not in text
    assert 'mandatory_fees' in text
    assert 'reward terkonfirmasi' not in text or '0' in text


def test_forged_url_is_only_request_and_cannot_supply_facts():
    q, state = qualified(cost_status='UNKNOWN')
    payload = request(state['quest_queue'][q['id']])
    payload['url'] = 'https://forged.example/claims-free'
    apply_request(state, payload)
    assert state['quest_queue'][q['id']]['cost_status'] == 'UNKNOWN'
    assert state['quest_queue'][q['id']]['evidence_requests'][0]['status'] == 'PENDING'


def test_import_transaction_and_restore_failure(tmp_path, monkeypatch):
    import json
    import importlib.util
    from pathlib import Path
    script = Path(__file__).resolve().parents[1] / 'scripts/import_evidence.py'
    spec = importlib.util.spec_from_file_location('import_script', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    q, state = qualified(cost_status='UNKNOWN')
    monkeypatch.chdir(tmp_path)
    path = tmp_path / 'dedup_state.json'
    path.write_text(json.dumps(state))
    payload = request(state['quest_queue'][q['id']])
    for key, env in {'campaign_id':'QUEST_ID', 'url':'EVIDENCE_URL', 'expected_revision':'EXPECTED_REVISION', 'request_id':'REVIEW_REQUEST_ID', 'reviewer':'REVIEWER'}.items():
        monkeypatch.setenv(env, payload[key])
    monkeypatch.setenv('LUMAVUN_STATE_SOURCE', 'run:123-failed')
    with pytest.raises(ValueError):
        module.main()
    assert json.loads(path.read_text()) == state
    monkeypatch.setenv('LUMAVUN_STATE_SOURCE', 'run:123')
    module.main()
    assert json.loads(path.read_text())['quest_queue'][q['id']]['operator_history'][0]['status'] == 'PENDING_REVALIDATION'


def test_import_lock_fails_closed(tmp_path, monkeypatch):
    from state import state_lock
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'dedup_state.lock').write_text('another writer')
    with pytest.raises(TimeoutError):
        with state_lock(timeout=0, strict=True):
            pytest.fail('Must not enter unlocked transaction')
