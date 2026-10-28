# LUMAVUN Crypto Agent V1.2

A zero-deposit, read-only prototype for monitoring configured RSS feeds for possible airdrop campaigns, checking a Solana wallet's SOL and SPL token balances, and sending Telegram reports.

**No automatic claiming, wallet signing, trades, or guaranteed rewards.**

## What's new in V1.2

- **Solana address validation is now real.** Addresses are Base58-decoded and the decoded payload must be exactly 32 bytes (Ed25519 pubkey). Previously a 32–44 character regex match was enough, which accepted strings that were not valid public keys.
- **An empty wallet is no longer confused with an RPC failure.** A successful `getBalance` response of `0` lamports is reported as `0 SOL`. `tidak tersedia` / unavailable is reserved for requests that actually failed.
- **Three verified default feeds** ship in `config/vetted_sources.yaml`, each HTTP-checked (status, content type, feed format, newest entry). Dead and stale candidates are recorded under `rejected_sources` so they are not re-added.
- **Dedup state is restored from the previous run.** `actions/download-artifact` defaults to the *current* run, so V1.1 never saw earlier state. The workflow now locates the newest previous successful run with the GitHub CLI and downloads its `dedup_state` artifact explicitly.
- **Python failures now fail the workflow.** The agent step has no `continue-on-error`; a non-zero exit fails the job. Reports and dedup state still upload on failure via `if: always()`.
- **84 automated tests**, including a structural test of the workflow file itself.

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
   - `AIRDROP_RSS_FEEDS`: comma-separated HTTPS RSS/Atom feed URLs you trust and are allowed to access. Optional in V1.2 — leave it empty to use the three verified default feeds listed below.
   - Optional `SOLANA_RPC_URL`: your authorized Solana JSON-RPC endpoint. Defaults to a public mainnet endpoint, which may rate-limit requests.
   - Optional `SOLANA_RPC_FALLBACK_URLS`: comma-separated fallback RPC endpoints used when the primary endpoint fails.
4. Create a Telegram bot with @BotFather. Message your bot, then obtain your `chat_id` from `getUpdates` on the Telegram Bot API. Treat the bot token as a password.
5. In **Actions → Secrets**, add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
6. Optionally, edit `config/vetted_sources.yaml` and commit it to tune default sources, keywords, and risk rules. This file is documentation for vetted sources; the agent still honors `AIRDROP_RSS_FEEDS` from Variables.
7. In **Actions**, enable workflows if prompted, open **LUMAVUN Crypto Agent V1**, and click **Run workflow**.
8. The scheduled job runs at 01:17 and 13:17 UTC (09:17 and 21:17 WITA), subject to GitHub scheduling delays and platform limits. Download the `latest_report` artifact from the workflow run to inspect `latest_report.json` and `latest_report.md`.

## Vetted sources configuration

`config/vetted_sources.yaml` documents the sources, keywords, and risk rules the agent uses when `AIRDROP_RSS_FEEDS` is not set or as a reference for operators. The agent validates every configured URL before use and skips unavailable feeds with a logged warning. It never invents campaigns or rewards.

### Verified default feeds (V1.2)

Each URL below was requested over HTTPS with a normal `GET` during V1.2 development. The table records what was actually observed, not what the sites claim.

| Source | HTTP | Format | Entries | Newest entry at check | Focus |
|---|---|---|---|---|---|
| `https://airdropalert.com/feed/rssfeed` | 200 | RSS 2.0 | 100 | 2026-10-27 | Dedicated airdrop campaign listings, incl. quest/task steps |
| `https://airdrops.io/feed/` | 200 | RSS 2.0 | 10 | 2026-10-02 | Airdrop guides, points programs, testnet farming |
| `https://decrypt.co/feed` | 200 | RSS 2.0 (`application/xml`) | 35 | 2026-10-28 | Crypto newsroom; covers airdrops and testnets among general news |

Two of these are airdrop-focused; `decrypt.co` is a general newsroom feed that carries airdrop and testnet coverage alongside unrelated market news, so most of its entries will not match campaign keywords.

