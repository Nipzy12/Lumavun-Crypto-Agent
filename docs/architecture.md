# LUMAVUN V1.4 architecture

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
