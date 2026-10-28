# LUMAVUN Crypto Agent V1.3

A zero-capital airdrop intelligence system: it reads a registry of verified sources (RSS/Atom feeds and public JSON APIs), extracts campaigns with their evidence, classifies the opportunity, applies a zero-capital filter, scores explainable risk indicators, and sends actionable Telegram notifications. It also monitors a Phantom wallet's SOL and SPL balances read-only.

**No guaranteed earnings. No automatic claiming, wallet signing, transactions, or trades.** Every report states this in plain words.

## What's new in V1.3

- **Modular multi-source discovery.** Sources are declared in `config/vetted_sources.yaml` with a `kind` (`rss` or `json`), a parser, and a category. Adding a source is a configuration change, not a code change. The three V1.2 feeds are preserved and remain the `default_feeds` list.
- **robots.txt and rate limits.** robots.txt is fetched and honored per host before any source request, requests to the same host are spaced by a configurable interval, and a single identifying User-Agent is used everywhere. Referral and tracking parameters are stripped from every link the agent reports.
- **Opportunity classification.** Every campaign is typed as `CONFIRMED_AIRDROP`, `POINTS_PROGRAM`, `TESTNET_POTENTIAL`, `FREE_QUEST`, or `UNVERIFIED`, with the reason and the signals that produced it. Speculation never gets upgraded to fact, and a points program is never described as a guaranteed token reward.
- **Zero-capital filter.** Opportunities are prioritised by whether they need no deposit, no purchase and no paid gas. Blockers are negation-aware ("no deposit required" is not a blocker); on-chain actions with an unstated gas cost are reported as *unknown* rather than assumed free.
- **Explainable risk intelligence.** Indicators now cover seed-phrase/private-key requests, wallet-draining transaction requests, impersonation, upfront payment, guaranteed-profit claims, unverifiable rewards and shortened URLs. Each campaign gets a bounded score (0–100), a band, and a human-readable trail of which rule contributed what. Cross-source corroboration and claim-domain mismatches feed the same trail.
- **Richer Telegram notifications.** One daily summary is always sent (even with zero new campaigns), followed by a block per new opportunity containing project, title, type, official source, required actions, reward status, deadline when stated, risk indicators, and why it passes or fails the zero-capital filter. Messages are chunked under Telegram's limit and partial failures are reported.
- **Notification history in state.** `dedup_state.json` now records which campaign ids have already been sent, so a failed delivery is retried and a successful one is never repeated. Writes are atomic, a corrupt file is quarantined instead of crashing the run, and concurrent runs merge instead of overwriting each other.
- **Wallet: empty is not failed.** SPL token status now reports `ok` vs `unavailable` explicitly: a successful query returning no accounts says "0 tokens (saldo kosong, RPC berhasil)", a failed query says "tidak tersedia (RPC gagal)", and a missing address says "alamat belum diatur".
- **Source verification in CI.** `scripts/verify_sources.py` runs in the workflow and uploads `source_health.json`, so a dead source becomes visible without stopping the scan.
- **281 automated tests**, including an end-to-end run of `main()` twice against mocked HTTP that proves cross-run deduplication.

## What's new in V1.2

- Solana addresses are Base58-decoded and must be exactly 32 bytes.
- A successful `getBalance` of `0` lamports is reported as `0 SOL`, never as "unavailable".
- Three HTTP-verified default feeds, with dead/stale candidates recorded under `rejected_sources`.
- Dedup state is restored from the **previous** successful run via `gh run download` (`actions/download-artifact` defaults to the current run).
- A non-zero Python exit fails the workflow; reports still upload with `if: always()`.

## Quick setup

1. Push this repository to GitHub, including `.github/workflows/agent.yml`.
2. Create a dedicated Phantom wallet and **copy only its public address**. Never share recovery phrases or private keys.
3. In **Settings → Secrets and variables → Actions → Variables**, add:
   - `PHANTOM_PUBLIC_ADDRESS` — public Solana address of the read-only monitoring wallet.
   - `AIRDROP_RSS_FEEDS` — comma-separated HTTPS feeds you trust. **Leave empty to use the six verified V1.3 sources.** Setting it overrides the whole registry with feed transport only.
   - Optional `SOLANA_RPC_URL` and `SOLANA_RPC_FALLBACK_URLS`.
4. Create a Telegram bot with @BotFather, message it, and read your `chat_id` from `getUpdates`. Treat the token as a password.
5. In **Actions → Secrets**, add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
6. In **Actions**, open **LUMAVUN Crypto Agent V1.3** and click **Run workflow**.
7. The schedule runs at 01:17 and 13:17 UTC (09:17 and 21:17 WITA), subject to GitHub scheduling delays.

