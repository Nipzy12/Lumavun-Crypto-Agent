"""Mocked finalized transactions verify attribution, never actual earnings."""
from copy import deepcopy
from decimal import Decimal
import pytest
from rewards import transaction_changes, recent_transfers, update_rewards
from state import default_state, save_state_locally, load_state_from_artifact, merge_states

ADDRESS = '7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU'
OTHER = '11111111111111111111111111111111'
MINT = 'So11111111111111111111111111111111111111112'
RPC = 'https://rpc.example.test'
SIG = 'synthetic-transaction-signature'


def transaction():
    def balance(amount):
        return {'accountIndex': 1, 'owner': ADDRESS, 'mint': MINT, 'uiTokenAmount': {'amount': str(amount), 'decimals': 6}}
    return {'slot': 123, 'blockTime': 1793952000,
            'transaction': {'signatures': [SIG], 'message': {'accountKeys': [{'pubkey': ADDRESS}, {'pubkey': 'token-account'}]}},
            'meta': {'err': None, 'preBalances': [1000000000, 0], 'postBalances': [2000000000, 0],
                     'preTokenBalances': [balance(0)], 'postTokenBalances': [balance(2500000)]}}


def wallet(sol=0, amount=0):
    return {'address_valid': True, 'sol_balance': sol, 'spl_status': 'ok', 'spl_token_count': 1,
            'spl_tokens': [{'mint': MINT, 'raw_balance': str(amount), 'decimals': 6}], 'used_rpc': RPC}


def no_transfers(*args):
    return [], [], []


def received(*args):
    return transaction_changes(transaction(), ADDRESS, SIG), [SIG], []


def test_finalized_token_and_sol_receipts_are_unattributed():
    events = transaction_changes(transaction(), ADDRESS, SIG)
    assert {e['asset'] for e in events} == {'SOL', MINT}
    assert {e['amount'] for e in events} == {'1', '2.5'}
    assert all(e['signature'] == SIG and e['attribution'] == 'UNVERIFIED_INCOMING' for e in events)
    assert transaction_changes(transaction(), OTHER, SIG) == []


def test_failed_transaction_not_received():
    tx = transaction()
    tx['meta']['err'] = {'InstructionError': [0, 'Failed']}
    assert transaction_changes(tx, ADDRESS, SIG) == []

@pytest.mark.parametrize('change', ['missing_meta', 'wrong_signature', 'bad_sol', 'bad_token'])
def test_invalid_transactions_rejected(change):
    tx = transaction()
    if change == 'missing_meta':
        tx.pop('meta')
    elif change == 'wrong_signature':
        tx['transaction']['signatures'] = ['wrong']
    elif change == 'bad_sol':
        tx['meta']['postBalances'] = []
    else:
        tx['meta']['postTokenBalances'][0]['uiTokenAmount']['amount'] = 'NaN'
    with pytest.raises(ValueError):
        transaction_changes(tx, ADDRESS, SIG)


def test_rpc_methods_and_finality(requests_mock):
    requests_mock.post(RPC, [
        {'json': {'result': [{'signature': SIG, 'err': None, 'confirmationStatus': 'finalized'}]}},
        {'json': {'result': transaction()}},
    ])
    events, signatures, errors = recent_transfers(ADDRESS, RPC, set())
    assert len(events) == 2 and signatures == [SIG] and not errors
    calls = [r.json() for r in requests_mock.request_history]
    assert [c['method'] for c in calls] == ['getSignaturesForAddress', 'getTransaction']
    assert all(c['params'][-1]['commitment'] == 'finalized' for c in calls)
    assert calls[1]['params'][1]['maxSupportedTransactionVersion'] == 0


def test_seen_signature_not_requested_again(requests_mock):
    requests_mock.post(RPC, json={'result': [{'signature': SIG, 'err': None, 'confirmationStatus': 'finalized'}]})
    assert recent_transfers(ADDRESS, RPC, {SIG}) == ([], [], [])
    assert requests_mock.call_count == 1


def test_unfinalized_not_recorded(requests_mock):
    requests_mock.post(RPC, json={'result': [{'signature': SIG, 'err': None, 'confirmationStatus': 'confirmed'}]})
    assert recent_transfers(ADDRESS, RPC, set()) == ([], [], [])

@pytest.mark.parametrize('body', [{}, {'result': None}, {'result': [{}]}, {'error': {'message': 'rate limit'}}])
def test_rpc_failure_is_not_zero_or_reward(requests_mock, body):
    requests_mock.post(RPC, json=body)
    events, signatures, errors = recent_transfers(ADDRESS, RPC, set())
    assert not events and not signatures and errors


