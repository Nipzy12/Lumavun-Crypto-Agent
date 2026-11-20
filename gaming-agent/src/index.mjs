import { readFileSync } from 'node:fs';
import { Botcoin } from './botcoin.mjs';
import { generateLocalIdentity, getPublicKey, hasLocalIdentity } from './identity.mjs';
import { validateConfig } from './guard.mjs';
import { workerTick } from './worker.mjs';
const config = JSON.parse(readFileSync(new URL('../config.json', import.meta.url), 'utf8'));
validateConfig(config);
const command = process.argv[2] || 'help';
const game = new Botcoin();
function argument(name) { const ix = process.argv.indexOf(name); return ix >= 0 ? process.argv[ix + 1] : undefined; }
function safeJson(x) { console.log(JSON.stringify(x, null, 2)); }
async function main() {
  switch (command) {
    case 'identity-init': {
      const publicKey = generateLocalIdentity();
      safeJson({ status: 'LOCAL_GAME_KEY_CREATED', publicKey, message: 'Kunci lokal hanya berada di folder .local. Jangan unggah folder ini.' });
      break;
    }
    case 'doctor': {
      safeJson({ config, gameIdentityReady: Boolean(getPublicKey()), signingKeyLocal: hasLocalIdentity(),
        metamaskAddressSupplied: Boolean(process.env.LUMAVUN_BASE_ADDRESS),
        note: 'Belum tentu terdaftar/terhubung dengan game. Tidak ada dana yang dapat dibelanjakan bot.' }); break;
    }
    case 'challenge': safeJson(await game.challenge()); break;
    case 'register': {
      const challengeId = argument('--challenge-id'), answer = argument('--answer'), tweet = argument('--tweet-url');
      if (!challengeId || !answer || !tweet) throw new Error('register --challenge-id ID --answer JAWABAN --tweet-url URL_X');
      safeJson(await game.register(challengeId, answer, tweet)); break;
    }
    case 'link-wallet': {
      const address = process.env.LUMAVUN_BASE_ADDRESS;
      if (!address) throw new Error('Set LUMAVUN_BASE_ADDRESS dengan public address MetaMask.');
      safeJson(await game.linkWallet(address)); break;
    }
    case 'status': {
      const out = { health: await game.health() };
      if (getPublicKey()) { out.balance = await game.balance(); out.gas = await game.gas(); }
      safeJson(out); break;
    }
    case 'run-once': await workerTick(config); break;
    case 'run': {
      console.log('LUMAVUN V1: polling tiap 15 menit, pengeluaran 0, mode payout hold');
      while (true) {
        try { await workerTick(config); } catch (e) { console.error(JSON.stringify({ status: 'ERROR', message: e.message })); }
        await new Promise(resolve => setTimeout(resolve, config.intervalMinutes * 60_000));
      }
    }
    default:
      console.log('LUMAVUN Zero Capital Gaming Agent V1\nCommands:\n' +
        '  node src/index.mjs doctor\n  node src/index.mjs identity-init\n  node src/index.mjs challenge\n' +
        '  node src/index.mjs register --challenge-id ID --answer ANSWER --tweet-url URL\n' +
        '  node src/index.mjs link-wallet\n  node src/index.mjs status\n  node src/index.mjs run-once\n  node src/index.mjs run');
  }
}
main().catch(e => { console.error('ERROR: ' + e.message); process.exitCode = 1; });
