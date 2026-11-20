import { generateKeyPairSync, createPublicKey, createPrivateKey, sign, verify } from 'node:crypto';
import { mkdirSync, readFileSync, writeFileSync, existsSync } from 'node:fs';
import { join } from 'node:path';
export const root = process.cwd();
export const localDir = join(root, '.local');
export const privatePath = join(localDir, 'botcoin-game-key.pem');
export const publicPath = join(localDir, 'botcoin-public-key.txt');
export function generateLocalIdentity() {
  if (existsSync(privatePath)) throw new Error('Game key sudah ada. Tidak akan ditimpa.');
  mkdirSync(localDir, { recursive: true, mode: 0o700 });
  const { privateKey, publicKey } = generateKeyPairSync('ed25519');
  writeFileSync(privatePath, privateKey.export({ format: 'pem', type: 'pkcs8' }), { mode: 0o600, flag: 'wx' });
  const publicKeyB64 = publicKey.export({ format: 'der', type: 'spki' }).subarray(-32).toString('base64');
  writeFileSync(publicPath, publicKeyB64 + '\n', { mode: 0o644, flag: 'wx' });
  return publicKeyB64;
}
export function getPublicKey() {
  if (existsSync(publicPath)) return readFileSync(publicPath, 'utf8').trim();
  return process.env.BOTCOIN_PUBLIC_KEY?.trim() || '';
}
export function hasLocalIdentity() { return existsSync(privatePath); }
export function signedEnvelope(transaction) {
  if (!hasLocalIdentity()) throw new Error('Game key lokal belum dibuat. Jalankan identity-init di PC sendiri.');
  const key = createPrivateKey(readFileSync(privatePath));
  const signature = sign(null, Buffer.from(JSON.stringify(transaction)), key).toString('base64');
  return { transaction, signature };
}
export const serverPublicKeyB64 = 'EV4RO4uTSEYmxkq6fSoHC16teec6UJ9sfBxprIzDhxk=';
export function verifyGameResponse(body, signatureB64, timestamp) {
  const rawKey = Buffer.from(serverPublicKeyB64, 'base64');
  const spki = Buffer.concat([Buffer.from('302a300506032b6570032100', 'hex'), rawKey]);
  const serverKey = createPublicKey({ key: spki, format: 'der', type: 'spki' });
  if (!signatureB64 || !timestamp || !Number.isFinite(Number(timestamp))) return false;
  const message = Buffer.from(JSON.stringify({ body, timestamp: Number(timestamp) }));
  return verify(null, message, serverKey, Buffer.from(signatureB64, 'base64'));
}
