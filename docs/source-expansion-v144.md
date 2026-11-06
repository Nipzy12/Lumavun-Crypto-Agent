# Official campaign discovery expansion — V1.4.4

## Audit and scope

| Existing source | Class | Role |
|---|---|---|
| AirdropAlert RSS | MIXED | Third-party campaign claims and articles; not official proof |
| airdrops.io RSS | MIXED | Third-party guides and campaign claims |
| AirdropAlert WordPress API | MIXED | Publisher articles, including campaign coverage |
| Decrypt RSS | NEWS | News discovery; no actionable classification from keywords |
| Ethereum Foundation blog | MIXED | Official technical, ecosystem and occasional funding announcements |
| Arbitrum blog | MIXED | Official ecosystem and incentive announcements |
| Bankless Academy lesson API | EDUCATION | Educational badges; excluded from monetary discovery yield |

The additional source is **CAMPAIGN**: Optimism Foundation Mission Requests through the documented GitHub repository-issues REST API. Eight sources are now configured. Existing source IDs remain unchanged.

## Verified official integration

- Repository: https://github.com/ethereum-optimism/ecosystem-contributions — MIT, not archived at review.
- Ownership: the GitHub `ethereum-optimism` organization is verified and identifies https://optimism.io as its website. The repository README explicitly directs grant seekers to issues labeled **Foundation Mission Request**. This is a maintained label in the official repository, not a claim based on arbitrary issue authors; current authors may have CONTRIBUTOR association.
- Discovery endpoint: `https://api.github.com/repos/ethereum-optimism/ecosystem-contributions/issues?state=open&labels=Foundation%20Mission%20Request&per_page=30`.
- Documentation: https://docs.github.com/en/rest/issues/issues#list-repository-issues. GitHub documents this public-resource endpoint as available without authentication. The application sends no Authorization header and adds no credential. Existing runtime network credentials, if provided by the platform, are not exposed or changed.
- Permission: published GitHub API terms at https://docs.github.com/en/site-policy/github-terms/github-terms-of-service. Rate limits are respected; no HTML scraping, private API or account interaction.
- Observed on 2026-11-06: HTTP 200, JSON array, nine labeled open issues. `api.github.com/robots.txt` returned 404; the API's explicit public documentation applies.
- Mapping checks repository URL, exact official issue URL/number, required curated label and issue state, and excludes pull requests. The API endpoint is fixed, not constructed from submitted links.

The source contains developer/community grant missions, not guaranteed free cash. Explicit OP allocations are total program budgets, not individual earnings. Tasks are quoted from the required-work section; a community-support task is never permission to automate social activity. Reward conditions, dates, fees, gas, geographic/account restrictions and source update timestamps are retained. Missing years, season-relative dates and rolling applications do not imply an open deadline. An open GitHub issue does not establish active participation. A required attestation is a credential rather than payment evidence; owned token requirements still fail the zero-capital gate. No cost statement means UNKNOWN. Truncated campaign bodies cannot get cost PASS.

## Canonical legacy endpoints

Redirect protection revealed these published same-host migrations:

- Ethereum: `/feed.xml` → `/en/feed.xml`; the direct destination returned HTTP 200 with 646 entries.
- Arbitrum: `/feed` → `/feed/` → `/rss/`; the homepage advertises `/rss/`, which returned HTTP 200 with 15 entries.

Configuration now uses the reviewed final endpoints directly. The agent does not follow redirects. Source IDs, campaign URL IDs and previous state remain intact. Older verification snapshots in historical documentation describe their own observation dates.

## Sources researched but not enabled

- Gitcoin governance: official ownership established, but forum terms restrict automated monitoring, and robots restrict category RSS. Not integrated.
- Avail: official site links its RSS; permission/robots checks succeeded, but latest entries were primarily technical/product updates. “Nexus Rising” was a product upgrade, not a reward campaign. Not added merely to increase source count.
- Sonic Labs: advertised RSS was reachable but current entries were mostly product/governance updates; a campaign-specific integration and full policy review were not established. Not enabled.
- ZetaChain: no suitable published feed found in the reviewed page.
- Tanssi/Fuel blog hosts: access failed in this environment; no bypass attempted.
- Superteam: no documented usable campaign feed/API was established in this review. No guessed endpoint enabled.
- Galxe: existing AUTH_REQUIRED behavior remains until a legitimate configured token and reviewed integration are available.

