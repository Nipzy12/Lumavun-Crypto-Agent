export const SAFE_READ_PATHS = new Set([
  '/api/health', '/api/ticker', '/api/coins/stats', '/api/hunts', '/api/gas'
]);
const BLOCKED_PATHS = new Set([
  '/api/claim-onchain', '/api/transfer', '/api/gas-station/subscribe'
]);
export function validateConfig(cfg) {
  if (cfg.autoplay !== true || cfg.strictZeroExpense !== true ||
      cfg.intervalMinutes !== 15 || cfg.payoutMode !== 'hold' ||
      cfg.maxSpending !== 0 || cfg.sharePrivateKeys !== false) {
    throw new Error('CONFIG_GUARD: konfigurasi zero-capital V1 harus tetap ketat.');
  }
  if (cfg.game !== 'botcoin') throw new Error('Game connector belum diizinkan.');
  if (cfg.solver?.maximumSolveAttemptsPerHunt !== 1) throw new Error('Maksimal 1 tebakan/hunt.');
}
export function assertAllowedAction(path, { method = 'GET', moneyCost = 0 } = {}) {
  if (!Number.isFinite(moneyCost) || moneyCost !== 0) throw new Error('SPEND_BLOCKED: pengeluaran tidak diizinkan.');
  if (BLOCKED_PATHS.has(path)) throw new Error('SPEND_BLOCKED: payout/transfer/subscription dinonaktifkan.');
  if (!['GET', 'POST'].includes(method)) throw new Error('METHOD_BLOCKED: unsupported method.');
  const allowedWrites = new Set(['/api/link-wallet', '/api/hunts/pick', '/api/hunts/solve', '/api/register']);
  if (method === 'POST' && !allowedWrites.has(path)) throw new Error('WRITE_BLOCKED: action tidak terdaftar.');
  if (method === 'GET' && !SAFE_READ_PATHS.has(path) &&
      !/^\/api\/balance\/[A-Za-z0-9+%_=-]+$/.test(path) &&
      !/^\/api\/register\/challenge\?publicKey=/.test(path)) {
    throw new Error('READ_BLOCKED: endpoint tidak ada di allowlist.');
  }
}
export function ensureSafeKeyInput(input) {
  const s = String(input || '');
  if (/\b(seed phrase|mnemonic|private key|secret key)\b/i.test(s)) throw new Error('Jangan memasukkan seed phrase/private key.');
}
