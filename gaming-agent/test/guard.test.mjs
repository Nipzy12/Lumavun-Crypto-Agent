import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { validateConfig, assertAllowedAction } from '../src/guard.mjs';
const cfg = JSON.parse(readFileSync(new URL('../config.json', import.meta.url)));
test('V1 fixed settings pass', () => { assert.doesNotThrow(() => validateConfig(cfg)); });
test('cannot disable zero spending', () => {
  for (const patch of [{ maxSpending: 1 }, { strictZeroExpense: false }, { payoutMode: 'direct' }]) {
    assert.throws(() => validateConfig({ ...cfg, ...patch }), /CONFIG_GUARD/);
  }
});
test('all financial endpoints fail even with zero declared cost', () => {
  for (const path of ['/api/claim-onchain', '/api/transfer', '/api/gas-station/subscribe']) {
    assert.throws(() => assertAllowedAction(path, { method: 'POST', moneyCost: 0 }), /SPEND_BLOCKED/);
  }
});
test('financial request and arbitrary write fail', () => {
  assert.throws(() => assertAllowedAction('/api/hunts/pick', { method: 'POST', moneyCost: 0.0001 }), /SPEND_BLOCKED/);
  assert.throws(() => assertAllowedAction('/api/tokens/mint', { method: 'POST', moneyCost: 0 }), /WRITE_BLOCKED/);
});
test('allowed game pick and balance read', () => {
  assert.doesNotThrow(() => assertAllowedAction('/api/hunts/pick', { method: 'POST' }));
  assert.doesNotThrow(() => assertAllowedAction('/api/balance/abc%2Fdef%3D', { method: 'GET' }));
});
test('unapproved HTTP methods fail closed', () => {
  assert.throws(() => assertAllowedAction('/api/health', { method: 'DELETE' }), /METHOD_BLOCKED/);
});