## Verified sources (V1.3)

Every URL below was requested over HTTPS with a normal `GET` during V1.3 development. The table records what was observed, and the same numbers are stored under `verified_sources` in `config/vetted_sources.yaml`.

| Source | Kind | HTTP | Format | Entries | Newest entry at check | Keyword hits | Role |
|---|---|---|---|---|---|---|---|
| `airdropalert.com/feed/rssfeed` | rss | 200 | RSS 2.0 | 100 | 2026-10-27 | 15/15 | Third-party campaign listing |
| `airdrops.io/feed/` | rss | 200 | RSS 2.0 | 10 | 2026-10-02 | 10/10 | Third-party guides/points/testnet |
| `airdropalert.com/wp-json/wp/v2/posts` | json | 200 | WP REST (JSON) | 10 | 2026-10-28 | mixed (blog feed) | Public JSON API transport |
| `decrypt.co/feed` | rss | 200 | RSS 2.0 | 35 | 2026-10-28 | 1/15 | Newsroom (kept since V1.1) |
| `blog.ethereum.org/feed.xml` | rss | 200 | RSS 2.0 | 644 | 2026-10-22 | 6/15 | Official project announcements |
| `blog.arbitrum.io/feed` | rss | 200 | RSS 2.0 | 15 | 2026-10-15 | 1/15 | Official project announcements |

Live re-verification with `python scripts/verify_sources.py` on 2026-10-28 returned `6 sources, 0 failed` with matching entry counts and content types.

**Rejected sources** (kept in `rejected_sources` so they are not re-added by guesswork) include HTTP 404s (`coinmarketcap.com/headlines/rss`, `api.llama.fi/airdrops`, `api.coingecko.com/api/v3/events`, `optimism.io/blog/rss.xml`), HTTP 410s (`airdrops.io/tag/testnet/feed/`, `airdrops.io/blog/feed/`, `airdrops.io/news/feed/`), HTTP 403 (`cryptoslate.com/feed/`), HTTP 401 (`airdrops.io/wp-json/...`), paid-only (HTTP 402, DeFiLlama emissions), stale feeds (GitHub `solana-labs/solana/releases.atom` newest 2024-10-21; reddit r/airdrops newest 2025), zero-hit feeds (cointelegraph, theblock, coindesk, ambcrypto, cryptopotato, u.today, coinjournal, dailyhodl, solana.com/news, blog.sui.io, blog.celestia.org), and 200-but-empty feed bodies (zksync, scroll, starknet, monad). Full list with observed statuses is in the config file.

Source compliance: public read-only feeds and documented public APIs only. robots.txt is honored, requests are rate-limited, tracking parameters are stripped, and no login, scraping of HTML pages, or account automation happens.

## How discovery works

For each enabled source:

1. Validate the URL (HTTPS, sane host) and check robots.txt for that host.
2. Rate-limit, then fetch with a timeout and an identifying User-Agent.
3. Parse with the source's adapter (`feed` for RSS/Atom, `wp_rest_posts` for WordPress REST JSON).
4. Keep entries that contain campaign keywords (airdrop, testnet, devnet, quest, reward, incentive, faucet, points).
5. Extract quoted evidence: title, project, announcement URL, target URL, publication date, deadline, eligibility, required tasks, reward statement, and any official project URL the source published.
6. Derive labelled heuristics: opportunity type, zero-capital result, risk score and explanation, cross-source corroboration.
7. Sort by zero-capital result first, then risk score, then recency; keep the top 20.
8. Deduplicate against persistent state using a stable campaign id.

Nothing is invented: if the payload has no deadline, the report says "tidak disebutkan sumber".

## Opportunity classification

| Type | Meaning |
|---|---|
| `CONFIRMED_AIRDROP` | The source states an airdrop **and** a confirmation signal (announced, snapshot, distribution, eligible, genesis, …). |
| `POINTS_PROGRAM` | Points, leaderboard, season, or loyalty score — **not** a token promise. |
| `TESTNET_POTENTIAL` | Testnet/devnet/node/faucet activity where a reward is possible, not certain. |
| `FREE_QUEST` | Quests or social/off-chain tasks. |
| `UNVERIFIED` | Anything else, including any entry carrying speculation words (proposed, rumored, potential, expected, reportedly, …). |

Each classification carries a reason listing the actual words that produced it.

## Zero-capital filter

Prioritised when the entry requires no deposit, no purchase, and no paid gas:

