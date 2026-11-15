# V1.6 — Audited zero-capital auto-claim engine

**Operational status:** deployed to the repository, tested with deterministic mocked
official-provider responses. **No production claim was attempted.** The reviewed
claim-adapter registry is intentionally empty. As of the last audited queue,
there were zero ACTIONABLE/ELIGIBLE campaigns and no verified free,
automation-permitted claim API. Do not call tests or an API acceptance a payout.

## What is implemented

- Real, outbound HTTPS **GET** entitlement verification and **POST** claim transport
  for an independently reviewed, permissioned program implementing the documented
  contract below. Submission is **one-shot**, never a speculative retry.
- Exact campaign ID, official campaign URL, API origin and two static API paths must
  be pinned in `config/claim_adapters.json` via a reviewed Git commit. No user,
  Telegram or discovery-feed input may set execution URLs.
- Each adapter must have explicit documented permission for automated claims,
  independent proof that all mandatory fees and gas are zero, and verified terms
  and official API documentation; the review expires after 30 days.
- **Solana mainnet only** at present: the existing Phantom wallet is used only
  as a public identifier, never as a signer. Full Base58 32-byte validation is
  enforced. No seed/private keys or onchain submissions are supported.
- All 16 existing qualification gates must pass; the campaign must be an
  individual token-reward quest with task completion evidence, authorized
  approvals, and lifecycle SUBMITTED or AWAITING_RESULT.
- A separate operator authorization binds the specific campaign revision,
  reviewed adapter fingerprint and hashed public wallet identifier, expiring in
  six hours. Any terms change, revocation or stale eligibility cancels readiness.
- A read-only live entitlement check must confirm matching campaign and wallet,
  active/eligible/claimable and explicitly false gas and mandatory fees **before**
  a POST can be made.
- The engine writes a durable CLAIM_DISPATCHED event before sending POST. A timeout,
  crash or ambiguous acknowledgment is never automatically replayed. Platform
  acknowledgment is recorded as CLAIM_SUBMITTED_UNVERIFIED, **not earnings**.
  Only the existing finalized wallet-transfer attribution can report income.
- Each scheduled scan writes `claim_readiness.json` to `latest_report` with blockers,
  claimed-attempt counts and existing wallet-attributed receipts. Claim data
  persists through the existing operator-history state merger.

## Official adapter onboarding — hard requirement

There are **zero adapters enabled** in
[config/claim_adapters.json](../config/claim_adapters.json).
Do not invent one, treat an airdrop guide as API documentation, or interpret a
web claim button as automation permission.

Before adding an adapter, independently verify **all** of the following from
the project's official sources:

1. Exact official campaign and its live active status, eligibility for the
   dedicated wallet/account, full task and reward terms, end date.
2. Public, authorized API explicitly permits an individual participant to
   submit automated claims, with documented GET entitlement and POST claim
   contracts. No scraping, CAPTCHA bypass, wallet signing, OAuth interception,
   sybil abuse, payment or unapproved engagement.
3. Written, campaign-specific proof that participation **and the claim itself**
   require neither payment nor gas. A general "gasless" label is insufficient.
4. Provider's authentication, idempotency/retry behavior, response meanings,
   permission/rate limits, payout chain and possible taxes/fees.
5. Provenance and ownership of exact API host, official URL, documentation and
   terms. The repo's source-security boundary checks public HTTPS/DNS and rejects
   redirects; source review is still essential.
6. Implement provider-specific contract translation **in a reviewed adapter**
   if its real API differs from the engine's pinned JSON contract. Never guess
   a URL or shape from its website.

The registry schema for each adapter requires: `id`, `campaign_id`,
`official_url`, `api_origin`, `eligibility_path`, `claim_path`, `docs_url`,
`terms_url`, `checked_at`, `automation_allowed=true`,
`gasless_verified=true`, `no_mandatory_fees_verified=true`,
`network="solana-mainnet"`, and `requires_token`.
Only one credentialed provider can use `CLAIM_PROVIDER_TOKEN`; separate
providers must use isolated credentials and an audited code change.

**Expected provider response contract (not a claim that a real provider
implements it):** The HTTPS GET endpoint receives `campaign_id` and `wallet`
query parameters and must respond with matching identifiers, `eligible=true`,
`claimable=true`, `active=true`, `fee_required=false`,
`gas_required=false`. The HTTPS POST endpoint receives the same identifiers
and `idempotency_key`, and returns matching identifiers, a distinct
`claim_id` and `status=accepted|pending|already_claimed`. Both responses
must be JSON and HTTP 200. This contract is **only** an integration interface;
a real project's official API must be confirmed independently before use.

## GitHub operator flow

The existing [agent workflow](../.github/workflows/agent.yml) now offers three
manual `queue_action` values:

- `claim_authorize`: enter canonical `quest_id`, `expected_revision` from
  the latest `claim_readiness.json` artifact, and a unique lowercase
  `review_request_id` matching `[a-z][a-z0-9_-]{2,48}`. This records
  a time-limited approval, only if every claim gate passes.
- `claim_revoke`: record an explicit revocation for that campaign.
- `claim_run`: execute only the campaign named in `quest_id` if an active,
  specific approval exists and the live API entitlement check passes.

Scheduled scans execute **audit-only** unless the repository Variable
`CLAIM_AUTO_ENABLED` is explicitly set to `true`. Once enabled, the scanner
will try eligible, authorized campaigns **only**, with no repeated POST.
If set to true during an explicit `claim_authorize` dispatch, the scanner
will attempt the approved claim immediately after authorization.

Set `CLAIM_PROVIDER_TOKEN` as a GitHub Actions **Secret** only when a vetted
provider specifically documents this credential. It must never be embedded in
URLs, logs, configuration, Telegram messages or reports. Do not enable an
adapter by supplying a token alone.

**Fail-closed rules:** Missing restored state, absent adapter, stale source
evidence, unknown cost, incomplete task proofs, missing approval, wrong wallet,
unverified provider identity, or missing credential prevent claim submissions.
The normal discovery/Telegram/wallet monitoring remains read-only.

## Verification and limits

- `.github/workflows/claim-checks.yml` runs Python compilation, registry
  validation, the full Python test suite, and a mocked official GET+POST test.
- Live production claiming requires **a specific externally verified campaign**
  and a live official endpoint, followed by authorization. As of this release
  there is no qualifying adapter. Reports truthfully show ready=0.
- A successful POST does not prove delivery of tokens. The existing finalized
  Solana wallet monitor must see and independently attribute a real transfer.
  Token-account-only historical gaps remain a known limitation.
- Do not use this architecture to automate Galxe/Layer3 activity where
  platform access or anti-bot rules prohibit it.
