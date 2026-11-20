import test from 'node:test';
import assert from 'node:assert/strict';
import { solverReady } from '../src/solver.mjs';
test('remote paid solver cannot be used as Ollama URL', async () => {
  await assert.rejects(solverReady('x', { url: 'https://paid-api.example.com' }), /ZERO_COST_GUARD/);
});
test('unconfigured solver skips the network', async () => {
  assert.equal(await solverReady('', { fetcher: () => { throw Error('should not call'); } }), false);
});
