/**
 * Tests for the encrypted session store.
 *
 * This file holds the WhatsApp login and lives in a PUBLIC repository, so the
 * failure modes that matter are: it decrypts with the wrong key, it silently
 * accepts a corrupted blob, or it saves something unusable and locks the number
 * out until someone re-pairs.
 */
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { encrypt, decrypt, restore, persist } from './session.js';

let fails = [];
const check = (name, cond, extra) => {
  console.log(`  ${cond ? 'PASS' : 'FAIL'}  ${name}` +
    (!cond && extra !== undefined ? `  -> ${extra}` : ''));
  if (!cond) fails.push(name);
};
const threw = (fn, re) => {
  try { fn(); return false; } catch (e) { return re ? re.test(e.message) : true; }
};

const KEY = 'a-long-enough-test-key-0123456789';
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'wa-sess-'));
const authDir = path.join(tmp, 'auth');
const blob = path.join(tmp, 'state', 'wa-session.enc');

const CREDS = JSON.stringify({ me: { id: '919363904228:1@s.whatsapp.net' }, noiseKey: 'x' });

function seedAuth() {
  fs.rmSync(authDir, { recursive: true, force: true });
  fs.mkdirSync(authDir, { recursive: true });
  fs.writeFileSync(path.join(authDir, 'creds.json'), CREDS);
  fs.writeFileSync(path.join(authDir, 'pre-key-1.json'), '{"private":"aaa"}');
  fs.writeFileSync(path.join(authDir, 'app-state-sync-key-AAA.json'), '{"k":"v"}');
}

console.log('\nENCRYPTION');
const secret = Buffer.from('the whatsapp login');
check('round-trips', decrypt(encrypt(secret, KEY), KEY).equals(secret));
check('ciphertext does not contain the plaintext',
  !encrypt(secret, KEY).toString('latin1').includes('whatsapp login'));
check('two encryptions of the same data differ (random IV)',
  !encrypt(secret, KEY).equals(encrypt(secret, KEY)));
check('wrong key is rejected, not silently wrong',
  threw(() => decrypt(encrypt(secret, KEY), 'another-key-that-is-long-enough'),
    /WA_SESSION_KEY does not match/));
check('tampered ciphertext is rejected', threw(() => {
  const bad = encrypt(secret, KEY);
  bad[bad.length - 1] ^= 0xff;
  decrypt(bad, KEY);
}, /does not match|altered/));
check('a short key is refused outright',
  threw(() => encrypt(secret, 'tooshort'), /shorter than 16/));
check('an empty key is refused', threw(() => encrypt(secret, ''), /missing/));
check('a non-session file is rejected clearly',
  threw(() => decrypt(Buffer.from('hello there, not a session at all'), KEY), /bad magic/));
check('a truncated blob is rejected',
  threw(() => decrypt(encrypt(secret, KEY).subarray(0, 20), KEY), /truncated/));

console.log('\nSAVE / RESTORE');
check('no file yet -> restore reports "not paired"', restore(blob, authDir, KEY) === false);

seedAuth();
const size = persist(blob, authDir, KEY);
check('saving writes the blob', fs.existsSync(blob) && size > 0);
check('no leftover .tmp file', !fs.existsSync(`${blob}.tmp`));
check('the saved file is not readable as text',
  !fs.readFileSync(blob).toString('latin1').includes('919363904228'));

fs.rmSync(authDir, { recursive: true, force: true });
check('restore rebuilds the folder', restore(blob, authDir, KEY) === true);
check('creds.json survives exactly',
  fs.readFileSync(path.join(authDir, 'creds.json'), 'utf8') === CREDS);
check('every key file comes back', fs.readdirSync(authDir).length === 3);
check('pre-keys survive',
  fs.readFileSync(path.join(authDir, 'pre-key-1.json'), 'utf8') === '{"private":"aaa"}');

console.log('\nNOT REWRITING AN UNCHANGED SESSION');
seedAuth();
persist(blob, authDir, KEY);
const before = fs.readFileSync(blob);
check('an unchanged session is not rewritten', persist(blob, authDir, KEY) === 0);
check('the file on disk is untouched', fs.readFileSync(blob).equals(before));
fs.writeFileSync(path.join(authDir, 'pre-key-2.json'), '{"private":"bbb"}');
check('a changed session IS rewritten', persist(blob, authDir, KEY) > 0);
check('the new key is in the saved session',
  restore(blob, authDir, KEY) && fs.existsSync(path.join(authDir, 'pre-key-2.json')));

console.log('\nREFUSING TO BREAK THE LOGIN');
fs.rmSync(authDir, { recursive: true, force: true });
fs.mkdirSync(authDir, { recursive: true });
fs.writeFileSync(path.join(authDir, 'pre-key-9.json'), '{}');
check('will not save a folder with no creds.json',
  threw(() => persist(blob, authDir, KEY), /no creds\.json/));

seedAuth();
persist(blob, authDir, KEY);
check('a good session is still intact after that refusal',
  restore(blob, authDir, KEY) === true &&
  fs.readFileSync(path.join(authDir, 'creds.json'), 'utf8') === CREDS);

check('restoring with the wrong key fails loudly',
  threw(() => restore(blob, authDir, 'a-different-key-0123456789'), /does not match/));

// An empty file is what an interrupted write or a bad checkout looks like.
fs.writeFileSync(blob, '');
check('an empty blob is treated as "not paired", not a crash',
  restore(blob, authDir, KEY) === false);

fs.rmSync(tmp, { recursive: true, force: true });
console.log(fails.length ? `\nFAILURES: ${JSON.stringify(fails)}` : '\nALL PASS');
process.exit(fails.length ? 1 : 0);