Sources that failed verification are listed under `rejected_sources` in the same file, with the observed status, so they are not re-added by guesswork:

| Rejected source | Why |
|---|---|
| `https://coinmarketcap.com/headlines/rss/` | HTTP 404 (HTML, not a feed) |
| `https://coinmarketcap.com/community/articles/feed/` | HTTP 404 |
| `https://www.reddit.com/r/airdrops/.rss` | HTTP 200 Atom, but newest entry was from 2025 — over a year stale |
| `https://airdrops.io/tag/testnet/feed/` | HTTP 410 Gone |
| `https://airdrops.io/blog/feed/` | HTTP 410 Gone |

Feed availability and content change over time. Re-run the verification before relying on these in production.

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

- Validates the address by Base58-decoding it and requiring exactly 32 decoded bytes. A string can look plausible and still fail this check; that is the point.
- Queries native SOL balance.
- Queries read-only SPL token balances when configured.
- Uses a primary RPC endpoint with configurable fallback endpoints.
- Handles timeouts, HTTP errors, malformed responses, and explicit RPC errors.
- Reports `0 SOL` when the RPC succeeds and the account holds no lamports — an empty wallet is a real answer, not a failure.
- Reports `tidak tersedia` / unavailable only when the RPC request itself failed, so a network problem is never disguised as a zero balance.
- Never requests, generates, or stores seed phrases, private keys, or signing credentials.

## Telegram notifications

Notifications are written in readable Indonesian and include:

- Scan timestamp and summary.
- Wallet status, including SOL balance and SPL token counts when available.
- Newly discovered campaigns with name, source, risk indicators, and verified link where available.
- Duplicate-prevention so the same campaign is not re-notified on every scheduled run.

If Telegram fails, the agent still saves `latest_report.json` and `latest_report.md` and completes other independent tasks. Bot tokens are never written to logs or reports.

## Persistent state

GitHub Actions runners are ephemeral, so the agent persists deduplication state through a workflow artifact.

**How it works in V1.2.** `actions/download-artifact` reads artifacts from the *current* run unless you pass an explicit `run-id`. In V1.1 it was used without one, so it found nothing and every run started from an empty state — dedup never actually worked across runs. The workflow now:

1. Lists the most recent successful runs of `agent.yml` via the GitHub CLI.
2. Excludes the current run ID.
3. Downloads the newest other run's `dedup_state` artifact into the workspace.

On a first run (no earlier success) it logs that state starts empty and continues. The restore step is non-fatal, so a missing artifact never blocks the scan. Verified locally with two back-to-back runs:

| Run | Campaigns reported | Duplicates skipped | State entries |
|---|---|---|---|
| 1 (no prior state) | 20 | 0 | 20 |
| 2 (state from run 1) | 0 | 20 | 20 |

The `concurrency` group keeps scheduled runs from overlapping, since two concurrent runs would race on the same artifact.

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

Current suite: **94 passing tests** across wallet validation, zero-balance handling, feed discovery, dedup across two runs, state persistence, Telegram message building, config, reports, the address-checker CLI, and the workflow file structure.

Validate a Solana address at any time:

```bash
python scripts/check_address.py <address>
python scripts/check_address.py --env PHANTOM_PUBLIC_ADDRESS
```

A local run writes `latest_report.json`, `latest_report.md`, and `dedup_state.json` into the working directory. All three are gitignored; they are upload artifacts, not source.

## GitHub Actions

The workflow keeps `workflow_dispatch` and twice-daily scheduling. Step order is: install → **pytest** → restore dedup state → run agent → upload reports → upload state → summary.

Failure behaviour is deliberate:

- The `Run agent` step has **no** `continue-on-error`. A non-zero Python exit fails the job, so a broken scan shows red instead of green.
- The `Run tests` step has no `continue-on-error` either; a failing test blocks the scan.
- Only the **state restore** step is allowed to fail, because the first run legitimately has no prior artifact.
- Reports and `dedup_state.json` upload with `if: always()`, so a failed run still leaves diagnostics behind.

