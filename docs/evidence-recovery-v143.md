# Evidence resolution and opportunity recovery — V1.4.3

## Gap analysis

Every review candidate exposes its complete mandatory UNKNOWN fields, minimum missing set, failed conditions, required human decisions, verification steps, current qualification and freshness. Because all gates are conjunctive, the smallest missing set is the set of all UNKNOWN mandatory fields; resolving only a high-weight field cannot unlock the candidate. A deferred review or changed-terms hold still requires a decision even when that set is empty.

Missing public eligibility policy is distinguished from published participant restrictions. Known country/account/wallet/KYC restrictions require participant-specific authorized verification. No geography, account ownership, identity or zero-cost eligibility is guessed. `all_public_evidence_resolved` excludes only actual participant-specific gaps; missing published eligibility rules remain public gaps.

## Approved transport boundaries

Revalidation reuses independently fetched observations from the current scan or revisits the campaign through its configured, reviewed official RSS/Atom/JSON source. Supported parsers are `feed`, `bankless_lessons`, and `wp_rest_posts`; nonofficial, disabled or unrecognized transports are not fetched. The same canonical campaign ID must be returned. Third-party reports cannot establish official evidence.

The release does not add permission to scrape arbitrary campaign HTML. Campaign-page URLs can be submitted, but are checked through their approved official feed/API representation. An unsupported page or new evidence URL without an existing campaign/source binding is reported UNSUPPORTED; it cannot silently become an approved adapter. Bankless Academy continues through its documented public lesson API. Galxe remains on the existing authorized adapter and AUTH_REQUIRED behavior; this resolver does not make unauthenticated Galxe requests.

- URLs supplied by humans are requests, never facts. Only an independently retrieved matching official record can update qualification fields.
- Exact submitted URLs, source endpoint, official announcement URL and observation timestamps are retained. Successful checking can still leave all relevant fields UNKNOWN.
- Existing parsing extracts explicit fees, gas, reward conditions, dates, eligibility and tasks. There is no inference from missing text. Bankless educational narrative is still excluded from participation evidence.
- Additional requests honor robots.txt and the configured host rate limit. The enrichment request refuses redirects, authentication challenges, anti-bot restrictions and non-200 responses.
- A six-hour revalidation cache avoids redundant extra reads; cache hits do not change `last_observed` or `checked_at`. Fresh independently fetched scheduled observations may supersede a cache.
- Failed extra reads use persistent exponential backoff starting at five minutes, capped at six hours. A longer numeric Retry-After is honored. No sleep or authentication bypass occurs. This cache/backoff applies to enrichment; the existing scheduled discovery scan is preserved.
- Missing entries in a paginated or rolling feed are NOT proof of withdrawal. Explicit withdrawn/cancelled/closed text or the existing authorized platform status blocks participation. Unavailable sources do not delete progress or make old evidence fresh.

## Changed terms and approvals

Terms snapshots cover identity/URL, dates, tasks, reward evidence/conditions, fees, eligibility, availability and risk. A change creates an append-only event containing before/after snapshots, exact announcement URL, timestamp and previous task proofs. Current task approvals become invalid, while historical proofs, completion records and removed-task archives remain available. The candidate cannot be ACTIONABLE until changed terms are explicitly reviewed and every qualification gate passes. Acceptance never fills missing facts. Canonical IDs, notification keys, prior review histories and wallet attribution records are unchanged.

## Operator CLI

```bash
python scripts/quest_queue.py review --quest QUEST_ID
python scripts/quest_queue.py review --quest QUEST_ID --submit-url 'https://official.example/campaign'
python scripts/quest_queue.py review --quest QUEST_ID --revalidate
python scripts/quest_queue.py review --revalidate
```

The example URL is illustrative, not a configured source. Listing is read-only. Submission persists PENDING evidence requests and does not access the URL. `--revalidate` checks only approved transports, respecting cache/backoff; with `--quest`, only that candidate is checked. Subsequent scans also process pending requests. CHECKED means independently read, not automatically qualified. Requests outside the supported binding become UNSUPPORTED with a reason.

Local CLI updates affect the local restored artifact. Production still restores the previous successful workflow's `dedup_state` artifact; this patch does not add a remote state-upload command or change workflow approval inputs. Do not put secrets or personal data into evidence notes.

## Persistent recovery report

`opportunity_recovery.json` is included in the existing `latest_report` artifact and embedded in `latest_report.json`; Markdown shows key counts. It covers **all persisted candidates**, including candidates outside today's feed window. It records total/qualification counts, REVIEW_REQUIRED grouped by missing field and IDs, public-evidence-resolved counts, participant-specific waiting counts, field-resolution outcomes, reward classes and confirmed received rewards. The normal report still distinguishes current-scan counts.

Artifact retention is the existing seven days. Evidence requests, cache/backoff metadata and append-only terms events persist across runs in the existing state artifact. Optional malformed resolver fields are sanitized without resetting task history. State merges union request/change events and retain the newest resolution metadata. No new dependency, secret, schedule or wallet transaction capability is introduced. Badges, points and speculative allocations never become income; only the retained transaction-attribution engine reports confirmed received rewards.

## Validation

Complete local suite on 2026-11-06: **483 passed in 17.97s**. Deterministic regressions cover changed/withdrawn evidence, stale-cache behavior, Retry-After/backoff, unsupported sources, redirects, robots restrictions, ambiguous fees, participant eligibility, read-only review and URL submission, event merges, historical task-proof preservation and malformed optional resolver state.

A read-only migration check against the actual V1.4.2 artifact from run `42209308808` retained all 27 canonical IDs, task records, reviews, notifications and wallet attribution records. Repeated sanitization was identical. This is migration validation, not a new live opportunity or reward claim.
