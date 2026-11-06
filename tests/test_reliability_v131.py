"""V1.3.1 reliability regressions; all external services are mocked."""
import json
from pathlib import Path

import pytest

from analysis import classify_opportunity, evaluate_zero_capital
from config_loader import load_config
from discovery import Campaign, _build_campaign, campaign_id_for, deduplicate_campaigns
from sources import RawItem, SourceSpec
from telegram import build_messages
from wallet import query_token_accounts, get_sol_balance, TOKEN_PROGRAM_ID

ADDRESS = '7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU'
RPC = 'https://rpc.example.test'

@pytest.mark.parametrize('title', [
    'Airdrop Exit Strategy: How I Sell an Airdrop Without Regret',
    "OpenAI Promises to Foot AI's Power Bill After Voter Revolt",
    'Three million Paraguayans are waiting to get their money back...',
])
def test_news_false_positives_never_alert(title):
    cfg = load_config()
    spec = SourceSpec(id='news', url='https://news.example/feed', category='news')
    item = RawItem(title, 'An ordinary news article about past events and market reactions.', 'https://news.example/article', 'https://news.example/article', spec.url, spec.id)
    campaign = _build_campaign(item, spec, cfg)
    assert campaign.opportunity_type == 'NEWS_ONLY'
    assert not campaign.evidence_verified
    assert campaign.zero_capital_ok is None
    messages = build_messages('now', {}, [vars(campaign)], [], [])
    assert len(messages) == 1
    assert title not in messages[0]
    assert 'Berita ditolak: 1' in messages[0]

@pytest.mark.parametrize('text,status', [
    ('Follow the account and join Discord.', 'UNKNOWN'),
    ('', 'UNKNOWN'),
    ('Participation is free. Register to earn points.', 'PASS'),
    ('No deposit required. You must buy 10 tokens.', 'FAIL'),
    ('Participation is free, but you must stake owned tokens.', 'FAIL'),
    ('Stake 10 ETH to participate.', 'FAIL'),
    ('Trade tokens to qualify.', 'FAIL'),
    ('Pay a transaction fee to qualify.', 'FAIL'),
    ('No purchase required. Mint a token.', 'UNKNOWN'),
    ('Gas is free. Stake 10 ETH to qualify.', 'FAIL'),
])
def test_cost_states(text, status):
    assert evaluate_zero_capital('', text, load_config()).status == status


def test_official_metadata_is_not_verification():
    spec = SourceSpec(id='listing', url='https://listing.example/feed')
    item = RawItem('Meta announces Airdrop campaign', 'Register to receive 100 tokens in the confirmed airdrop campaign. Participation is free.', 'https://meta.example/claim', 'https://listing.example/post', spec.url, spec.id, official_url='https://meta.example')
    campaign = _build_campaign(item, spec, load_config())
    assert campaign.opportunity_type == 'UNVERIFIED'
    assert not campaign.evidence_verified
    assert not campaign.official_url


def test_official_source_requires_same_host_announcement():
    spec = SourceSpec(id='official', url='https://meta.example/feed', official=True)
    item = RawItem('Meta announces Airdrop campaign', 'Register to receive 100 tokens in the confirmed airdrop campaign. Participation is free.', 'https://evil.example/post', 'https://evil.example/post', spec.url, spec.id)
    assert not _build_campaign(item, spec, load_config()).evidence_verified


def test_keywords_and_task_substrings_are_not_evidence():
    result = classify_opportunity('Reward program', 'A request followed a voter revolt.', load_config(), project='Meta', official_url='https://meta.example/post', source_verified=True)
    assert result.type == 'NEWS_ONLY'


def test_canonical_identity_preserves_semantic_parameters_and_case():
    def campaign(url, title='Original'):
        return Campaign(title, 'Meta', url, 'https://news.example/feed')
    original = campaign('https://META.example:443/Campaign?b=2&a=1&utm_source=news#top')
    edited = campaign('https://meta.example/Campaign?a=1&b=2', 'Edited headline')
    assert original.campaign_id == edited.campaign_id
    assert original.campaign_id != campaign('https://meta.example/campaign?a=1&b=2').campaign_id
    assert original.campaign_id != campaign('https://meta.example/Campaign?a=2&b=2').campaign_id
    new, state, duplicates = deduplicate_campaigns([original, edited], {})
    assert len(new) == len(duplicates) == 1


def test_spl_rpc_exact_parameters_and_empty_wallet(requests_mock):
    requests_mock.post(RPC, json={'jsonrpc': '2.0', 'id': 1, 'result': {'context': {'slot': 1}, 'value': []}})
    assert query_token_accounts(ADDRESS, RPC) == ([], None)
    payload = requests_mock.last_request.json()
    assert payload['method'] == 'getTokenAccountsByOwner'
    assert payload['params'] == [ADDRESS, {'programId': 'TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA'}, {'encoding': 'jsonParsed', 'commitment': 'finalized'}]


