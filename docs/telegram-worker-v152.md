# V1.5.2 real-time operator deployment

The repository contains a deployable Worker, not evidence of a live deployment.
A successful build or a mocked Telegram response does not prove production delivery.
GitHub Actions remains the sole discovery/qualification/wallet monitoring authority.
The Worker only reads a sanitized production projection and replies to commands.

## Storage and security

Use one Worker/namespace per bot; retain its namespace when rotating that bot’s token.
One **SQLite-backed Durable Object** serializes snapshot publication and atomic
update claims. Unlike eventually consistent KV, its transactions prevent concurrent
webhooks from issuing duplicate sends. Cloudflare documents SQLite Durable Objects
on the [Workers Free plan](https://developers.cloudflare.com/durable-objects/platform/pricing/).
Free compatibility is subject to account plan and quotas, not unlimited usage or a
promise of zero charges on a paid account. No VPS or always-on laptop is required.

The version-1 snapshot has observation time, canonical IDs, qualification/lifecycle,
verified official links (query/fragment removed), reward classes, missing field
codes, deadlines, aggregate confirmed receipt count and monitoring status/gap codes.
It excludes titles/free-form source text, approvals, participant notes, grant drafts,
wallet addresses, balances, token quantities, transaction IDs and credentials.
Unknown fields are rejected by the Worker. Older/conflicting snapshots cannot
replace newer ones. Data older than 24 hours cannot imply current qualification.

`PUT /snapshot` and `GET /health` require a separate snapshot write bearer secret.
`POST /telegram` requires the Telegram secret header, the exact permitted chat and
an allowlisted non-bot sender. Forwarded/anonymous/via-bot messages are ignored.
Bodies and replies are bounded. No endpoint approves reviews or executes tasks.
Do not enable request-body/header logging. Tokens never enter exported snapshots.

Update claims are persisted **before** sending. Delivery is at-most-once, not
exactly-once: Telegram sendMessage lacks an idempotency key. A crash/timeout after
claim can lose a reply; the user must send a new command. Never automatically retry
ambiguous sends, including Telegram 429 responses. `/health` exposes only timestamp,
command name and sanitized outcome codes, never identities or request contents.
Storage failure before claim returns 503 for Telegram retry. Concurrent/replayed
updates already claimed return 200 without sending. Messages older than 24 hours
are rejected; claims retained at least 48 hours are cleaned with bounded alarms.
Authorized traffic is limited to 30 commands/minute. Free-plan/storage quota failure
is an availability failure, never permission to skip authentication/deduplication.

## Deployment prerequisites

An operator needs a Cloudflare account, an account-scoped Workers deployment API
token, the existing Telegram bot token, permitted chat ID and numeric operator IDs.
This checkout does not contain those credentials. Use a trusted one-shot shell or
an approved GitHub deployment job; never put secret values into repository files,
GitHub public Variables, command transcripts or chat.

1. In `worker/`, run `npm ci`, `npm test`, `npm run check`.
2. Configure `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN` in the deployment
   environment, then `npx wrangler deploy`. The included SQLite migration creates
   `OperatorStore`; do not delete/recreate this namespace during redeploys.
3. Use interactive **Worker Secrets** prompts:

   ```sh
   npx wrangler secret put TELEGRAM_BOT_TOKEN
   npx wrangler secret put TELEGRAM_WEBHOOK_SECRET
   npx wrangler secret put SNAPSHOT_WRITE_TOKEN
   npx wrangler secret put PERMITTED_CHAT_ID
   npx wrangler secret put OPERATOR_USER_IDS
   ```

   Generate independent high-entropy webhook and publisher secrets (at least 32
   characters, Telegram secret alphabet A-Z/a-z/0-9/underscore/hyphen). The operator
   list is comma-separated numeric IDs. No bot/webhook secret belongs in wrangler
   `vars`. `TELEGRAM_WEBHOOK_SECRET` must match Telegram setWebhook's secret_token.
4. In GitHub, configure `OPERATOR_WORKER_URL` as a Variable containing the deployed
   HTTPS origin, and `OPERATOR_SNAPSHOT_WRITE_TOKEN` as a **Secret** matching the
   Worker's `SNAPSHOT_WRITE_TOKEN`. Never upload the original report or dedup state
   to Cloudflare. Run the existing agent workflow: it exports `operator_snapshot.json`
   and publishes via the authenticated endpoint. Inspect the publication step
   separately: publication failure is nonfatal to discovery/state persistence.
5. Check authenticated `/health` for a fresh snapshot. Keep polling mode unchanged.

## Safe cutover and real delivery verification

Pause operator commands, wait for active agent workflow runs to finish, and avoid the next scheduled poll during handoff.
Download the newest successful `dedup_state` artifact into the administration
working directory. Configure the script's environment secrets securely:
`TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET`, `OPERATOR_SNAPSHOT_WRITE_TOKEN`,
and the non-secret `OPERATOR_WORKER_URL`.

```sh
python scripts/manage_operator_webhook.py set --operators-paused
python scripts/manage_operator_webhook.py status
```

The setter acknowledges only the persisted polling offset, registers the webhook
without dropping pending updates, and verifies getWebhookInfo's URL. Polling may
receive Telegram's expected conflict while the webhook is installed; this does
not authorize switching mode yet. Do not run another getUpdates consumer.

Resume commands and send **an actual `/status` from an allowlisted human**. Verify
its real Telegram reply and the authenticated `/health` `last_delivery` timestamp
and command, after registration. Also verify another unauthorized user gets no
reply. A synthetic curl update or local fixture is not this acceptance test.
Only after these observations, explicitly set GitHub Variables:

- `TELEGRAM_OPERATOR_MODE=webhook`
- `TELEGRAM_WEBHOOK_VERIFIED=true`

Both are required to suppress polling. No build/deploy/export script sets them.
Run the agent again and verify `operator_commands.status=WEBHOOK_MODE`, snapshot
publication and unchanged discovery/state restoration. Snapshots update on the
existing schedule; real-time replies do not mean real-time on-chain evidence.

## Rollback

Pause commands. Check getWebhookInfo until pending_update_count is zero and allow
in-flight deliveries to finish. Inspect `/health` for ambiguous deliveries; users
must issue a new command for those. Then:

```sh
python scripts/manage_operator_webhook.py delete --operators-paused
```

The script refuses to discard pending updates and verifies webhook removal.
Set `TELEGRAM_OPERATOR_MODE=polling`, clear `TELEGRAM_WEBHOOK_VERIFIED`, retain
`TELEGRAM_OPERATOR_USER_IDS`, and run the scheduled agent workflow manually.
Retain all existing dedup artifacts and the Durable Object namespace/claims.
Do not restore an older candidate state or reset notifications. Resume commands
only after polling works. If the Worker is unavailable and pending messages cannot
be drained, recover it before handoff; do not silently drop/replay that backlog.

Commands: `/status`, `/worklist`, `/opportunity ID`, `/rewards`, `/monitor`.
They never expose private operator notes or change eligibility/approvals/wallets.