- **Blockers** (deposit, send funds, presale, entry fee, must hold, gas fee required, …) fail the filter; a blocker that the source explicitly negates ("no deposit required") is not counted.
- **On-chain actions** (bridge, swap, mint, stake, …) with no gas statement return *unknown* plus an explicit "gas cost not stated" note — unknown is never reported as free.
- **Off-chain actions** (follow, join, connect wallet, submit, check-in, …) pass.
- The agent never recommends depositing money to unlock a reward.

## Risk intelligence

Indicators: `secret_request`, `drain_request`, `deposit_required`, `guaranteed_profit`, `domain_mismatch`, `suspicious_url`, `impersonation`, `unverifiable_reward`, `unknown_project`.

Each campaign carries `risk_score` (0–100, capped), `risk_band` (`low`/`medium`/`high`), and `risk_explanation` naming each rule, its weight, and the reason (for example a claim domain that differs from the project's official domain). The tier (`LOW_RISK_INDICATORS` / `REVIEW_REQUIRED` / `HIGH_RISK_INDICATORS`) is unchanged from V1.1/V1.2.

**Risk scores are heuristic indicators, not security guarantees.** A low score means fewer red-flag phrases matched the text, nothing more.

Cross-checking: a campaign reported by two or more distinct source hosts in one run is marked `cross_checked` with the hosts named; a listing seen only once says so explicitly.

## Phantom wallet monitor

- Validates the address by Base58-decoding it and requiring exactly 32 decoded bytes.
- Reads native SOL and read-only SPL token balances; supports primary plus fallback RPC endpoints.
- Three distinct outcomes: **0 SOL / 0 tokens** (RPC succeeded, wallet is empty), **unavailable** (the request itself failed), **invalid address** (configuration error, logged loudly when `PHANTOM_PUBLIC_ADDRESS` is set but unusable).
- Never requests, generates, or stores seed phrases or private keys, and never signs anything.

## Telegram notifications

- **Daily summary (always sent):** scan time, SOL/SPL status, sources reachable, total found vs new vs duplicates, opportunity-type counts, zero-capital counts, failed sources, and errors — including when nothing new was found.
- **One block per new opportunity:** project/title, type with reason, official source, listing link, required actions, reward status with the quoted text, deadline with the quoted sentence, zero-capital verdict with reason, risk score with its trail, and cross-check status.
- Deduplication is two-layer: campaign ids in persistent state (cross-run) plus notification history (a failed send is retried, a delivered one never repeats).
- Messages are chunked below Telegram's 4096-character limit. Bot tokens never appear in logs, reports, or state files.

## Persistent state

`dedup_state.json` is written by the agent and passed between workflow runs through the `dedup_state` artifact:

- `seen`: stable campaign id → first/last sighting, title, source, type, notified flag.
- `notifications`: campaign id → delivery record (bounded to 500 entries).
- `runs`: how many runs contributed to this state.

Safety properties: writes are atomic (temp file + rename), a corrupt file is quarantined to `dedup_state.corrupt.json` instead of crashing, a lock file serializes concurrent runs, and saving **merges** with whatever is already on disk so overlapping runs cannot erase each other's history. State files are plain JSON of strings and numbers — verified by tests that assert no credential-shaped content can appear.

The workflow restores state from the newest *other* successful run with `gh run download` (verified: run 9 restored run 8's state, run 10 restored run 9's).

## Reports

- `latest_report.json` — machine-readable: source health with HTTP metadata, campaigns split into quoted evidence vs labelled heuristics, type/zero-capital/risk summaries, wallet, errors, Telegram result, state source.
- `latest_report.md` — the same content in readable Indonesian with the interpretation caveats spelled out.

Missing data is reported as absent, never as zero.

## Run locally

```bash
pip install -r requirements.txt
python src/main.py
```

Verify every configured endpoint before trusting the registry:

```bash
python scripts/verify_sources.py
```

Validate a Solana address:

```bash
python scripts/check_address.py <address>
python scripts/check_address.py --env PHANTOM_PUBLIC_ADDRESS
```

Tests:

```bash
pip install -r requirements-dev.txt
python -m pytest tests -v
```

Current suite: **281 passing tests** covering source layer (robots, rate limits, both transports, HTTP failures), analysis (classification, zero-capital, deadlines, rewards, risk scoring, cross-checking), risk tiers and their consistency with discovery, discovery and stable ids, state (corruption, concurrency, legacy V1.2 keys, notification history), notifications, wallet RPC behaviour, reports, the address-checker CLI, the workflow file structure, and an end-to-end double run of `main()`.

A local run writes `latest_report.json`, `latest_report.md`, `dedup_state.json` and `source_health.json`. All are gitignored.

## GitHub Actions

Step order: install → **pytest** → **verify sources** (non-fatal, uploads `source_health.json`) → restore dedup state (non-fatal) → **run agent** → upload reports/state/source health (`if: always()`) → summary.

Failure behaviour:

- `Run tests` and `Run agent` have **no** `continue-on-error`. A failing test or a Python exception fails the job.
- Only source verification and the state restore are allowed to fail, because a dead feed or a first-ever run are legitimate conditions — both are surfaced in the step summary instead of hidden.
- Permissions stay minimal: `contents: read`, `actions: read`.

### GitHub configuration to review

No new secrets are required for V1.3.

| Kind | Name | Required | Notes |
|---|---|---|---|
| Variable | `PHANTOM_PUBLIC_ADDRESS` | Yes | Public Solana address only; must Base58-decode to 32 bytes. |
| Variable | `AIRDROP_RSS_FEEDS` | No | **Leave empty to use the six verified V1.3 sources.** Setting it overrides the whole registry with plain feeds. |
| Variable | `SOLANA_RPC_URL` | No | Authorized RPC endpoint. |
| Variable | `SOLANA_RPC_FALLBACK_URLS` | No | Comma-separated fallbacks. |
| Secret | `TELEGRAM_BOT_TOKEN` | No | Omit to run without notifications; reports still write. |
| Secret | `TELEGRAM_CHAT_ID` | No | Same as above. |

If `AIRDROP_RSS_FEEDS` is currently set to anything, clear it (or accept that it replaces the registry).

### Action required: `PHANTOM_PUBLIC_ADDRESS` is currently invalid

As configured at the time of this release, the repository variable holds a value that is **not** a valid Solana address. It contains uppercase `I` and lowercase `l`, which do not exist in the Base58 alphabet:

```
INVALID  4GEkhEsX2ut3ymVm2lIXVhv2ZhwMlP9kpCeZqBtVUTBA
         reason: contains characters outside the Base58 alphabet: 'I' 'l'
```

Until it is replaced the report shows `Saldo SOL: tidak tersedia` with the diagnostic `Invalid Solana address format` — the wallet refusing a malformed address, not an RPC failure. Copy the address from Phantom (**Receive → account address**) and check it:

```bash
python scripts/check_address.py --env PHANTOM_PUBLIC_ADDRESS
```

The script exits `0` for a valid address. A valid address decodes to exactly 32 bytes, e.g. `7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU`.

## Verifying the first real scan

1. **Locally first:** `python scripts/verify_sources.py` must print `6 sources, 0 failed` (exit 0). If a source fails, the table shows the exact HTTP status.
2. **Locally:** `python src/main.py` — expect `Scan finished: N campaigns from 6 sources (0 errors)` and a daily summary in the console.
3. **Second local run:** `python src/main.py` again — expect `0 new, N duplicates skipped`, proving state persistence.
4. **In GitHub:** run the workflow from the **Actions** tab, then open the job summary. It reports the dedup state source, the agent outcome, source verification, sources reachable, campaign counts by type, zero-capital counts, wallet SOL, and Telegram delivery.
5. **Artifacts:** download `latest_report` (JSON + Markdown) and `source_health` from the run. Confirm `source_summary.reachable == 6` and read `opportunity_summary`.
6. **Telegram:** you should receive the daily summary first, then one block per *new* opportunity. Re-running without new campaigns must send **only** the summary.
7. **Dedup across runs:** the second successful run's summary must show `duplikat dilewati: N` equal to the first run's campaign count, and its job summary must name the previous run as the state source.

## Security

Never commit credentials, wallet secrets, or seed phrases. Use repository secrets for Telegram credentials. Prefer a separate read-only monitoring wallet and manual approval for any wallet interaction. Respect feed terms, provider terms, robots.txt, and platform rules.

## Limitations

- Heuristic engine: it reports what sources publish and classifies them with transparent rules. It does not verify legitimacy, and it does not discover every airdrop.
- `decrypt.co`, `airdropalert.com/wp-json/...` are news/blog feeds, so most of their entries are filtered out by campaign keywords.
- Risk scores and opportunity types are indicators, never guarantees. Never approve a signature based on this output.
- Wallet monitoring does not detect airdrop eligibility.
- Cross-checking only compares what a single run sees; a campaign published by one source only is never called verified.
- Dedup depends on at least one previous successful run; the first run always starts empty.
- The workflow may be delayed, rate-limited, or affected by provider free-tier changes; feed content changes over time, so re-run `scripts/verify_sources.py` periodically.

## License

Provided as-is for read-only monitoring and research. No license is granted for third-party feed content, RPC endpoints, or Telegram bots you configure.
