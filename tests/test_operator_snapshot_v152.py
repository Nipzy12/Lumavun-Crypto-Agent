import json
from copy import deepcopy
from datetime import datetime, timezone
from operator_snapshot import export_snapshot
from test_qualification_v142 import qualified, NOW
from operator_bot import poll


def test_projection_excludes_sensitive_fields_and_url_queries():
    q,_=qualified();q['official_url']='https://example.org/campaign?credential=PRIVATE'
    q.update(human_review={'evidence':'PRIVATE'},operations={'state':'EVIDENCE_REVIEW','grant_plan':{'proposal_draft':'PRIVATE'}},name='PRIVATE',wallet_requirements=['PRIVATE'])
    report={'scan_time_utc':NOW.isoformat(),'quest_queue':{q['id']:q},'credentials':'PRIVATE','wallet':{'address':'PRIVATE'},'reward_tracking':{'monitor':{'status':'RPC_UNAVAILABLE','gaps':['PRIVATE','CURSOR_NOT_FOUND'],'provider':'PRIVATE'},'actual_rewards_received':[]}}
    before=deepcopy(report);s=export_snapshot(report)
    assert len(s['opportunities']) == 1
    assert s['opportunities'][0]['official_url'] == 'https://example.org/campaign'
    assert 'PRIVATE' not in json.dumps(s) and report==before
    assert s['generated_at']==NOW.isoformat()
    assert s['monitor']['gaps']==['CURSOR_NOT_FOUND']


def test_export_uses_observation_time_not_publication_time():
    s=export_snapshot({'scan_time_utc':'2020-01-01T00:00:00Z'})
    assert s['generated_at'].startswith('2020-')


def test_polling_requires_explicit_verified_cutover(monkeypatch):
    monkeypatch.setenv('TELEGRAM_OPERATOR_MODE','webhook')
    monkeypatch.delenv('TELEGRAM_WEBHOOK_VERIFIED',raising=False)
    assert poll({},token='',allowed='')['status']=='DISABLED'
    monkeypatch.setenv('TELEGRAM_WEBHOOK_VERIFIED','true')
    assert poll({},token='',allowed='')['status']=='WEBHOOK_MODE'
    monkeypatch.setenv('TELEGRAM_OPERATOR_MODE','polling')
    assert poll({},token='',allowed='')['status']=='DISABLED'


def test_cutover_acknowledges_only_saved_offset_and_never_disables_polling(tmp_path,monkeypatch,requests_mock):
    import importlib.util
    from pathlib import Path
    script=Path(__file__).resolve().parents[1]/'scripts/manage_operator_webhook.py'
    spec=importlib.util.spec_from_file_location('manage_fixture',script);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    monkeypatch.chdir(tmp_path);Path('dedup_state.json').write_text(json.dumps({'telegram_operator':{'offset':41}}))
    for k,v in {'TELEGRAM_BOT_TOKEN':'fixture','OPERATOR_WORKER_URL':'https://worker.example','TELEGRAM_WEBHOOK_SECRET':'a'*40,'OPERATOR_SNAPSHOT_WRITE_TOKEN':'b'*40}.items():monkeypatch.setenv(k,v)
    monkeypatch.delenv('TELEGRAM_OPERATOR_MODE',raising=False);monkeypatch.delenv('TELEGRAM_WEBHOOK_VERIFIED',raising=False)
    monkeypatch.setattr('sys.argv',['manage','set','--operators-paused'])
    base='https://api.telegram.org/botfixture/'
    requests_mock.post(base+'getWebhookInfo',[{'json':{'ok':True,'result':{'url':''}}},{'json':{'ok':True,'result':{'url':'https://worker.example/telegram'}}}])
    requests_mock.get('https://worker.example/health',json={'stale':False})
    ack=requests_mock.post(base+'getUpdates',json={'ok':True,'result':[]})
    set_hook=requests_mock.post(base+'setWebhook',json={'ok':True,'result':True})
    module.main()
    assert ack.last_request.json()['offset']==41
    assert set_hook.last_request.json()['drop_pending_updates'] is False
    import os
    assert 'TELEGRAM_OPERATOR_MODE' not in os.environ and 'TELEGRAM_WEBHOOK_VERIFIED' not in os.environ


def test_rollback_refuses_pending_messages(monkeypatch,requests_mock):
    import importlib.util
    import pytest
    from pathlib import Path
    spec=importlib.util.spec_from_file_location('rollback_fixture',Path(__file__).resolve().parents[1]/'scripts/manage_operator_webhook.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN','fixture');monkeypatch.setattr('sys.argv',['manage','delete','--operators-paused'])
    requests_mock.post('https://api.telegram.org/botfixture/getWebhookInfo',json={'ok':True,'result':{'url':'https://worker.example/telegram','pending_update_count':3}})
    with pytest.raises(SystemExit,match='Drain pending'):module.main()
    assert requests_mock.call_count==1