Permissions stay minimal (`contents: read`, `actions: read`). `actions: read` is required for the state restore to query previous runs.

### GitHub configuration to review

No new secrets are needed for V1.2. Confirm these exist under **Settings → Secrets and variables → Actions**:

| Kind | Name | Required | Notes |
|---|---|---|---|
| Variable | `PHANTOM_PUBLIC_ADDRESS` | Yes | Public Solana address only. Must Base58-decode to 32 bytes; a malformed value now reports `Invalid Solana address format` instead of silently querying. |
| Variable | `AIRDROP_RSS_FEEDS` | No | Comma-separated HTTPS feeds. **Leave empty to use the three verified defaults.** Setting it overrides them. |
| Variable | `SOLANA_RPC_URL` | No | Authorized RPC endpoint. Public mainnet is rate-limited. |
| Variable | `SOLANA_RPC_FALLBACK_URLS` | No | Comma-separated fallbacks. |
| Secret | `TELEGRAM_BOT_TOKEN` | No | Omit to run without notifications; reports still write. |
| Secret | `TELEGRAM_CHAT_ID` | No | Same as above. |

No new permissions or workflow settings need enabling. If the repository previously had `AIRDROP_RSS_FEEDS` set to `[]` or to the old empty behaviour, clear it so the verified defaults apply.

### Action required: `PHANTOM_PUBLIC_ADDRESS` is currently invalid

As configured at the time of this release, the repository variable holds a value that is **not** a valid Solana address. It contains uppercase `I` and lowercase `l`, neither of which exists in the Base58 alphabet, so it can never be a public key:

```
INVALID  4GEkhEsX2ut3ymVm2lIXVhv2ZhwMlP9kpCeZqBtVUTBA
         reason: contains characters outside the Base58 alphabet: 'I' 'l'
```

Until this is replaced, the report and Telegram message will show:

```
Saldo SOL: tidak tersedia
```

with the diagnostic `Invalid Solana address format`. That is the wallet refusing a malformed address, not an RPC failure.

Copy the address straight from Phantom (**Receive → your account address**) and validate it before saving:

```bash
python scripts/check_address.py 4GEkhEsX2ut3ymVm2lIXVhv2ZhwMlP9kpCeZqBtVUTBA
python scripts/check_address.py --env PHANTOM_PUBLIC_ADDRESS
```

The script exits `0` for a valid address and prints the exact reason otherwise. A valid address decodes to exactly 32 bytes, e.g. `7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU`.

## Security

Never commit credentials, wallet secrets, or seed phrases. Use repository secrets for Telegram credentials. Prefer a separate read-only monitoring wallet and manual approval for any wallet interaction. This version does not require an AI API; AI classification, persistent history, and broader discovery are future phases. Respect feed terms, platform rules, and user-consent requirements.

## Limitations

- This is a functional scaffold, not a production airdrop intelligence engine. It only searches RSS feeds you provide using keyword matching; it does not discover every airdrop or verify legitimacy.
- Risk flags are heuristic and do not certify safety. Never approve wallet signatures based only on this output.
- Wallet monitoring reads native SOL balance and, where supported, SPL token balances. It does not detect airdrop eligibility. A successful RPC call that returns zero lamports is shown as 0 SOL; "unavailable" is used only when the RPC request itself fails.
- Address validation now checks Base58 decoding and a 32-byte decoded length; typos and wrong-length public addresses are rejected.
- The workflow may be delayed, rate-limited, disabled due to repository inactivity, or affected by provider free-tier changes.
- Deduplication across runs depends on the `dedup_state.json` artifact being present from a previous run; the first run has no prior state.
- Feed content changes over time; periodically re-verify default feed URLs.

## Local development snapshot

This repository snapshot was assembled for local editing and testing. Push it to your own GitHub repository to use the scheduled workflow.

## License

This project is provided as-is for read-only monitoring and research. No license is granted for any third-party feed content, RPC endpoints, or Telegram bots you configure.
