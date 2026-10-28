# LUMAVUN Crypto Agent V1.1

A zero-deposit, read-only prototype for monitoring configured RSS feeds for possible airdrop campaigns, checking a Solana wallet's SOL and SPL token balances, and sending Telegram reports.

**No automatic claiming, wallet signing, trades, or guaranteed rewards.**

## What's new in V1.1

- Modular discovery engine with vetted-sources configuration and campaign deduplication across scans.
- Transparent rule-based risk scoring with three classifications: `LOW_RISK_INDICATORS`, `REVIEW_REQUIRED`, `HIGH_RISK_INDICATORS`. Low-risk does **not** mean safe.
- Solana RPC health check, configurable fallback endpoints, SOL + read-only SPL token balance queries, and sanitized diagnostics.
- Indonesian Telegram notifications with duplicate-prevention and graceful fallback when Telegram is down.
- Persistent deduplication state via a workflow artifact (`dedup_state.json`) passed between scheduled runs.
- Two report outputs: `latest_report.json` and `latest_report.md`.
- Tests for RSS parsing, duplicate detection, scam indicators, RPC error handling, Telegram failures, and missing environment variables.

## Quick setup

1. Create or use your existing GitHub repository and push the contents of this project, including `.github/workflows/agent.yml`.
2. Create a dedicated Phantom wallet and **copy only its public Solana address**. Never share or upload recovery phrases or private keys.
3. In GitHub repository **Settings → Secrets and variables → Actions → Variables**, add:
   - `PHANTOM_PUBLIC_ADDRESS`: public Solana address of the read-only monitoring wallet.
   - `AIRDROP_RSS_FEEDS`: comma-separated HTTPS RSS/Atom feed URLs you trust and are allowed to access. Empty by default; no feed means no campaign results.
   - Optional `SOLANA_RPC_URL`: your authorized Solana JSON-RPC endpoint. Defaults to a public mainnet endpoint, which may rate-limit requests.
   - Optional `SOLANA_RPC_FALLBACK_URLS`: comma-separated fallback RPC endpoints used when the primary endpoint fails.
4. Create a Telegram bot with @BotFather. Message your bot, then obtain your `chat_id` from `getUpdates` on the Telegram Bot API. Treat the bot token as a password.
5. In **Actions → Secrets**, add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
6. Optionally, edit `config/vetted_sources.yaml` and commit it to tune default sources, keywords, and risk rules. This file is documentation for vetted sources; the agent still honors `AIRDROP_RSS_FEEDS` from Variables.
7. In **Actions**, enable workflows if prompted, open **LUMAVUN Crypto Agent V1**, and click **Run workflow**.
8. The scheduled job runs at 01:17 and 13:17 UTC (09:17 and 21:17 WITA), subject to GitHub scheduling delays and platform limits. Download the `latest_report` artifact from the workflow run to inspect `latest_report.json` and `latest_report.md`.

## Vetted sources configuration

`config/vetted_sources.yaml` documents the sources, keywords, and risk rules the agent uses when `AIRDROP_RSS_FEEDS` is not set or as a reference for operators. The agent validates every configured URL before use and skips unavailable feeds with a logged warning. It never invents campaigns or rewards.

## How discovery works

For each feed URL:

1. Validate the URL scheme and structure.
2. Fetch the feed with a timeout.
3. Parse entries and keep items that mention airdrop-related keywords.
4. Extract title, project, URL, source, publication date, requirements, and risk indicators where present.
5. Mark whether the item appears to describe confirmed campaign information or speculation.
6. Normalize and deduplicate against the previous run's state.

The agent only reports what it can extract from the feeds you provide. It does not discover every airdrop, and it does not verify legitimacy beyond heuristic flags.

## Risk scoring

Rules are transparent and listed in `config/vetted_sources.yaml` and in `src/risk.py`. Indicators include:

- Required deposits or upfront payments.
- Seed phrase or private key requests.
- Guaranteed profit claims.
- Suspicious URLs and impersonation patterns.
- Unknown or unverifiable project identity.
- Unverifiable reward promises.

Classification outcome is one of:

- `LOW_RISK_INDICATORS`
- `REVIEW_REQUIRED`
- `HIGH_RISK_INDICATORS`

A `LOW_RISK_INDICATORS` result only means fewer heuristic red flags were detected in the extracted text. It never implies guaranteed safety. Always verify manually before interacting with any wallet, signature request, or deposit.

## Phantom wallet monitor

The monitor reads balances through Solana JSON-RPC:

- Validates the public address format before querying.
- Queries native SOL balance.
- Queries read-only SPL token balances when configured.
- Uses a primary RPC endpoint with configurable fallback endpoints.
- Handles timeouts, HTTP errors, malformed responses, and explicit RPC errors.
- Shows `unavailable` when a query fails instead of reporting zero.
- Never requests, generates, or stores seed phrases, private keys, or signing credentials.

## Telegram notifications

Notifications are written in readable Indonesian and include:

- Scan timestamp and summary.
- Wallet status, including SOL balance and SPL token counts when available.
- Newly discovered campaigns with name, source, risk indicators, and verified link where available.
- Duplicate-prevention so the same campaign is not re-notified on every scheduled run.

If Telegram fails, the agent still saves `latest_report.json` and `latest_report.md` and completes other independent tasks. Bot tokens are never written to logs or reports.

## Persistent state

GitHub Actions runners are ephemeral, so the agent persists deduplication state through a workflow artifact. On each run it downloads the previous `dedup_state.json` artifact when present, updates it, and uploads the new version alongside the reports. This is a documented, conservative approach with minimal permissions and safe concurrent-run behavior.

## Reports

Each successful run produces:

- `latest_report.json`: machine-readable scan report.
- `latest_report.md`: human-readable Indonesian summary.

Reports include scan timestamps, source health, campaign findings, risk assessments, wallet information, and errors. Missing data is reported as absent, not as zero.

## Run locally

```bash
pip install -r requirements.txt
python src/main.py
```

For tests:

```bash
pip install -r requirements-dev.txt
python -m pytest tests -v
```

## GitHub Actions

The workflow keeps `workflow_dispatch` and twice-daily scheduling. It uploads reports even when a noncritical integration such as Telegram fails. It uses minimum necessary permissions, timeouts, and safe error handling.

## Security

Never commit credentials, wallet secrets, or seed phrases. Use repository secrets for Telegram credentials. Prefer a separate read-only monitoring wallet and manual approval for any wallet interaction. This version does not require an AI API; AI classification, persistent history, and broader discovery are future phases. Respect feed terms, platform rules, and user-consent requirements.

## Limitations

- This is a functional scaffold, not a production airdrop intelligence engine. It only searches RSS feeds you provide using keyword matching; it does not discover every airdrop or verify legitimacy.
- Risk flags are heuristic and do not certify safety. Never approve wallet signatures based only on this output.
- Wallet monitoring reads native SOL balance and, where supported, SPL token balances. It does not detect airdrop eligibility.
- The workflow may be delayed, rate-limited, disabled due to repository inactivity, or affected by provider free-tier changes.
- Deduplication across runs depends on the `dedup_state.json` artifact being present; the first run has no prior state.

## Local development snapshot

This repository snapshot was assembled for local editing and testing. Push it to your own GitHub repository to use the scheduled workflow.

## License

This project is provided as-is for read-only monitoring and research. No license is granted for any third-party feed content, RPC endpoints, or Telegram bots you configure.
