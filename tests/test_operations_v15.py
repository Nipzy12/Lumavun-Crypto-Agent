"""Synthetic operator transitions never represent real completed opportunities."""
from copy import deepcopy
from datetime import timedelta
import pytest
from test_qualification_v142 import qualified, NOW
from operations import sync, transition, operation_revision, playbook, save_grant_plan, migrate
from state import merge_states, save_state_locally, load_state_from_artifact
from quests import update_queue


def move(q, target, **changes):
    kwargs = dict(authorized=True, actor='fixture', reason='Synthetic recorded evidence', expected_revision=operation_revision(q), request_id='fixture-' + target, now=NOW)
    kwargs.update(changes)
    return transition(q, target, **kwargs)


def test_lifecycle_cannot_skip_stages_or_verify_reward_manually():
    q, _ = qualified()
    assert migrate(q)['state'] == 'DISCOVERED'
    sync(q, now=NOW)
    with pytest.raises(ValueError): move(q, 'IN_PROGRESS')
    for target in ('ELIGIBLE', 'READY_FOR_HUMAN', 'IN_PROGRESS'):
        move(q, target)
    with pytest.raises(ValueError): move(q, 'SUBMITTED')
    for t in q['tasks'].values():
        t['completion'] = {'kind':'human_confirmation', 'evidence':'Synthetic completion', 'at':NOW.isoformat()}
        t['approval'] = {'kind':'human_approval', 'evidence':'Synthetic approval', 'at':NOW.isoformat()}
    move(q, 'SUBMITTED')
    move(q, 'AWAITING_RESULT')
    with pytest.raises(ValueError): move(q, 'REWARD_VERIFIED')
    sync(q, [{'quest_id':q['id'], 'signature':'fixture', 'amount':'1', 'attribution':'UNVERIFIED_INCOMING'}], NOW)
    assert q['operations']['state'] == 'AWAITING_RESULT'
    sync(q, [{'quest_id':q['id'], 'signature':'fixture', 'amount':'1', 'attribution':'CONFIRMED_QUEST_REWARD'}], NOW)
    assert q['operations']['state'] == 'REWARD_VERIFIED'


@pytest.mark.parametrize('changes', [{'eligibility_status':'UNKNOWN'}, {'gas_status':'UNKNOWN'}, {'cost_status':'FAIL'}])
def test_missing_gates_cannot_be_approved(changes):
    q, _ = qualified(**changes)
    sync(q, now=NOW)
    with pytest.raises(ValueError): move(q, 'ELIGIBLE')
    p = playbook(q, NOW)
    assert p['kind'] == 'EVIDENCE_GATHERING_ONLY'
    assert all('approval_required' not in s for s in p['checklist'])


def test_authorization_conflicts_and_duplicate_decisions():
    q, _ = qualified(); sync(q, now=NOW)
    before = deepcopy(q)
    with pytest.raises(ValueError): move(q, 'ELIGIBLE', authorized=False)
    with pytest.raises(ValueError): move(q, 'ELIGIBLE', expected_revision='stale')
    assert q == before
    rev = operation_revision(q)
    first = move(q, 'ELIGIBLE', expected_revision=rev)
    assert move(q, 'ELIGIBLE', expected_revision=rev) == first
    with pytest.raises(ValueError): move(q, 'ELIGIBLE', expected_revision=rev, reason='changed')


def test_expiry_and_terms_changes_suspend_execution():
    q, _ = qualified(); sync(q, now=NOW); move(q, 'ELIGIBLE')
    q['cost_status'] = 'UNKNOWN'; sync(q, now=NOW)
    assert q['operations']['state'] == 'EVIDENCE_REVIEW'
    q['end_at'] = '2020-01-01'; sync(q, now=NOW)
    assert q['operations']['state'] == 'EXPIRED'


@pytest.mark.parametrize('target', ['WITHDRAWN','CANCELLED'])
def test_explicit_terminal_outcomes(target):
    q, _ = qualified(); sync(q, now=NOW); move(q, target)
    sync(q, now=NOW)
    assert q['operations']['state'] == target
    with pytest.raises(ValueError): move(q, 'ELIGIBLE')


def test_grant_fit_plan_and_draft_are_not_eligibility_or_income():
    q, _ = qualified(name='Developer grant', cost_status='UNKNOWN'); sync(q, now=NOW)
    plan = dict(applicant_fit_reviewed=True, technical_requirements='Synthetic requirements', proposal_draft='Draft only, no claims', deliverables='Proposed work, not completed')
    save_grant_plan(q, plan, authorized=True, actor='fixture', reason='Synthetic plan review', expected_revision=operation_revision(q), request_id='plan1', now=NOW)
    with pytest.raises(ValueError): move(q, 'ELIGIBLE')
    p = playbook(q, NOW)
    assert p['kind'] == 'EVIDENCE_GATHERING_ONLY' and p['grant_track']['budget_is_income'] is False
    assert p['grant_track']['plan']['proposal_draft'] == plan['proposal_draft']


def test_idempotent_migration_and_state_merge_preserve_progress(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    q, state = qualified(); sync(q, now=NOW); move(q, 'ELIGIBLE')
    state['quest_queue'][q['id']] = q
    migrate(q); before = deepcopy(q); migrate(q); assert before == q
    old = deepcopy(state)
    move(q, 'READY_FOR_HUMAN', now=NOW + timedelta(seconds=1))
    # A newer source snapshot must not overwrite a newer operator decision.
    old['quest_queue'][q['id']]['updated_at'] = (NOW+timedelta(hours=1)).isoformat()
    merged = merge_states(state, old)
    assert merged['quest_queue'][q['id']]['operations']['state'] == 'READY_FOR_HUMAN'
    save_state_locally(merged)
    restored = load_state_from_artifact()
    assert restored['quest_queue'][q['id']]['operation_history'] == merged['quest_queue'][q['id']]['operation_history']
    update_queue(restored, [deepcopy(before)], NOW)
    assert restored['quest_queue'][q['id']]['operations']['state'] == 'READY_FOR_HUMAN'
