import test from 'node:test';
import assert from 'node:assert/strict';
import { Botcoin } from '../src/botcoin.mjs';
test('game API calls target trusted domain, strictly GET, with read-only test client', async () => {
  const calls = [];
  const client = new Botcoin({ requireSignatures: false,
    fetcher: async (url, args) => {
      calls.push({ url, args });
      return { ok: true, status: 200, headers: { get: () => null }, text: async () => '{"status":"healthy"}' };
    }
  });
  const out = await client.health();
  assert.equal(out.status, 'healthy');
  assert.equal(calls[0].url, 'https://botfarmer.ai/api/health');
  assert.equal(calls[0].args.method, 'GET');
});
test('server signature missing is rejected by default', async () => {
  const client = new Botcoin({ fetcher: async () => ({
    ok: true, status: 200, headers: { get: () => null }, text: async () => '{"status":"healthy"}'
  }) });
  await assert.rejects(client.health(), /BAD_SERVER_SIGNATURE/);
});
test('wallet private keys cannot be passed as addresses', async () => {
  const client = new Botcoin();
  assert.throws(() => client.linkWallet('seed phrase garbage'), /Alamat MetaMask/);
});