def test_first_snapshot_is_baseline_not_income():
    state = default_state()
    result = update_rewards(state, wallet(4, 9000000), ADDRESS, fetch=no_transfers)
    assert result['balance_changes'] == []
    assert result['actual_rewards_received'] == []


def test_confirmed_balance_delta_and_repeated_snapshot():
    state = default_state()
    update_rewards(state, wallet(1, 0), ADDRESS, fetch=no_transfers)
    result = update_rewards(state, wallet(2, 2500000), ADDRESS, fetch=no_transfers)
    assert len(result['balance_changes']) == 2
    assert result['actual_rewards_received'] == []
    assert update_rewards(state, wallet(2, 2500000), ADDRESS, fetch=no_transfers)['balance_changes'] == []


def test_rpc_failure_does_not_reset_balance_baseline():
    state = default_state()
    update_rewards(state, wallet(2, 2500000), ADDRESS, fetch=no_transfers)
    failed = {'sol_balance': None, 'spl_status': 'unavailable'}
    assert update_rewards(state, failed, ADDRESS, fetch=no_transfers)['balance_changes'] == []
    assert update_rewards(state, wallet(2, 2500000), ADDRESS, fetch=no_transfers)['balance_changes'] == []


def test_new_wallet_has_independent_baseline():
    state = default_state()
    update_rewards(state, wallet(5, 5000000), ADDRESS, fetch=no_transfers)
    assert update_rewards(state, wallet(1, 0), OTHER, fetch=no_transfers)['balance_changes'] == []
    assert len(state['reward_wallets']) == 2


def test_incoming_token_not_assumed_airdrop_and_no_usd():
    result = update_rewards(default_state(), wallet(2, 2500000), ADDRESS, fetch=received)
    assert len(result['unverified_incoming_tokens']) == 2
    assert result['actual_rewards_received'] == []
    assert 'usd' not in str(result).lower()


def test_trusted_token_is_received_but_not_attributed():
    result = update_rewards(default_state(), wallet(2, 2500000), ADDRESS, config={'trusted_mints': [MINT]}, fetch=received)
    assert len(result['confirmed_received_tokens']) == 1
    assert result['actual_rewards_received'] == []


def test_explicit_attribution_requires_actual_transfer():
    state = default_state()
    state['quest_queue']['quest-1'] = {'id': 'quest-1', 'evidence_verified': True, 'reward_type': 'TOKEN', 'reward_status': 'stated'}
    config = {'attributions': [{'quest_id': 'quest-1', 'signature': SIG, 'asset': MINT, 'evidence_url': 'https://example.test/reward-proof', 'reviewed': True}]}
    assert update_rewards(state, wallet(), ADDRESS, config=config, fetch=no_transfers)['actual_rewards_received'] == []
    result = update_rewards(state, wallet(2, 2500000), ADDRESS, config=config, fetch=received)
    assert len(result['actual_rewards_received']) == 1
    assert result['actual_rewards_received'][0]['amount'] == '2.5'
    assert len(update_rewards(state, wallet(2, 2500000), ADDRESS, config=config, fetch=received)['actual_rewards_received']) == 1


def test_offchain_points_are_advertised_not_earned():
    state = default_state()
    state['quest_queue']['points'] = {'id': 'points', 'reward_type': 'OFFCHAIN_POINTS', 'reward_status': 'points_only'}
    result = update_rewards(state, wallet(), ADDRESS, fetch=no_transfers)
    assert result['offchain_points'][0]['earned'] is None
    assert result['actual_rewards_received'] == []


def test_reward_state_survives_persistence(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    state = default_state()
    update_rewards(state, wallet(), ADDRESS, fetch=received)
    save_state_locally(state)
    restored = load_state_from_artifact()
    assert restored['reward_wallets'] == state['reward_wallets']
    merged = merge_states(restored, deepcopy(state))
    assert merged['reward_wallets'] == state['reward_wallets']


def test_truncated_display_wallet_does_not_zero_omitted_tokens():
    state = default_state()
    update_rewards(state, wallet(1, 1000000), ADDRESS, fetch=no_transfers)
    incomplete = wallet(1, 0)
    incomplete['spl_token_count'] = 100
    incomplete['spl_tokens'] = []
    assert update_rewards(state, incomplete, ADDRESS, fetch=no_transfers)['balance_changes'] == []


def test_transaction_rate_limit_stops_remaining_requests(requests_mock):
    entries = [{'signature': f'synthetic-{i}', 'err': None, 'confirmationStatus': 'finalized'} for i in range(3)]
    requests_mock.post(RPC, [{'json': {'result': entries}}, {'status_code': 429}])
    records, processed, errors = recent_transfers(ADDRESS, RPC, set())
    assert not records and not processed and errors
    assert requests_mock.call_count == 2
