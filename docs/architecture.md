# LUMAVUN V1.5 architecture

## Data flow

```text
Existing RSS / public JSON sources ──> V1.3.1 evidence classifier
Reviewed official feeds ────────────> same classifier
Authorized Galxe query ─────────────> validated structured evidence
Manual official URL selection ─────> corroboration against observed official feed
                                      │
                                      v
                                Quest intelligence
                                      │
                          Eligibility + cost + expiry gates
                                      │
                                      v
                       Persistent quest / per-task queue
                                      │
                           Telegram alert + daily digest

Finalized Solana SOL/SPL observations ──> wallet-scoped baseline changes
Finalized signatures + transactions ───> transaction balance changes
Reviewed receipt attribution ──────────> confirmed quest reward (only with receipt)
```

All persistence uses the existing `dedup_state.json` artifact, atomic replacement and lock. V1.3 records migrate additively to schema 1.4; `seen`, `notifications`, and `runs` remain compatible. `quest_queue` and `reward_wallets` are explicit, validated extensions. Partial malformed extensions are discarded rather than interpreted as completed tasks or received rewards. Entire invalid JSON is quarantined by the existing loader.

## Modules

- `sources.py`, `analysis.py`, `discovery.py`: retained source transport, robots/rate limits, evidence gate, risk and cost analysis. `Campaign.quest_intelligence` carries extraction from the full available source summary, before the legacy report snippet is clipped.
- `quest_adapters.py`: existing official-feed inventory, reviewed public feeds, manual URL corroboration and documented Galxe queries. Health statuses include OK, DISABLED, UNSUPPORTED, AUTH_REQUIRED, UNAVAILABLE and RATE_LIMITED. One unavailable integration does not invalidate other sources. No guessed API endpoints or HTML-page extraction.
- `quests.py`: typed field conventions, task IDs, lifecycle gates, human decisions, deadlines and digest counts. A task ID hashes the canonical quest ID and normalized task text; repeated descriptions collapse to one checklist entry. Consent is scoped to the current quest terms hash. Changed terms invalidate approval.
- `persistence_v14.py`: extension validation and merges. Newer quest observations win; independent human completion proofs survive overlapping saves. Wallet events and processed transaction signatures merge by identity.
- `rewards.py`: wallet-specific snapshots and finalized transaction observations; no signing, claiming, prices or USD estimates. Public addresses are used only for read-only RPC. A different address has a different baseline.
- `scripts/quest_queue.py`: inspect records, approve a specific task, or confirm manual completion with a note. This is not an executor.
- `main.py`, `telegram.py`, `reporter.py`: orchestration, actionable alerts, one successful summary per UTC day, and JSON/Markdown reports.

## Queue states and transitions

| State | Meaning |
|---|---|
| DISCOVERED | Candidate exists; official evidence is incomplete. |
| VERIFIED | Evidence passes, but eligibility/cost/time/freshness is not sufficient for action. |
| READY | Evidence, eligibility and zero-cost checks pass; no outstanding approval on remaining tasks. |
| NEEDS_APPROVAL | Same eligibility gates pass; at least one task needs human consent. |
| COMPLETED | Every required task has an explicit human completion record. Opening a URL cannot do this. |
| REJECTED | News, mandatory capital, failed eligibility, high risk, or unavailable/deleted platform campaign. Ordinary NEWS_ONLY entries are counted in reports rather than added to the queue. |
| EXPIRED | End date passed or platform explicitly reports expiration. |

Every scan re-evaluates records, including expiration of quests not returned by a failing source. Start dates in the future, invalid dates, unknown fees, unknown eligibility and observations older than 24 hours prevent READY. Dates without a time use UTC; end dates include the full calendar day. Unknown dates are reported as absent, never fabricated.

Wallet connections/signatures, account authorizations, social actions, personal-information submission and claiming require approval. Unknown action types also require approval. Approving a task records consent only. Completing it requires a separate human confirmation; a URL alone is rejected. No module executes wallet, social, account or claim actions, even after approval.

Eligibility from text is conservative: explicit unrestricted participation is accepted only without contradictory restrictions. Otherwise eligibility remains UNKNOWN. Galxe eligibility is opt-in; ALL/ANY groups are evaluated using actual boolean `eligible` fields, and all groups must pass. Unknown group semantics or missing conditions remain UNKNOWN. Gasless on Galxe establishes only the platform gas policy, not free participation in every task.

## Notifications and persistence

Immediate quest alerts require a current observation and queue state READY or NEEDS_APPROVAL in addition to V1.3.1 evidence and PASS cost. Approvals are actionable review requests, not execution authorization for the program. Successful delivery remains per canonical campaign ID; failed delivery retries, successful delivery is not repeated. Each task has its own stable status and proof; no per-task Telegram blast is sent. Human approval/completion appears in later digests and reports.

The digest reports new verified quests today, pending approvals, completed tasks, rejected campaigns, expired quests, and explicitly attributed received rewards. Reward totals are cumulative receipt records, not USD earnings. Ordinary news appears only as a count. The daily key remains `summary:YYYY-MM-DD`, preserving V1.3.1 delivery history.