def test_spl_valid_parsed_account(requests_mock):
    account = {'account': {'data': {'parsed': {'info': {'mint': ADDRESS, 'tokenAmount': {'amount': '1200000', 'decimals': 6, 'uiAmount': None}}}}}}
    requests_mock.post(RPC, json={'jsonrpc': '2.0', 'id': 1, 'result': {'value': [account]}})
    tokens, error = query_token_accounts(ADDRESS, RPC)
    assert error is None
    assert tokens[0]['ui_amount'] == 1.2


@pytest.mark.parametrize('body', [[], {}, {'result': None}, {'result': {'value': None}}, {'result': {'value': [{}]}}, {'error': {'message': 'Invalid params'}}])
def test_invalid_spl_response_is_unavailable(requests_mock, body):
    requests_mock.post(RPC, json=body)
    tokens, error = query_token_accounts(ADDRESS, RPC)
    assert tokens is None and error

@pytest.mark.parametrize('value', [True, -1, 'bad', {}, 1.2])
def test_invalid_sol_balance_is_unavailable(requests_mock, value):
    requests_mock.post(RPC, json={'result': {'value': value}})
    assert get_sol_balance(ADDRESS, RPC) is None


def test_sol_zero_preserved(requests_mock):
    requests_mock.post(RPC, json={'result': {'value': 0}})
    assert get_sol_balance(ADDRESS, RPC) == 0.0


def test_notification_partial_failure_retry_and_daily_dedup(monkeypatch, tmp_path):
    import main
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'test-token')
    monkeypatch.setenv('TELEGRAM_CHAT_ID', 'test-chat')
    campaigns = [Campaign('Campaign '+str(i), 'Meta', 'https://meta.example/campaign/'+str(i), 'https://meta.example/feed', evidence_verified=True, raw_summary='Open to everyone. Starts on 2020-01-01. Rewards are not guaranteed.', deadline='2099-12-01', source_verification_status='official_feed', zero_capital_reason='Explicit free participation', reward_text='Earn points for participation', tasks=['Follow the official account'], opportunity_type='POINTS_PROGRAM', zero_capital_ok=True, cost_status='PASS', official_url='https://meta.example/campaign/'+str(i)) for i in range(2)]
    monkeypatch.setattr(main, 'scan_sources', lambda *a, **kw: (campaigns, [], []))
    monkeypatch.setattr(main, 'read_wallet', lambda *a: {})
    calls = []
    def send(token, chat, messages):
        calls.append(messages)
        ok = len(calls) != 3  # summary and first opportunity succeed, second fails
        return {'sent': ok, 'messages': len(messages), 'sent_count': len(messages) if ok else 0, 'error': None if ok else 'temporary failure'}
    monkeypatch.setattr(main, 'send_messages', send)
    assert main.main() == 0
    state = json.loads(Path('dedup_state.json').read_text())
    assert campaigns[0].campaign_id in state['notifications']
    assert campaigns[1].campaign_id not in state['notifications']
    assert main.main() == 0
    assert len(calls) == 4
    assert 'Campaign 1' in calls[-1][0]
    assert main.main() == 0
    assert len(calls) == 4


def test_no_telegram_does_not_mark_delivered(monkeypatch, tmp_path):
    import main
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv('TELEGRAM_BOT_TOKEN', raising=False)
    monkeypatch.delenv('TELEGRAM_CHAT_ID', raising=False)
    campaign = Campaign('Meta Points', 'Meta', 'https://meta.example/points', 'https://meta.example/feed', evidence_verified=True, zero_capital_ok=True, opportunity_type='POINTS_PROGRAM')
    monkeypatch.setattr(main, 'scan_sources', lambda *a, **kw: ([campaign], [], []))
    monkeypatch.setattr(main, 'read_wallet', lambda *a: {})
    assert main.main() == 0
    assert json.loads(Path('dedup_state.json').read_text())['notifications'] == {}

@pytest.mark.parametrize('result', [None, {}, {'epoch': '1'}, False, 'unhealthy'])
def test_invalid_health_response_is_not_healthy(requests_mock, result):
    from wallet import check_rpc_health
    requests_mock.post(RPC, json={'result': result})
    assert check_rpc_health(RPC)[0] is False


def test_negated_free_participation_is_not_pass():
    assert evaluate_zero_capital('', 'Participation is not free to join.', load_config()).status == 'UNKNOWN'


def test_faucet_mention_does_not_prove_free_gas():
    assert evaluate_zero_capital('', 'Participation is free. Mint with the faucet.', load_config()).status == 'UNKNOWN'
