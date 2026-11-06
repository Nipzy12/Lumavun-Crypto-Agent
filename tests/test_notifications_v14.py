"""Quest alerts and digests retain human-action boundaries."""
from main import campaign_payload
from telegram import build_opportunity_block, build_daily_summary
from quests import update_queue, queue_digest
from state import default_state
from test_quests_v14 import campaign, NOW


def test_quest_alert_shows_verified_url_cost_deadline_and_task_status():
    c = campaign()
    q = c.quest_intelligence
    state = default_state()
    update_queue(state, [q], NOW)
    text = build_opportunity_block({**campaign_payload(c), 'quest': q}, 1)
    for required in [c.title, c.official_url, 'PASS', '2026-12-01', 'NEEDS_APPROVAL', 'Join the community', 'Risiko', 'Hadiah']:
        assert required in text
    assert 'belum terverifikasi' not in text.split('Hadiah dijamin: ')[1].split('\n')[0]  # explicit not guaranteed


def test_digest_separates_actual_rewards_from_incoming_and_points():
    summary = {'new_verified_quests': 1, 'pending_approvals': 2, 'completed_tasks': 0, 'rejected_campaigns': 3, 'expired_quests': 1}
    rewards = {'actual_rewards_received': [], 'unverified_incoming_tokens': [{'amount': '999', 'asset': 'unknown'}], 'offchain_points': [{'earned': None}]}
    text = build_daily_summary('now', {}, [], [], [], quest_summary=summary, reward_summary=rewards)
    assert 'Quest baru terverifikasi: 1' in text
    assert 'menunggu persetujuan: 2' in text
    assert 'kampanye ditolak: 3' in text
    assert 'atribusi terverifikasi (kumulatif): 0' in text
    assert '999' not in text and 'USD' not in text


def test_digest_uses_completion_records():
    c = campaign()
    state = default_state()
    update_queue(state, [c.quest_intelligence], NOW)
    summary = queue_digest(state['quest_queue'])
    assert summary['pending_approvals'] == 2
    assert summary['completed_tasks'] == 0
