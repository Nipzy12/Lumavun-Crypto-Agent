import { readFileSync, writeFileSync, mkdirSync, existsSync, renameSync } from 'node:fs';
import { join } from 'node:path';
import { Botcoin } from './botcoin.mjs';
import { hasLocalIdentity, getPublicKey, localDir } from './identity.mjs';
import { solverReady, solveLocally } from './solver.mjs';
const statePath = join(localDir, 'state.json');
export function readState() {
  if (!existsSync(statePath)) return {};
  return JSON.parse(readFileSync(statePath, 'utf8'));
}
export function writeState(state) {
  mkdirSync(localDir, { recursive: true, mode: 0o700 });
  const temp = statePath + '.tmp';
  writeFileSync(temp, JSON.stringify(state, null, 2), { mode: 0o600 });
  renameSync(temp, statePath);
}
export async function workerTick(cfg, { game = new Botcoin(), ready = solverReady, solver = solveLocally, log = console.log } = {}) {
  const result = { timestamp: new Date().toISOString(), mode: 'MONITOR', game: cfg.game, spending: 0 };
  result.health = await game.health();
  if (!getPublicKey()) { result.reason = 'NEED_GAME_IDENTITY'; log(JSON.stringify(result)); return result; }
  result.balances = await game.balance();
  try { result.gas = await game.gas(); } catch (e) { result.gasError = e.message; }
  if (!cfg.autoplay) { result.reason = 'AUTOPLAY_DISABLED'; log(JSON.stringify(result)); return result; }
  if (!hasLocalIdentity()) { result.reason = 'READ_ONLY_WITHOUT_LOCAL_GAME_KEY'; log(JSON.stringify(result)); return result; }
  if (!process.env.LUMAVUN_BASE_ADDRESS) { result.reason = 'NEED_METAMASK_PUBLIC_ADDRESS_AND_LINK'; log(JSON.stringify(result)); return result; }
  const model = process.env.OLLAMA_MODEL || cfg.solver.model;
  const ollamaUrl = process.env.OLLAMA_URL || 'http://127.0.0.1:11434';
  if (!await ready(model, { url: ollamaUrl })) { result.reason = 'LOCAL_SOLVER_NOT_READY'; log(JSON.stringify(result)); return result; }
  const state = readState();
  if (state.activeHunt && state.activeHunt.expiresAt && Date.now() > Date.parse(state.activeHunt.expiresAt)) {
    delete state.activeHunt; writeState(state);
  }
  if (state.cooldownUntil && Date.now() < Date.parse(state.cooldownUntil)) {
    result.reason = 'GAME_24H_COOLDOWN'; log(JSON.stringify(result)); return result;
  }
  let active = state.activeHunt;
  if (!active) {
    const huntsData = await game.hunts();
    const hunts = Array.isArray(huntsData.hunts) ? huntsData.hunts : [];
    const candidate = hunts.find(h => Number.isInteger(Number(h.id)) && !state.attemptedHunts?.includes(Number(h.id)) && !state.pickedHunts?.includes(Number(h.id)));
    if (!candidate) { result.reason = 'NO_AVAILABLE_HUNTS'; log(JSON.stringify(result)); return result; }
    const picked = await game.pick(Number(candidate.id));
    active = { id: Number(candidate.id), poem: picked.poem, expiresAt: picked.expiresAt, submitted: false };
    state.activeHunt = active;
    state.pickedHunts = [...new Set([...(state.pickedHunts || []), active.id])];
    writeState(state);
  }
  if (active.submitted) { result.reason = 'ALREADY_SUBMITTED'; log(JSON.stringify(result)); return result; }
  if (!active.poem) { result.reason = 'MISSING_RIDDLE_TEXT'; log(JSON.stringify(result)); return result; }
  const proposal = await solver(active.poem, model, { url: ollamaUrl });
  result.candidateConfidence = proposal.confidence;
  if (!proposal.answer || proposal.confidence < cfg.solver.minimumConfidence) {
    result.reason = 'INSUFFICIENT_CONFIDENCE_HOLD'; log(JSON.stringify(result)); return result;
  }
  active.submitted = true;
  state.attemptedHunts = [...new Set([...(state.attemptedHunts || []), active.id])];
  writeState(state);
  try {
    const solved = await game.solve(active.id, proposal.answer);
    result.mode = 'GAMEPLAY'; result.solutionStatus = solved.success === true ? 'SOLVED' : 'SUBMITTED';
    result.gameReward = solved.success ? { coinId: solved.coinId, shares: solved.shares, status: 'HELD_IN_GAME' } : null;
    if (solved.success) {
      delete state.activeHunt; state.cooldownUntil = new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString();
      writeState(state);
    }
  } catch (e) { result.solutionStatus = 'REJECTED_OR_UNVERIFIED'; result.solutionError = e.message; }
  log(JSON.stringify(result)); return result;
}