GitHub Actions restores and uploads the same state artifact, on the same `17 1,13 * * *` schedule. Optional workflow inputs record human task decisions after restoration; fields are passed through environment variables and quoted shell arguments. Failed jobs still upload artifacts, but automatic restoration continues to select the previous **successful** run, as before. A failed run's newer artifact may require manual recovery.

## Receipt evidence and attribution

1. First successful wallet observation establishes a baseline; existing assets are not newly earned rewards.
2. Successful SOL and complete SPL observations update their respective baselines independently. RPC failure never resets a baseline to zero. The bounded SPL display list is not used to infer missing assets as zero.
3. `getSignaturesForAddress` and `getTransaction` use finalized commitment and documented read-only RPC methods. Only matching transaction signatures with successful outcomes are parsed. Pre/post balances must belong to the monitored owner.
4. Positive transaction changes are UNVERIFIED_INCOMING by default. A configured trusted asset becomes CONFIRMED_RECEIVED_TOKEN, which still does not establish an airdrop.
5. CONFIRMED_QUEST_REWARD additionally requires a reviewed attribution record tying the observed signature and asset to an evidence-verified quest and a public evidence URL. Configuration without a positive actual transaction observation cannot create a receipt.
6. Off-chain points are `advertised_not_earned` with `earned: null`. Speculative reward claims remain separate. No price feed is configured, so no USD earnings are calculated.

Snapshot deltas and transaction deltas are overlapping evidence. They are never summed together as rewards. Receipt tracking is limited to the latest 20 finalized wallet-address signatures per scan and surfaces a coverage warning when the window is full. It is not a complete historical indexer. Token-account-only activity absent from the owner's signature history can be missed. Token-2022 is not covered by the retained classic SPL query.

## Verified integration contract (2026-11-06)

Official documentation fetched successfully over verified HTTPS:

