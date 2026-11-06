"""Deterministic RPC fixtures; balances and receipts are synthetic."""
from datetime import timedelta
from copy import deepcopy
from test_rewards_v14 import ADDRESS, RPC, SIG, transaction
from test_qualification_v142 import NOW
from reward_monitor import reconcile
from state import merge_states


def row(sig=SIG):
    return {'signature':sig, 'err':None, 'confirmationStatus':'finalized', 'slot':123}


def invoke(tracker, rpc, **kwargs):
    return reconcile(ADDRESS, [RPC], tracker, rpc=rpc, sleep=lambda _:None, now=kwargs.pop('now', NOW), **kwargs)


def test_zero_transfers_is_distinct_from_outage_and_backoff():
    tracker = {'signatures':{}}
    assert invoke(tracker, lambda *a,**k: {'result':[]})[3]['status'] == 'ZERO_TRANSFERS'
    before = deepcopy(tracker['monitor'])
    calls = []
    def fail(*a,**k): calls.append(1); raise RuntimeError('fixture outage')
    result = invoke(tracker, fail)
    assert result[3]['status'] == 'RPC_UNAVAILABLE'
    assert result[3]['last_success_at'] == before['last_success_at']
    assert len(calls) == 2
    assert invoke(tracker, fail)[3]['status'] == 'BACKOFF' and len(calls) == 2
    assert invoke(tracker, lambda *a,**k:{'result':[]}, now=NOW+timedelta(minutes=2))[3]['status'] == 'ZERO_TRANSFERS'


def test_second_trusted_provider_and_transaction_deduplication():
    tracker = {'signatures':{}}
    calls = []
    def rpc(url,payload,**kwargs):
        calls.append(url)
        if url == RPC: raise RuntimeError('fixture primary outage')
        return {'result':[row()] if payload['method']=='getSignaturesForAddress' else transaction()}
    events,done,errors,monitor = reconcile(ADDRESS,[RPC,'https://second.example.test'],tracker,rpc=rpc,sleep=lambda _:None,now=NOW)
    assert len(events)==2 and done==[SIG] and not errors and monitor['cursor']==SIG
    assert monitor['provider_number']==2 and monitor['configured_provider_count']==2
    tracker['signatures'][SIG]=NOW.isoformat()
    events,done,errors,monitor = reconcile(ADDRESS,[RPC,'https://second.example.test'],tracker,rpc=rpc,sleep=lambda _:None,now=NOW)
    assert not events and not done and not errors


def test_transaction_failure_preserves_cursor_and_pending_then_recovers():
    tracker = {'signatures':{'old':'timestamp'}, 'monitor':{'cursor':'old'}}
    def rpc(url,payload,**kwargs):
        if payload['method']=='getTransaction': raise RuntimeError('fixture unavailable tx')
        return {'result':[row(),row('old')]}
    result = invoke(tracker,rpc)
    assert result[3]['cursor']=='old' and SIG in result[3]['pending']
    result = invoke(tracker,lambda *a,**k:{'result':transaction()},now=NOW+timedelta(minutes=2))
    assert result[3]['cursor']==SIG and not result[3]['pending'] and result[1]==[SIG]


def test_bounded_pagination_resumes_without_losing_history():
    tracker = {'signatures':{}}
    def rpc(url,payload,**kwargs):
        if payload['method']=='getTransaction': return {'result':transaction()}
        return {'result':[] if payload['params'][1].get('before') else [row()]}
    first = invoke(tracker,rpc,page_size=1,max_pages=1)
    assert first[3]['status']=='PARTIAL' and first[3]['cursor'] is None and first[3]['before']==SIG
    tracker['signatures'][SIG]=NOW.isoformat()
    second = invoke(tracker,rpc,page_size=1,max_pages=1)
    assert second[3]['status']=='OK' and second[3]['cursor']==SIG
    assert second[3]['scope_gaps']==['TOKEN_ACCOUNT_HISTORY_NOT_ENUMERATED']


def test_unfinalized_or_slot_mismatch_never_attributed():
    for bad in ('finality','slot'):
        tracker={'signatures':{}}
        def rpc(url,payload,**kwargs):
            if payload['method']=='getSignaturesForAddress':
                entry=row()
                if bad=='finality':entry['confirmationStatus']='confirmed'
                return {'result':[entry]}
            tx=transaction();tx['slot']=999;return {'result':tx}
        result=invoke(tracker,rpc)
        assert not result[0] and not result[1] and result[3]['cursor'] is None and result[2]


def test_monitor_cursor_survives_state_merge():
    base={'reward_wallets':{'owner':{'monitor':{'cursor':'old','updated_at':NOW.isoformat()}}}}
    new=deepcopy(base);new['reward_wallets']['owner']['monitor'].update(cursor='new',updated_at=(NOW+timedelta(seconds=1)).isoformat())
    assert merge_states(new,base)['reward_wallets']['owner']['monitor']['cursor']=='new'


def test_unsafe_provider_is_never_contacted(monkeypatch):
    monkeypatch.setattr('reward_monitor.check_source_destination',lambda url:False)
    result=invoke({},lambda *a,**k: (_ for _ in ()).throw(AssertionError('Unsafe request')))
    assert result[3]['configured_provider_count']==0 and result[3]['status']=='RPC_UNAVAILABLE'


def test_malformed_optional_monitor_state_preserves_cursor():
    tracker={'signatures':{},'monitor':{'cursor':'last-good','pending':{'bad':None}}}
    result=invoke(tracker,lambda *a,**k: (_ for _ in ()).throw(AssertionError('No unsafe resumption')))
    assert result[3]['cursor']=='last-good' and result[3]['status']=='INVALID_STATE'


def test_incomplete_spl_evidence_is_not_zero_tokens():
    def rpc(url,payload,**kwargs):
        if payload['method']=='getSignaturesForAddress':return {'result':[row()]}
        tx=transaction();del tx['meta']['postTokenBalances'];return {'result':tx}
    result=invoke({},rpc)
    assert not result[0] and not result[1] and result[3]['cursor'] is None


def test_request_budget_reports_gap_without_advancing_cursor():
    result=invoke({},lambda *a,**k:{'result':[row()]},request_budget=1)
    assert result[3]['status']=='PARTIAL' and result[3]['gaps']==['REQUEST_BUDGET_REACHED']
    assert result[3]['cursor'] is None and SIG in result[3]['pending']
