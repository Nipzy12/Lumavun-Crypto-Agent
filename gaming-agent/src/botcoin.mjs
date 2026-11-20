import { assertAllowedAction } from './guard.mjs';
import { getPublicKey, signedEnvelope, verifyGameResponse } from './identity.mjs';
const ORIGIN = 'https://botfarmer.ai';
export class Botcoin {
  constructor({ fetcher = globalThis.fetch, requireSignatures = true } = {}) {
    this.fetcher = fetcher; this.requireSignatures = requireSignatures;
  }
  async request(path, { method = 'GET', transaction, publicKey, signed = false } = {}) {
    assertAllowedAction(path, { method, moneyCost: 0 });
    const headers = { Accept: 'application/json' };
    if (publicKey) headers['X-Public-Key'] = publicKey;
    let body;
    if (method === 'POST') {
      headers['Content-Type'] = 'application/json';
      body = JSON.stringify(signed ? signedEnvelope(transaction) : transaction);
    }
    const response = await this.fetcher(ORIGIN + path, {
      method, headers, body, signal: AbortSignal.timeout(15000)
    });
    const text = await response.text();
    let data;
    try { data = text ? JSON.parse(text) : {}; }
    catch { throw new Error('Botcoin returned non-JSON (' + response.status + ')'); }
    const signature = response.headers.get('X-Botcoin-Signature');
    const timestamp = response.headers.get('X-Botcoin-Timestamp');
    if (this.requireSignatures && !verifyGameResponse(data, signature, timestamp)) {
      throw new Error('BAD_SERVER_SIGNATURE: game response cannot be trusted');
    }
    if (!response.ok) throw new Error('Botcoin HTTP ' + response.status + ': ' + String(data.error || data.message || 'unknown').slice(0, 150));
    return data;
  }
  health() { return this.request('/api/health'); }
  ticker() { return this.request('/api/ticker'); }
  hunts() { return this.request('/api/hunts', { publicKey: getPublicKey() }); }
  gas() { return this.request('/api/gas', { publicKey: getPublicKey() }); }
  balance() {
    const pk = getPublicKey();
    if (!pk) throw new Error('Game public key belum tersedia.');
    return this.request('/api/balance/' + encodeURIComponent(pk));
  }
  challenge() {
    const pk = getPublicKey();
    if (!pk) throw new Error('Jalankan identity-init terlebih dahulu.');
    return this.request('/api/register/challenge?publicKey=' + encodeURIComponent(pk));
  }
  register(challengeId, challengeAnswer, tweetUrl) {
    if (!/^https:\/\/(x\.com|twitter\.com)\/[^/]+\/status\/\d+/.test(tweetUrl)) {
      throw new Error('Gunakan URL tweet verifikasi X yang valid.');
    }
    return this.request('/api/register', { method: 'POST', transaction: {
      publicKey: getPublicKey(), challengeId, challengeAnswer, tweetUrl
    } });
  }
  linkWallet(baseAddress) {
    if (!/^0x[a-fA-F0-9]{40}$/.test(baseAddress)) throw new Error('Alamat MetaMask EVM tidak valid.');
    const transaction = { type: 'link_wallet', publicKey: getPublicKey(), baseAddress, timestamp: Date.now() };
    return this.request('/api/link-wallet', { method: 'POST', signed: true, transaction });
  }
  pick(huntId) {
    const transaction = { type: 'pick', huntId, publicKey: getPublicKey(), timestamp: Date.now() };
    return this.request('/api/hunts/pick', { method: 'POST', signed: true, transaction });
  }
  solve(huntId, answer) {
    const transaction = { type: 'solve', huntId, answer, publicKey: getPublicKey(), timestamp: Date.now() };
    return this.request('/api/hunts/solve', { method: 'POST', signed: true, transaction });
  }
  // Deliberately NO on-chain claim, wallet signing, transfers, purchases, or paid subscription methods.
}
