# LUMAVUN Crypto Agent V1

A zero-deposit, read-only prototype for monitoring configured RSS feeds for possible airdrop campaigns, checking a Solana wallet's SOL balance, and sending Telegram reports. **No automatic claiming, wallet signing, trades, or guaranteed rewards.**

## Setup

1. Create a new GitHub repository and upload the contents of this project, including `.github/workflows/agent.yml`.
2. Create a dedicated Phantom wallet and **copy only its public Solana address**. Never share or upload recovery phrases or private keys.
3. In GitHub repository **Settings → Secrets and variables → Actions → Variables**, add:
   - `PHANTOM_PUBLIC_ADDRESS`: public Solana address
   - `AIRDROP_RSS_FEEDS`: comma-separated HTTPS RSS/Atom feed URLs you trust and are allowed to access. Empty by default; no feed means no campaign results.
   - Optional `SOLANA_RPC_URL`: your authorized Solana JSON-RPC endpoint (defaults to public mainnet endpoint, which may rate-limit requests).
4. Create a Telegram bot with @BotFather. Message your bot, then obtain your `chat_id` from `getUpdates` on Telegram Bot API. Treat the bot token as a password.
5. In **Actions → Secrets**, add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
6. In **Actions**, enable workflows if prompted, open **LUMAVUN Crypto Agent V1**, click **Run workflow**.
7. The scheduled job runs at 01:17 and 13:17 UTC (09:17 and 21:17 WITA), subject to GitHub scheduling delays and platform limits. Download the latest-report artifact from the workflow run to inspect findings.

## Important limitations

- This is a functional **scaffold**, not a production airdrop intelligence engine. It only searches RSS feeds **you provide** using simple keyword matching; it does not discover every airdrop or verify legitimacy.
- Risk flags are heuristic and do not certify safety. Never approve wallet signatures based only on this output.
- Wallet monitor reads **native SOL balance only**, not SPL tokens, NFT holdings, or airdrop eligibility.
- The workflow may be delayed, rate-limited, disabled due to repository inactivity, or affected by provider free-tier changes.
- This version does not require an AI API; AI classification, persistent history, and broader discovery are future phases.
- Respect feed terms, platform rules, and user-consent requirements.

## Run locally

```bash
pip install -r requirements.txt
python src/main.py
```

## Security

Never commit credentials, wallet secrets, or seed phrases. Use repository secrets for Telegram credentials. Prefer a separate read-only monitoring wallet and manual approval for any wallet interaction.