## Controlled onboarding and network boundary

New registry entries require committed APPROVED metadata: exact endpoint/parser binding, ownership/documentation/permission URLs, verification date, fixture path and fixture SHA-256. Unreviewed entries, changed fixtures and mismatched provenance are rejected before requests. Legacy IDs are bound to their reviewed endpoints. Existing `AIRDROP_RSS_FEEDS` overrides remain nonofficial and receive network validation; they do not gain official provenance.

```bash
python scripts/check_source_onboarding.py --source optimism-foundation-missions
python -m pytest tests/test_sources_v144.py -q
python scripts/verify_sources.py
```

The offline command checks provenance metadata and parser fixture integrity; it does not grant permission or prove live availability. Human source review, deterministic tests and a live endpoint check are separate steps. The committed fixture is explicitly synthetic and never counted as a discovered opportunity or payout.

Every source/robots request validates public HTTPS syntax and public DNS answers. Private, loopback, link-local, metadata, alternate numeric IP, credential-bearing and nonstandard-port URLs are rejected. DNS is checked again before the request. No source redirect is followed; robots wildcard/end-anchor rules are honored, and robots denial/rate-limit/server/redirect responses defer access. New sources still require endpoint review: DNS checking is defense in depth, not authorization to request arbitrary URLs. No campaign link is automatically fetched as a source.

Requests have timeouts, identifying User-Agent and existing per-host pacing. Source payloads are streamed with an 8 MiB bound; robots payloads have a 64 KiB bound. The mission adapter uses one request with `per_page=30`, no automatic pagination or retry loop. Existing scanner limits (10 sources, 15 analyzed entries per source, 20 legacy report findings) remain. Full queue/coverage diagnostics are distinct from the capped legacy findings list. Resolver backoff and daily notification deduplication are preserved.

## Coverage and quality

`source_coverage.json` is uploaded in `latest_report` and embedded in `latest_report.json`. It reports available official CAMPAIGN/MIXED sources, unique newly discovered noneducational campaigns, verified/actionable candidates, REVIEW_REQUIRED by missing field, token-program candidates, educational entries and confirmed received rewards. Counts are current-scan observations; all-persisted-candidate recovery remains in `opportunity_recovery.json`.

Source quality is a deterministic current-scan utility score:

- 40%: distinct verified noneducational campaign yield / noneducational entries examined.
- 30%: average proportion of qualification fields PASS among source-local candidates.
- 20%: observed source availability.
- 10%: precision proxy among keyword-nominated entries (one minus the NEWS_ONLY rejection rate).

No nominations means that precision is unknown and earns zero, not assumed perfect. No observations earns no yield/completeness credit. Education receives no monetary-discovery quality score. Duplicate canonical campaigns are counted once in coverage, and source-local observations are used for quality so another source's evidence cannot inflate a publisher's score.

The false-positive figure is an explicitly labeled **keyword rejection proxy**, not adjudicated fraud prevalence. The score is neither safety nor payout probability and never changes qualification. Points, speculative allocations, educational badges and promised token budgets are not received income. The existing finalized-transaction attribution engine remains the sole source of confirmed received reward counts.

No new dependency, GitHub secret, wallet operation or schedule is introduced. Read-only SOL/SPL monitoring, task proofs, reviews and notification history retain their existing persistence path.

## Validation

On 2026-11-06, the complete local test suite passed: **536 passed in 19.55s**. Regression fixtures cover official provenance, source/schema failures, incomplete rewards, deposits, ambiguous gas, expired/full-year dates, eligibility, duplicate announcements, malicious URLs/DNS answers, redirect refusal, robots rules, onboarding tampering and response-size limits. Existing wallet attribution and daily-deduplication tests remain active.

A live verification of the eight configured final endpoints returned **8/8 HTTP 200** with zero source failures. The new mission API yielded nine records; this is discovery, not proof of eligibility or payment. A migration check against the actual V1.4.3 artifact from run `42232586303` preserved all 27 existing IDs, task proofs, reviews, notifications and wallet attribution. Migration remained idempotent.
