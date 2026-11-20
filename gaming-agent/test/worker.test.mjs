import test from 'node:test';
import assert from 'node:assert/strict';
import { workerTick } from '../src/worker.mjs';
import { readFileSync } from 'node:fs';
const cfg = JSON.parse(readFileSync(new URL('../config.json', import.meta.url)));
test('without game identity the worker only checks public health', async () => {
  const called = [];
  const game = {
    health: async () => { called.push('health'); return { status: 'healthy' }; },
    pick: async () => { called.push('pick'); throw Error('should not pick'); },
    solve: async () => { called.push('solve'); throw Error('should not solve'); }
  };
  const result = await workerTick(cfg, { game, log: () => {} });
  assert.equal(result.reason, 'NEED_GAME_IDENTITY');
  assert.deepEqual(called, ['health']);
});