- [Galxe authentication](https://docs.galxe.com/galxe-integration/getting-started/authentication.md): `access-token` header; operator-generated server API token required.
- [Galxe Quest API](https://docs.galxe.com/galxe-integration/api-reference/quest.md): `https://graphigo-business.prd.galaxy.eco/query`, `quest(id: ID!)`, metadata fields and `credentialGroups(address:)`.
- [Galxe rate limits](https://docs.galxe.com/galxe-integration/resources/rate-limits.md): quota errors; the adapter stops on 429 and defers until a later scan, with one-second request spacing.
- [Solana signature history](https://solana.com/docs/rpc/http/getsignaturesforaddress) and [transaction RPC](https://solana.com/docs/rpc/http/gettransaction).

Galxe's schema is documented and covered by mocked contract tests. A live authorized Galxe query has **not** been verified because no token is configured; it remains disabled/AUTH_REQUIRED. A successful runtime query validates identity and schema before accepting any result. Reviewed ID/URL mappings are required because the documented metadata query does not supply an official URL; the adapter never constructs campaign URLs by guesswork. No claim, credential mutation, account authorization or social action API is called.

Other platforms require a reviewed public feed or a separately implemented documented adapter. Unknown platforms report UNSUPPORTED. Manual URLs must have a matching official-feed announcement; mere page accessibility is not reward evidence.

## V1.4.1 diagnostics and review

`SourceHealth.observations` retains every analyzed entry before the legacy report cap. `main.py` combines these with adapter observations, selects one current record per canonical ID, and updates the existing queue. `diagnostics.py` emits reason objects (`code`, `detail`, `next_action`) and separate source failures. Repeated observations get DUPLICATE without conflating discovery with delivery; notification history remains authoritative for successful sends and retries.

Bankless Academy uses a named public JSON parser in `sources.py`. Only the documented endpoint and official lesson host are accepted. `discovery.py` maps its explicit task/badge fields, with UNKNOWN costs and eligibility unless mandatory task payment establishes FAIL. General educational prose and quiz answers never become task evidence. EDUCATIONAL_BADGE is separate from token receipts.

`quests.review_candidate` records accept/reject/defer with a substantive human note, timestamp and terms-scoped persistence. Accept runs the normal eligibility gates and cannot grant task consent. `persistence_v14.py` validates review proofs and preserves the latest review when merging unchanged terms. Read-only CLI listing never writes or quarantines the artifact, including malformed JSON; normal scans retain existing corruption recovery.

Daily diagnostics use the existing persisted summary notification key. Source failures, news rejections, official evidence, cost PASS and actual receipts have separate counters. The legacy capped campaign list remains available for compatibility; consumers wanting complete scan decisions should use `discovery_diagnostics`. Historical completed tasks and confirmed reward receipts are explicitly cumulative rather than daily earnings.

## V1.4.2 qualification layer

`qualification.py` sits between quest extraction and alert/action decisions. `refresh_status` recalculates qualification separately from legacy task lifecycle state. `main.py` gates alerts on ACTIONABLE and includes a deterministic grouped shortlist; diagnostics expose current-scan counts by qualification status. `reporter.py` includes all field evidence in the existing artifacts.

`review_candidate` records append-only events without changing source facts. `persistence_v14.py` migrates additive fields, unions histories across merges, preserves valid task proofs when optional metadata is malformed and archives tasks removed from source observations. The existing state envelope, daily notification keys and wallet attribution mechanism are retained. See [qualification specification](qualification-v142.md) for exact weights, gating semantics and compatibility limits.

## V1.4.3 recovery layer

`recovery.py` groups qualification gaps, accepts evidence URL requests and revalidates canonical records through approved official source adapters. Scheduled observations are reused; additional source reads are batched, cached and backed off persistently. A submitted URL never directly changes a qualification field. Unsupported HTML pages are not fetched.

`quests.update_queue` records changed terms and prior task proofs, preserving canonical IDs and completion history while invalidating current approvals. Qualification adds a changed-terms review hold. `persistence_v14.py` unions requests/change histories and merges the latest resolver metadata. `main.py` writes the all-persisted-candidate recovery report, embedded by `reporter.py` and uploaded in the existing report artifact. Wallet attribution, notification keys and schedules retain their existing paths. See [evidence recovery specification](evidence-recovery-v143.md).

## V1.4.4 controlled campaign expansion

`campaign_sources.py` maps the documented Optimism mission API to existing Campaign/quest records with an exact repository/URL binding. `qualification.py` recognizes only this reviewed cross-host API binding; existing gates still apply. Source classification/provenance fields are additive persistence fields, without changing canonical IDs or task/review terms.

`source_onboarding.py` validates committed source reviews and fixture integrity. `source_security.py` validates HTTPS syntax and public DNS destinations; `sources.py` rejects redirects and bounds streamed responses. `source_coverage.py` computes current-scan utility metrics and deduplicated campaign coverage separately from education and receipts. `main.py` writes the additional coverage artifact using the existing workflow artifact path. See [V1.4.4 source expansion specification](source-expansion-v144.md).

### V1.4.5 triage and operator requests

`triage.py` derives independent classifications without mutating qualifications.
FREE_VERIFIED requires both fee and gas gates plus an official URL; missing gas
remains COST_UNKNOWN. Grants are program funding, not individual token payouts.
Review ordering counts explicit preferred properties, with deterministic ID ties;
all mandatory qualification gates remain authoritative. Each worklist row exposes
all field-level evidence and a manual action, or explains grant/education deferral.

`manual_evidence.py` accepts only URL request intents and checks a semantic state
revision before enqueueing. `scripts/import_evidence.py` validates restored state,
locks the read/check/write transaction and atomically replaces the local artifact.
It never imports a submitted state document or facts. `operator_history` records
request payload hashes, actor, timestamp, URL and conflict/outcome; discovery and
state merges union that history. Independent official-source revalidation alone
can alter facts. Existing human task approval and completion remain separate.

### V1.5 operator lifecycle and reconciliation

`operations.py` adds an additive per-candidate `operations` snapshot and append-only
`operation_history`; canonical IDs, legacy task state/proofs and notification keys
remain unchanged. Initial migration is idempotent and never infers a completed
operation from legacy task completion. Current mandatory qualification gates are
checked at each pre-submission transition. Receipt reconciliation is the only
REWARD_VERIFIED path. Plans and execution checklists are separate from evidence
checklists and do not execute instructions. Grant plans retain versioned human
reviews; grant allocations never enter transaction earnings metrics.

`scripts/opportunity_ops.py` applies explicit intents under an exclusive lock with
semantic revision validation, atomic replacement and duplicate request detection.
Merge logic unions operation events and selects the newest operation snapshot
independently of newer source observations. The existing workflow concurrency
serializes cloud scans and dispatches; no existing schedule or permissions change.

`reward_monitor.py` maintains cursor/head/before, pending signatures, retry time,
last successful interval and explicit scope/history gaps. Transactions are
finalized and identity/owner checked, then deduplicated by existing signature plus
asset event keys. Failed RPC calls never count as empty history. The legacy
`recent_transfers` function remains available for compatibility; production uses
the resumable reconciler. The monitored address history is not a claim of complete
SPL-account coverage or archival-provider completeness.

`operator_bot.py` authenticates sender and chat using Telegram's Bot API transport
and explicit user allowlist. Replies use a minimal public projection, excluding
review notes, applicant information and credential-bearing provider URLs. Only
notification cursor metadata changes, never qualification or task decisions.
Atomic pre-send claims provide at-most-once dispatch across overlapping workers;
there is no claim of exactly-once delivery. Polling is bounded and follows the
existing scan schedule unless an operator invokes the standalone poll.

### V1.5.2 webhook read model

GitHub Actions exports a strict version-1 public projection through
`operator_snapshot.py`, independently from private report/state artifacts.
`scripts/export_operator_snapshot.py` optionally publishes it to the authenticated
Worker endpoint; publication failure never resets agent state. The Worker's
SQLite Durable Object holds only the latest projection, transient atomic update
claims, bounded rate counters and delivery outcome timestamps. No discovery,
qualification, approvals or wallet operations move to Cloudflare.

Polling remains the default. The operator must verify deployment, Telegram
registration and actual human command delivery before setting both explicit
cutover variables. See the deployment document for drained, non-destructive
rollback and the acknowledged at-most-once delivery tradeoff.
