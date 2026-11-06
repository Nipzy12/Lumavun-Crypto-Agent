"""Synthetic official sources; no actual quest completion or payouts."""
from copy import deepcopy
from datetime import timedelta
import json
import pytest
from test_quests_v14 import quest, NOW, TEXT
from quests import update_queue, record_task_action, review_candidate
from recovery import gap_analysis, submit_url, revalidate, recovery_report
from sources import SourceSpec, SourceResult, RawItem
from config_loader import load_config
from state import default_state, save_state_locally, load_state_from_artifact, merge_states

SPEC = SourceSpec(id='example', url='https://example.test/feed', official=True)


def setup(text=TEXT):
    state = default_state(); q = quest(text); update_queue(state, [q], NOW)
    return state, q


def result(text=TEXT, **kwargs):
    return SourceResult(spec=SPEC, items=[RawItem('Example announces Points campaign', text,
        'https://example.test/quest/1', 'https://example.test/quest/1', SPEC.url, SPEC.id)], **kwargs)


def test_gap_minimum_is_all_missing_mandatory_fields():
    state, q = setup(TEXT.replace('Participation is free.', '').replace('Open to everyone.', ''))
    gaps = gap_analysis(q, NOW)
    assert {'mandatory_fees', 'gas_costs'} <= set(gaps['public_fields'])
    assert set(gaps['participant_specific_fields']) == {'account_eligibility'}
    assert 'geographic_eligibility' in gaps['public_fields']
    assert set(gaps['minimum_missing_set']) == set(gaps['missing_fields'])
    assert gaps['freshness']['last_verified_at'] == NOW.isoformat()
    report = recovery_report(state['quest_queue'], {'actual_rewards_received':[]}, NOW)
    assert report['review_required_by_missing_field']['mandatory_fees']['count'] == 1
    assert report['awaiting_participant_verification'] == 1
    assert report['only_participant_verification_remaining'] == 0


def test_submit_url_is_only_a_request_and_deduplicates():
    state, q = setup(TEXT.replace('Participation is free.', ''))
    before = deepcopy(q['qualification'])
    a = submit_url(q, q['official_url'], NOW)
    b = submit_url(q, q['official_url'], NOW)
    assert a['id'] == b['id'] and len(q['evidence_requests']) == 1
    assert q['qualification'] == before and a['status'] == 'PENDING'


def test_unapproved_submission_makes_no_arbitrary_request():
    state, q = setup(TEXT.replace('Participation is free.', ''))
    submit_url(q, 'https://unapproved.test/page', NOW)
    calls = []
    def fetch(spec, **kw):
        calls.append(spec.url); return result(TEXT.replace('Participation is free.', ''))
    revalidate(state, [SPEC], load_config(), now=NOW, fetch=fetch)
    q = state['quest_queue'][q['id']]
    assert calls == [SPEC.url]
    assert q['evidence_requests'][0]['status'] == 'UNSUPPORTED'
    assert q['cost_status'] == 'UNKNOWN'


def test_unknown_source_never_fetched():
    state, q = setup(TEXT.replace('Participation is free.', ''))
    q['source'] = 'https://unapproved.test/private-api'
    submit_url(q, q['official_url'], NOW)
    revalidate(state, [SPEC], load_config(), now=NOW, fetch=lambda *a,**k: pytest.fail('Unauthorized request'))
    assert q['evidence_resolution']['status'] == 'UNSUPPORTED'


def test_independent_evidence_resolves_fees_but_changed_terms_require_review():
    state, q = setup(TEXT.replace('Participation is free.', ''))
    submit_url(q, q['official_url'], NOW)
    revalidate(state, [SPEC], load_config(), now=NOW, fetch=lambda *a,**k: result())
    q = state['quest_queue'][q['id']]
    assert q['qualification']['fields']['mandatory_fees']['status'] == 'PASS'
    assert q['qualification']['status'] == 'REVIEW_REQUIRED'
    assert q['terms_review_required'] and q['terms_events']
    assert q['evidence_requests'][0]['status'] == 'CHECKED'
    assert 'mandatory_fees' in q['evidence_resolution']['resolved_fields']
    review_candidate(state, q['id'], 'accept', 'Checked changed official terms', NOW)
    assert q['qualification']['status'] == 'ACTIONABLE'


