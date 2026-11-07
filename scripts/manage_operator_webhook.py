#!/usr/bin/env python3
"""Explicit Telegram webhook administration; never logs credentials or raw API errors."""
import argparse
import json
import os
from pathlib import Path
import requests
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from source_security import check_source_destination


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['set','status','delete'])
    parser.add_argument('--state-file',default='dedup_state.json')
    parser.add_argument('--operators-paused',action='store_true')
    args=parser.parse_args()
    token=os.environ['TELEGRAM_BOT_TOKEN']
    base=os.environ.get('OPERATOR_WORKER_URL','').rstrip('/')
    def telegram(method,payload=None):
        try:
            r=requests.post(f'https://api.telegram.org/bot{token}/{method}',json=payload or {},timeout=15,allow_redirects=False)
            body=r.json()
            if r.status_code!=200 or body.get('ok') is not True:raise ValueError('API rejected request')
            return body.get('result')
        except (requests.RequestException,ValueError,AttributeError):
            raise SystemExit('Telegram administration failed; credentials and API body withheld') from None
    info=telegram('getWebhookInfo')
    if args.action=='status':
        print(json.dumps({'configured':bool(info.get('url')),'matches_worker':info.get('url')==base+'/telegram','pending_updates':info.get('pending_update_count'),'delivery_error_present':bool(info.get('last_error_date'))}))
        return
    if not args.operators_paused:raise SystemExit('Pause operator commands during cutover/rollback and pass --operators-paused')
    if args.action=='delete':
        if info.get('pending_update_count',0)!=0:raise SystemExit('Drain pending webhook updates before rollback; do not discard messages')
        telegram('deleteWebhook',{'drop_pending_updates':False})
        if telegram('getWebhookInfo').get('url'):raise SystemExit('Webhook deletion not verified')
        print('Webhook removed. Re-enable polling mode; retain polling state and Worker replay claims.')
        return
    if not check_source_destination(base):raise SystemExit('Valid public HTTPS Worker URL required')
    secret=os.environ['TELEGRAM_WEBHOOK_SECRET'];write=os.environ['OPERATOR_SNAPSHOT_WRITE_TOKEN']
    if len(secret)<32:raise SystemExit('Webhook secret must have at least 32 characters')
    try:
        health=requests.get(base+'/health',headers={'Authorization':'Bearer '+write},timeout=10,allow_redirects=False)
        health.raise_for_status();data=health.json()
        if data.get('stale') is not False:raise ValueError('Snapshot absent or stale')
    except (requests.RequestException,ValueError):raise SystemExit('Worker freshness verification failed') from None
    if not info.get('url'):
        state=json.loads(Path(args.state_file).read_text())
        offset=state.get('telegram_operator',{}).get('offset')
        if offset is not None:
            if type(offset) is not int or offset<0:raise SystemExit('Invalid restored polling offset')
            # Acknowledge only updates already claimed by the polling worker.
            telegram('getUpdates',{'offset':offset,'limit':1,'timeout':0,'allowed_updates':['message']})
    elif info['url']!=base+'/telegram':raise SystemExit('Another webhook exists; review it before replacing')
    telegram('setWebhook',{'url':base+'/telegram','secret_token':secret,'allowed_updates':['message'],'drop_pending_updates':False,'max_connections':4})
    if telegram('getWebhookInfo').get('url')!=base+'/telegram':raise SystemExit('Webhook URL verification failed')
    print('Webhook registration verified. NOT live-verified: send a real allowlisted command, observe its reply and inspect authenticated /health before disabling polling.')


if __name__=='__main__':main()
