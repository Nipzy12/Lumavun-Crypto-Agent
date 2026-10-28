import os
import json
import re
import requests
import feedparser
from datetime import datetime, timezone

TIMEOUT = 20
FEEDS = [x.strip() for x in os.getenv('AIRDROP_RSS_FEEDS', '').split(',') if x.strip()]
WALLET = os.getenv('PHANTOM_PUBLIC_ADDRESS', '').strip()
RPC = os.getenv('SOLANA_RPC_URL', 'https://api.mainnet-beta.solana.com')
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '')

RED_FLAGS = ['deposit required', 'send funds', 'guaranteed profit', 'seed phrase', 'private key', 'connect wallet to verify']

def scan_feeds():
    findings = []
    for url in FEEDS[:10]:
        if not url.startswith('https://'):
            continue
        try:
            feed = feedparser.parse(requests.get(url, timeout=TIMEOUT).content)
            for item in feed.entries[:15]:
                title = item.get('title', 'Untitled')
                link = item.get('link', '')
                summary = re.sub(r'<[^>]+>', ' ', item.get('summary', ''))[:350]
                if not any(k in (title + ' ' + summary).lower() for k in ('airdrop', 'testnet', 'quest', 'reward')):
                    continue
                flags = [k for k in RED_FLAGS if k in (title + ' ' + summary).lower()]
                findings.append({'title': title[:100], 'url': link, 'source': url, 'flags': flags, 'status': 'REVIEW_REQUIRED'})
        except Exception as exc:
            print(f'Feed error ({url}): {type(exc).__name__}')
    return findings[:20]

def wallet_balance():
    if not WALLET:
        return None
    try:
        response = requests.post(RPC, json={'jsonrpc':'2.0','id':1,'method':'getBalance','params':[WALLET]}, timeout=TIMEOUT)
        response.raise_for_status()
        data = response.json()
        if 'error' in data:
            raise RuntimeError(data['error'].get('message', 'RPC error'))
        return data['result']['value'] / 1_000_000_000
    except Exception as exc:
        print(f'Wallet RPC error: {type(exc).__name__}')
        return None

def notify(message):
    if not BOT_TOKEN or not CHAT_ID:
        print('Telegram not configured; printing report only.')
        print(message)
        return
    url = f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage'
    response = requests.post(url, json={'chat_id':CHAT_ID,'text':message[:4000],'disable_web_page_preview':True}, timeout=TIMEOUT)
    response.raise_for_status()

def main():
    findings = scan_feeds()
    balance = wallet_balance()
    report = {'time_utc':datetime.now(timezone.utc).isoformat(), 'wallet_sol':balance, 'campaigns':findings}
    with open('latest_report.json', 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    lines = ['LUMAVUN Crypto Agent V1', f'Kampanye terdeteksi: {len(findings)}', f'Saldo SOL: {balance if balance is not None else "belum tersedia"}', 'Semua peluang wajib diverifikasi manual; tidak ada klaim otomatis.']
    for item in findings[:5]:
        lines.append(f"- {item['title']} | {item['url']}" + (' | RED FLAG' if item['flags'] else ''))
    notify('\n'.join(lines))

if __name__ == '__main__':
    main()
