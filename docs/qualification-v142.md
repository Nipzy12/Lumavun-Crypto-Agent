# Opportunity qualification — V1.4.2

## Independent evidence and ranking

`qualification.py` evaluates 16 named fields on every queued candidate and diagnostic review row. Each field has PASS/FAIL/UNKNOWN, `evidence_url` (nullable when no usable source exists), an explanation and `last_verified_at` (nullable for legacy records without an observation timestamp). UNKNOWN records the last assessment time, not successful verification. `evaluated_at` distinguishes recalculation from a new source observation. Reading state never refreshes evidence.

| Field | Score weight |
|---|---:|
| Organizer/campaign identity | 10 |
| Official campaign URL | 10 |
| Task requirements | 10 |
| Reward type | 8 |
| Reward certainty/conditions | 7 |
| Mandatory fees | 10 |
| Gas costs | 5 |
| Geographic eligibility | 5 |
| Account eligibility | 5 |
| Start date | 3 |
| Deadline | 4 |
| Availability | 3 |
| Required human actions | 5 |
| Source reliability | 5 |
| Evidence freshness | 5 |
| Risk | 5 |

PASS earns the field's weight; FAIL/UNKNOWN earns zero. Medium risk subtracts five. Sum is bounded 0–100, with canonical ID as the shortlist tie-breaker. A score of 95 with high risk is still BLOCKED. Ranking estimates evidence completeness, never likely earnings.

All fields are mandatory for ACTIONABLE. Expiry wins over other outcomes. Any failed field or explicit rejection is BLOCKED; missing evidence or deferred review is REVIEW_REQUIRED. Official feed URLs must match the observed announcement URL and configured official host. Authorized Galxe URLs use the existing reviewed platform/API binding. Source reliability means verified provenance, not a security audit of the organizer.

The retained zero-capital evaluator supplies the overall mandatory-payment/gas decision; UNKNOWN never passes either field. Separate geographic/account checks flag published restrictions and require a participant-specific authorized check. No location, account ownership or identity is guessed. Dates must be explicitly available: missing deadlines are not assumed to mean indefinitely open. Evidence must be observed within 24 hours; future timestamps cannot pass freshness.

`human_actions` PASS means requirements are identified, **not consent to execute them**. Existing NEEDS_APPROVAL/task approvals remain separate. This patch does not execute any task. New task approval/completion and immediate alerts require ACTIONABLE qualification as well as the existing lifecycle gates. Historical task states and completion evidence remain intact even if current qualification becomes REVIEW_REQUIRED.

## Reward classes versus receipts

- CONFIRMED_TOKEN_REWARD: explicit token terms and guaranteed-program evidence, not a received token or assurance the organizer will fulfill its promise.
- CONDITIONAL_TOKEN_REWARD: an evidenced token program whose payout remains conditional. Unknown certainty still blocks qualification.
- POINTS_ONLY: off-chain points; possible future allocations do not turn them into tokens.
- EDUCATIONAL_BADGE: educational achievement, never income.
- SPECULATIVE_AIRDROP: speculation, not an actionable promise.
- UNKNOWN_REWARD: missing or unsubstantiated reward evidence.

Only the unchanged finalized-transaction tracker and reviewed receipt attribution create confirmed received rewards. Qualification and ranking cannot create a transfer, price, receipt or earnings amount.

## Persistence and review

State envelope remains schema 1.4. `qualification_schema: 1.4.2`, qualification fields, `review_history`, `eligibility_text`, and `task_archive` are additive. Migration is idempotent and never refreshes timestamps or canonical IDs. Optional malformed qualification data is recomputed; valid task approvals/completions remain. Removed source tasks are archived rather than deleted or executed. Existing notification keys and wallet attribution records are unchanged.

`review --decision accept|reject|defer --evidence ... --reason ... --reviewer ...` records a unique append-only history event: decision, supplied evidence, reason, reviewer identifier, time, previous/new qualification status. Existing legacy reviews migrate as historical events with unknown previous/new statuses rather than fabricated transitions. Histories are unioned across persistence merges and unchanged scans; review terms may expire without erasing history. Reviewer defaults to `operator`, reason defaults to the substantive evidence note for CLI compatibility. Supplied notes are stored, not treated as verified source facts. Acceptance cannot override UNKNOWN or FAIL.

`review` without a decision is read-only and includes all evidence fields/history. Full detail is also in JSON/Markdown artifacts. The compact daily shortlist shows at most one ranked example per category with up to three missing/failed fields; educational entries are separated from monetary opportunities. Full lists remain in artifacts. The digest uses the existing daily notification key and is limited to one compact message.

No sources, dependencies, credentials, schedule, wallet RPC behavior or GitHub permissions change in this release. Galxe remains AUTH_REQUIRED without an authorized token. Some legitimate campaigns remain REVIEW_REQUIRED because public feeds omit dates, fees, participant restrictions or reward conditions; human approval cannot fill these gaps.

## Validation

On 2026-11-06, the complete local suite passed: **459 passed in 16.46s**. New deterministic tests cover field evidence, unknown fees/rewards, geographic/account restrictions, fake URLs, expiry, reward classes, high-score blocking, review history merges, migration, malformed optional fields, task archives, compact digests and attribution separation. Existing notification-deduplication and finalized-wallet-transfer tests remain active.

A read-only migration check against the actual V1.4.1 artifact from workflow run `42204797288` preserved all 27 canonical quest IDs, task records, notification records and wallet attribution state. Repeated sanitization was identical. This migration check is not a new live scan or a payout claim.