def test_ambiguous_fees_remain_unknown_after_checked_request():
    text = TEXT.replace('Participation is free.', 'Gas fees may apply.')
    state, q = setup(text); submit_url(q, q['official_url'], NOW)
    revalidate(state, [SPEC], load_config(), now=NOW, fetch=lambda *a,**k: result(text))
    q = state['quest_queue'][q['id']]
    assert q['cost_status'] == 'UNKNOWN' and q['qualification']['status'] != 'ACTIONABLE'


def test_cache_and_backoff_preserve_observation_time():
    text=TEXT.replace('Participation is free.', '')
    state, q = setup(text)
    revalidate(state, [SPEC], load_config(), now=NOW, fetch=lambda *a,**k: result(text))
    checked = state['quest_queue'][q['id']]['last_observed']
    revalidate(state, [SPEC], load_config(), now=NOW+timedelta(hours=1), fetch=lambda *a,**k: pytest.fail('Cache miss'))
    q = state['quest_queue'][q['id']]
    assert q['last_observed'] == checked and q['evidence_resolution']['status'] == 'CACHED'
    later = NOW+timedelta(hours=25)
    revalidate(state, [SPEC], load_config(), now=later, fetch=lambda *a,**k: SourceResult(spec=SPEC,error='Rate limited (HTTP 429), retry-after 900s'))
    assert q['qualification']['fields']['freshness']['status'] == 'UNKNOWN'
    revalidate(state, [SPEC], load_config(), now=later+timedelta(seconds=600), fetch=lambda *a,**k: pytest.fail('Backoff violated'))
    assert q['evidence_resolution']['status'] == 'BACKOFF' and q['last_observed'] == checked


def test_missing_from_feed_is_not_withdrawn():
    state, q = setup(TEXT.replace('Participation is free.', ''))
    revalidate(state, [SPEC], load_config(), now=NOW, fetch=lambda *a,**k: SourceResult(spec=SPEC))
    assert q['evidence_resolution']['status'] == 'NOT_IN_SOURCE'
    assert q['platform_status'] != 'Deleted'


def test_explicit_withdrawal_invalidates_previous_actionable_terms():
    state, q = setup()
    incoming = quest('This campaign is withdrawn.')
    update_queue(state, [incoming], NOW+timedelta(minutes=1))
    actual=state['quest_queue'][q['id']]
    assert actual['platform_status'] == 'Deleted'
    assert actual['qualification']['status'] == 'BLOCKED'
    assert actual['terms_events'][-1]['previous_terms']['tasks']
    assert actual['task_archive']


def test_changed_reward_invalidates_approval_but_preserves_completion():
    state, q = setup()
    tid = next(iter(q['tasks']))
    record_task_action(state, q['id'], tid, 'approve', 'Approved manually', NOW)
    record_task_action(state, q['id'], tid, 'complete', 'Completed manually', NOW)
    completed = deepcopy(q['tasks'][tid]['completion'])
    incoming = quest(); incoming['reward_evidence'] = 'Now earn only 1 point'
    update_queue(state, [incoming], NOW+timedelta(minutes=1))
    q = state['quest_queue'][q['id']]
    assert q['tasks'][tid]['approval'] is None and q['tasks'][tid]['completion'] == completed
    assert q['qualification']['status'] == 'REVIEW_REQUIRED'


