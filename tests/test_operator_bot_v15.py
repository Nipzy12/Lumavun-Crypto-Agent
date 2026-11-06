"""Authenticated Telegram transport fixtures; never send real messages."""
from copy import deepcopy
import pytest
from operator_bot import poll, reply
from test_qualification_v142 import qualified, NOW
from state import merge_states, save_state_locally, load_state_from_artifact


def update(uid=10, user=123, chat=-456, text='/status', **extra):
    return {'update_id':uid,'message':{'from':{'id':user,'is_bot':False},'chat':{'id':chat},'text':text,**extra}}


class Response:
    def __init__(self,updates):self.updates=updates
    def raise_for_status(self):pass
    def json(self):return {'ok':True,'result':self.updates}


def invoke(state, updates, sent, **kwargs):
    return poll(state,token='fixture',chat='-456',allowed=kwargs.pop('allowed','123'),post=lambda *a,**k:Response(updates),send=lambda *a:sent.append(a[-1]) or {'sent':True},**kwargs)


@pytest.mark.parametrize('u',[update(user=999),update(chat=777),update(forward_origin={'type':'user'}),update(sender_chat={'id':-999})])
def test_unauthorized_commands_are_silent(u):
    state={};sent=[]
    assert invoke(state,[u],sent)['replied']==0 and not sent


def test_empty_allowlist_disables_polling():
    assert invoke({},[update()],[],allowed='')['status']=='DISABLED'


def test_repeated_updates_and_readonly_commands(tmp_path,monkeypatch):
    q,state=qualified(cost_status='UNKNOWN')
    before=deepcopy(state['quest_queue']);sent=[]
    assert invoke(state,[update(),update()],sent)['replied']==1
    assert invoke(state,[update()],sent)['replied']==0
    assert state['quest_queue']==before
    monkeypatch.chdir(tmp_path);save_state_locally(state)
    restored=load_state_from_artifact()
    assert invoke(restored,[update()],sent)['replied']==0
    assert merge_states(restored,{'telegram_operator':{'offset':1}})['telegram_operator']['offset']==11


def test_no_command_can_approve_or_leak_operator_notes():
    q,state=qualified(cost_status='UNKNOWN')
    state['quest_queue'][q['id']]['operations']={'state':'EVIDENCE_REVIEW','grant_plan':{'proposal_draft':'PRIVATE_FIXTURE'}}
    for command in ('/status','/worklist','/opportunity '+q['id'],'/rewards','/monitor','/approve '+q['id']):
        text=reply(command,state,NOW)
        assert 'PRIVATE_FIXTURE' not in text
    assert 'explicit authenticated workflow' in reply('/approve',state,NOW)
    assert state['quest_queue'][q['id']]['cost_status']=='UNKNOWN'


def test_failed_delivery_is_not_retried():
    state={};calls=[]
    def send(*args):calls.append(1);return {'sent':False}
    kwargs=dict(token='fixture',chat='-456',allowed='123',post=lambda *a,**k:Response([update()]),send=send)
    poll(state,**kwargs);poll(state,**kwargs)
    assert len(calls)==1


def test_overlapping_workers_claim_before_reply(tmp_path, monkeypatch):
    from operator_bot import claim_update
    monkeypatch.chdir(tmp_path)
    _,state=qualified(cost_status='UNKNOWN')
    stale=deepcopy(state);sent=[]
    assert invoke(state,[update()],sent,checkpoint=claim_update)['replied']==1
    assert invoke(stale,[update()],sent,checkpoint=claim_update)['replied']==0
    assert len(sent)==1


def test_failed_persistence_never_sends():
    def checkpoint(*args):raise OSError('fixture')
    sent=[]
    assert invoke({},[update()],sent,checkpoint=checkpoint)['status']=='PERSISTENCE_UNAVAILABLE'
    assert not sent
