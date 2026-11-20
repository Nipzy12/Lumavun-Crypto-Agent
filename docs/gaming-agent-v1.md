# Zero Capital Gaming Agent V1 (Botcoin)

The gaming-agent/ directory adds an independent Node.js Botcoin adapter alongside the existing Python airdrop/quest agent. It does not modify the Python orchestrator, claim engine, wallet monitor, or Telegram workflow.

**V1 settings:** autoplay=true; strictZeroExpense=true; intervalMinutes=15; payoutMode=hold; maxSpending=0; sharePrivateKeys=false.

Zero spending means **no cryptocurrency, fiat deposit, paid API, NFT purchase, network gas, claim or transfer**. Authorized free gameplay consumes the game's non-transferable gas credits, not cryptocurrency.

## GitHub Actions monitoring

The standalone workflow .github/workflows/gaming-agent.yml runs on a best-effort 15-minute GitHub schedule. It only reads API health and, optionally, the game balance when the public BOTCOIN_PUBLIC_KEY Repository Variable is configured.

**No signing key exists in CI. CI never plays, transfers, claims, or signs.** Without an identity, the expected result is NEED_GAME_IDENTITY. With only a public key, it is READ_ONLY_WITHOUT_LOCAL_GAME_KEY.

## Local gameplay setup

Inside gaming-agent/ with Node.js 20+ and a locally installed Ollama model:

1. Run npm test, then node src/index.mjs doctor.
2. Run node src/index.mjs identity-init on your own machine to create the separate Ed25519 *game* key in .local/.
3. Run node src/index.mjs challenge. Complete the game's required X account verification, then run register with the challenge ID, answer and tweet URL.
4. Set the **public** MetaMask Base address in LUMAVUN_BASE_ADDRESS, then run node src/index.mjs link-wallet.
5. Set OLLAMA_MODEL to an already installed local model; run node src/index.mjs run-once to test, then node src/index.mjs run for 15-minute cycles.

Never commit .local/, .env or any private key. Never put MetaMask seed phrases or passwords into environment variables. Game wallet keys are different from MetaMask keys and must stay local.

Puzzle rewards (coins/shares) are not proven liquid crypto income. Claims and payouts are deliberately disabled. A 15-minute cloud check is **monitoring**, not autonomous cloud gameplay. External API runtime and reward liquidity are not verified by the local tests.