def test_requests_and_terms_history_survive_merge_and_artifact_restore(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    state, q = setup(TEXT.replace('Participation is free.', ''))
    old = deepcopy(state)
    submit_url(q, q['official_url'], NOW)
    revalidate(state, [SPEC], load_config(), now=NOW+timedelta(seconds=1), fetch=lambda *a,**k: result())
    save_state_locally(state)
    restored = merge_states(load_state_from_artifact(), old)
    after=restored['quest_queue'][q['id']]
    assert len(after['evidence_requests']) == 1 and after['evidence_requests'][0]['status'] == 'CHECKED'
    assert len(after['terms_events']) == 1 and after['terms_review_required']
    assert merge_states(restored, restored) == restored


def test_participant_evidence_is_not_inferred_from_silence():
    state, q = setup(TEXT.replace('Open to everyone.', 'US residents only.'))
    report = recovery_report(state['quest_queue'], {}, NOW)
    assert report['all_public_evidence_resolved'] == 1
    assert report['only_participant_verification_remaining'] == 1
    assert report['confirmed_received_rewards'] == 0
    assert report['reward_classes'] == {'POINTS_ONLY':1}


def test_revalidation_selection_does_not_fetch_other_candidates():
    state, q = setup(TEXT.replace('Participation is free.', ''))
    revalidate(state, [SPEC], load_config(), now=NOW, candidate_ids=set(), fetch=lambda *a,**kw: pytest.fail('Wrong selection'))
    assert q.get('evidence_resolution', {}) == {}


def test_readonly_review_and_submission_cli(tmp_path, monkeypatch):
    import subprocess, sys
    from pathlib import Path
    monkeypatch.chdir(tmp_path)
    state, q = setup(TEXT.replace('Participation is free.', ''))
    save_state_locally(state)
    script = Path(__file__).resolve().parents[1]/'scripts/quest_queue.py'
    before = Path('dedup_state.json').read_bytes()
    run = subprocess.run([sys.executable, str(script), 'review', '--quest', q['id']], capture_output=True, text=True)
    assert run.returncode == 0
    rows = json.loads(run.stdout)
    assert 'mandatory_fees' in rows[0]['evidence_gap']['missing_fields']
    assert Path('dedup_state.json').read_bytes() == before
    run = subprocess.run([sys.executable, str(script), 'review', '--quest', q['id'], '--submit-url', q['official_url']], capture_output=True, text=True)
    assert run.returncode == 0
    restored = load_state_from_artifact()['quest_queue'][q['id']]
    assert restored['cost_status'] == 'UNKNOWN' and restored['evidence_requests'][0]['status'] == 'PENDING'


def test_source_redirect_is_not_followed(requests_mock):
    state, q = setup(TEXT.replace('Participation is free.', ''))
    from sources import reset_network_caches
    reset_network_caches()
    requests_mock.get('https://example.test/robots.txt', text='User-agent: *\nAllow: /')
    requests_mock.get(SPEC.url, status_code=302, headers={'Location':'https://unapproved.test/private'})
    revalidate(state, [SPEC], load_config(), now=NOW)
    assert q['evidence_resolution']['status'] == 'SOURCE_UNREACHABLE'
    assert not any('unapproved' in r.url for r in requests_mock.request_history)


def test_robot_restrictions_are_honored(requests_mock):
    state, q = setup(TEXT.replace('Participation is free.', ''))
    from sources import reset_network_caches
    reset_network_caches()
    requests_mock.get('https://example.test/robots.txt', text='User-agent: *\nDisallow: /')
    revalidate(state, [SPEC], load_config(), now=NOW)
    assert q['evidence_resolution']['status'] == 'SOURCE_UNREACHABLE'
    assert all('/robots.txt' in r.url for r in requests_mock.request_history)


def test_changed_terms_keep_previous_approval_proof_for_audit():
    state, q = setup()
    tid = next(iter(q['tasks']))
    record_task_action(state, q['id'], tid, 'approve', 'Prior explicit approval', NOW)
    proof = deepcopy(q['tasks'][tid]['approval'])
    incoming = quest(); incoming['end_at'] = '2026-11-30'
    update_queue(state, [incoming], NOW+timedelta(minutes=1))
    actual = state['quest_queue'][q['id']]
    assert actual['tasks'][tid]['approval'] is None
    assert actual['terms_events'][-1]['previous_task_proofs'][tid]['approval'] == proof


def test_unknown_eligibility_policy_is_public_gap_not_proof_of_restriction():
    state, q = setup(TEXT.replace('Open to everyone.', '').replace('Solana wallet required.', ''))
    gaps = gap_analysis(q, NOW)
    assert not gaps['participant_specific_fields']
    assert {'geographic_eligibility','account_eligibility'} <= set(gaps['public_fields'])
    assert not gaps['public_evidence_resolved']


def test_malformed_optional_resolution_does_not_reset_progress(tmp_path, monkeypatch):
    from pathlib import Path
    monkeypatch.chdir(tmp_path)
    state, q = setup(TEXT.replace('Participation is free.', ''))
    tasks = deepcopy(q['tasks'])
    q['evidence_requests'] = [{'id':'bad', 'status':'PENDING'}]
    q['evidence_resolution'] = {'failures':'malformed'}
    Path('dedup_state.json').write_text(json.dumps(state))
    restored = load_state_from_artifact()
    revalidate(restored, [SPEC], load_config(), now=NOW, fetch=lambda *a,**kw: SourceResult(spec=SPEC, error='HTTP 503'))
    record = restored['quest_queue'][q['id']]
    assert record['tasks'] == tasks
    assert record['evidence_requests'] == []
    assert record['evidence_resolution']['failures'] == 1


def test_unrequested_candidates_have_explicit_resolution_status():
    state, q = setup()
    report = recovery_report(state['quest_queue'], {}, NOW)
    assert report['resolution_outcomes'] == [{'candidate_id':q['id'], 'status':'NOT_REQUESTED'}]
