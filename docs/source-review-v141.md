# Source review — V1.4.1, 2026-11-06 UTC

## Enabled: Bankless Academy public lesson API

- Ownership: https://banklessacademy.com redirects to https://app.banklessacademy.com/.
- Documentation: https://app.banklessacademy.com/llms.txt explicitly links https://app.banklessacademy.com/api/lessons and the official source repository, https://github.com/bankless-academy/bankless-academy.
- Permission: https://app.banklessacademy.com/robots.txt explicitly welcomes crawlers and AI agents to read/index the MIT educational content (`User-agent: *`, `Allow: /`). The published content index describes the JSON and Markdown interfaces. No login, token, private endpoint or webpage scraping is used.
- Live check: API returned HTTP 200, application/json, array of 23 lesson/handbook records. Ten published LESSON records have a quest component; three include `learningActions`. Deprecated content and handbooks are excluded.
- Mapping: `englishName`/`name`, `lessonLinks.en`, `quest`, `badgeId`, `learningActions`, `publicationStatus`, `type`. Campaign links must be HTTPS on the verified host. No quiz answers are retained or acted upon.
- The badge is an educational achievement, not tokens or income. A quest component and badge ID establish program evidence; concrete `learningActions` are also required for evidence verification. Missing task requirements remain UNVERIFIED. Fees and participant eligibility remain UNKNOWN; explicit mandatory payments in task requirements produce FAIL. Free educational access does not prove free on-chain participation or claiming.
- The adapter does not infer campaign dates from publication dates or derive requirements from generic lesson prose. Users must review the current task and badge requirements. No new credentials are needed.

## Reviewed but not enabled

- Galxe: existing documented integration remains AUTH_REQUIRED without a legitimate configured token and reviewed campaign IDs. No authentication bypass.
- Layer3: public terms prohibit automated bots/scripts/data mining. Not enabled.
- Intract: public access returned HTTP 403. Not enabled; no bypass attempted.
- Zealy and RabbitHole: public landing pages were reachable, but this review did not establish a suitable authorized public campaign interface and complete mapping. No guessed endpoints were enabled.
- Sui blog: reachable public news feed, but not selected merely to increase source count.

## Maintained Actions

Verified official release tags and their action manifests: [checkout v7.0.1](https://github.com/actions/checkout/releases/tag/v7.0.1), [setup-python v7.1.0](https://github.com/actions/setup-python/releases/tag/v7.1.0), [upload-artifact v7.0.2](https://github.com/actions/upload-artifact/releases/tag/v7.0.2). These use Node 24; checkout/setup-python require runner 2.327.1 or newer. The workflow uses maintained GitHub-hosted `ubuntu-latest`. Schedules, permissions, credentials, concurrency and previous-run artifact restoration remain unchanged.

## Release validation

Local complete suite: **426 passed in 15.45s**, 2026-11-06. Deterministic fixtures cover missing evidence, unknown fees, expiry, structured reasons, duplicate tasks/alerts, source failure, human review, same-day digest idempotency and artifact restoration, alongside the retained wallet/reward tests. Fixtures are not actual rewards.

The live `scripts/verify_sources.py` check returned **7/7 HTTP 200**, zero failed sources: Bankless Academy, AirdropAlert RSS and WordPress API, airdrops.io, Decrypt, Ethereum Foundation and Arbitrum. Availability is a point-in-time observation, not a guarantee of future access or an actionable campaign.
