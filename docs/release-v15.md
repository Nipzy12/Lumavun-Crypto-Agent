# V1.5 production validation

Implementation commit: `a13f2739c26ff7a969b305efc5b8c2a8c25f0cdc`.
Production run: https://github.com/Nipzy12/Lumavun-Crypto-Agent/actions/runs/42254501360

- Local full suite: **581 passed in 20.18s**.
- Production CI full suite: **581 passed in 27.79s**.
- Restored state from V1.4.5 run `42250900372`.
- All eight sources reachable; four classified official.
- 36 historical candidates: 28 EVIDENCE_REVIEW, five BLOCKED, three EXPIRED.
- Zero ACTIONABLE candidates, eligible execution playbooks, submissions or confirmed receipts were fabricated.
- All 36 playbooks are evidence-gathering only with current evidence.
- SOL balance read successfully (0 SOL); SPL monitoring status `ok`.
- Transaction history status **RPC_UNAVAILABLE**, with two bounded attempts against one configured provider. No successful transaction cursor exists yet; this is not ZERO_TRANSFERS.
- Unresolved history gap: `UNRESOLVED_HISTORY_OR_TRANSACTION`.
- Persistent coverage limitation: `TOKEN_ACCOUNT_HISTORY_NOT_ENUMERATED`.
- Operator command polling: DISABLED pending explicit numeric user allowlist.
- Telegram messages sent: zero, preserving the already-sent daily summary key.

Operator setup still needed: configure `TELEGRAM_OPERATOR_USER_IDS` in repository
Variables and, if available, a second authorized provider via
`SOLANA_REWARD_RPC_URL` (Secret for credential-bearing URLs). This session's GitHub
integration cannot access the repository Variables API (HTTP 403), so it did not
alter those settings. No provider credentials, private keys or signatures were
requested or stored.

New regression coverage exercises lifecycle ordering and mandatory gates, grant
fit/draft separation, explicit authorization, optimistic revision conflicts,
idempotent decisions/migrations, RPC outage/recovery and pagination, transaction
identity/finality, stale state merges, untrusted destinations, incomplete SPL
evidence, allowlisted commands, concurrent/repeated updates and failed delivery.
All campaign/receipt fixtures used in tests are synthetic, not actual earnings.
