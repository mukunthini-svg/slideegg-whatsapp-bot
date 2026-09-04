/**
 * One-time WhatsApp pairing.
 *
 * Two ways in, and the QR is the one that actually works:
 *
 *   QR  (default)  a QR is drawn in the workflow log. The log is already open
 *                  on a screen, so the phone camera reads it straight off that
 *                  screen — nothing to read out, nothing to type, and WhatsApp
 *                  refreshes it by itself, so there is no two-minute race.
 *   CODE           an 8-character code, for when the screen showing the log is
 *                  the same phone that has to accept it. Set WA_PAIR_MODE=code.
 *
 * Whichever is used, a fresh one replaces the old one every ROTATE_MS until the
 * phone connects: whatever is at the BOTTOM of the log is valid.
 *
 *   WA_PAIR_NUMBER   digits only with country code (only needed for code mode)
 *   WA_SESSION_KEY   the passphrase the session is encrypted with
 */
import makeWASocket, {
  useMultiFileAuthState, Browsers, fetchLatestBaileysVersion, DisconnectReason,
} from '@itsliaaa/baileys';
import fs from 'node:fs';
import path from 'node:path';
import qrTerminal from 'qrcode-terminal';
import QRCode from 'qrcode';
import { AUTH_DIR, SECRET, ROOT, connect, save, resolveChannel, log } from './wa.js';

const MODE = (process.env.WA_PAIR_MODE || 'qr').trim().toLowerCase();
const NUMBER = (process.env.WA_PAIR_NUMBER || '').replace(/\D/g, '');
const ROTATE_MS = 100000;
const ATTEMPTS = Number(process.env.WA_PAIR_ATTEMPTS || 8);
const QR_PNG = path.join(ROOT, 'qr.png');

const quiet = {
  level: 'silent', child: () => quiet,
  trace() {}, debug() {}, info() {}, warn() {}, error() {}, fatal() {},
};

const rule = (ch = '=') => console.log(ch.repeat(60));

function showQR(raw, n) {
  console.log('');
  rule();
  console.log(`   SCAN THIS WITH THE PHONE   (QR ${n})`);
  rule();
  console.log('');
  // Big blocks, not the compact half-block form: a camera pointed at a screen
  // needs the modules to be several pixels across to lock on.
  qrTerminal.generate(raw, { small: false }, (art) => console.log(art));
  console.log('');
  console.log('   On the phone holding the bot number:');
  console.log('     WhatsApp -> Linked devices -> Link a device');
  console.log('     -> point the camera at this QR on your screen');
  console.log('');
  console.log('   Too small? Zoom the browser in with Ctrl and +.');
  console.log('   A qr.png is also attached to this run under "Artifacts".');
  rule();
  console.log('');

  // A PNG as well: some log themes render the blocks at a contrast a camera
  // struggles with, and a downloaded image always scans.
  QRCode.toFile(QR_PNG, raw, { width: 600, margin: 2 })
    .catch(e => log('could not write qr.png:', e.message));
}

function showCode(code, n) {
  const pretty = code.length === 8 ? `${code.slice(0, 4)}-${code.slice(4)}` : code;
  console.log('');
  rule();
  console.log(`   PAIRING CODE  (${n} of ${ATTEMPTS}):   ${pretty}`);
  rule();
  console.log('   Type it WITHOUT the dash:  ' + code);
  console.log('   WhatsApp -> Linked devices -> Link a device');
  console.log('     -> "Link with phone number instead" -> type it');
  console.log('   If it expires, a NEW code appears below in ~100 seconds.');
  rule();
  console.log('');
}

/** One attempt: fresh socket, fresh QR or code, wait for the phone. */
function attempt(state, saveCreds, version, n) {
  return new Promise((resolve) => {
    const sock = makeWASocket({
      version, auth: state, logger: quiet,
      browser: Browsers.macOS('Desktop'),
      syncFullHistory: false, markOnlineOnConnect: false,
    });
    sock.ev.on('creds.update', saveCreds);

    let settled = false;
    let askedForCode = false;
    const finish = (ok) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (!ok) { try { sock.end(undefined); } catch { /* already down */ } }
      resolve(ok ? sock : null);
    };
    const timer = setTimeout(() => {
      log(`attempt ${n} went unused — starting another`);
      finish(false);
    }, ROTATE_MS);

    sock.ev.on('connection.update', async ({ connection, lastDisconnect, qr }) => {
      if (qr && !sock.authState.creds.registered) {
        if (MODE === 'code') {
          if (askedForCode) return;
          askedForCode = true;
          // Asking the instant the socket opens returns a 428; wait a beat.
          await new Promise(r => setTimeout(r, 3000));
          try {
            showCode(await sock.requestPairingCode(NUMBER), n);
          } catch (e) {
            log('could not request a code:', e.message);
            finish(false);
          }
        } else {
          // Baileys re-emits this every ~20s with a new QR; print each one so
          // the newest is always at the bottom of the log.
          showQR(qr, n);
        }
      }
      if (connection === 'open') { log('phone connected.'); finish(true); }
      if (connection === 'close') {
        const code = lastDisconnect?.error?.output?.statusCode;
        if (code === DisconnectReason.restartRequired) { log('paired.'); finish(true); }
        else if (settled) { /* handled */ }
        else if (code === DisconnectReason.loggedOut) {
          log('WhatsApp rejected this device.'); finish(false);
        }
      }
    });
  });
}

async function main() {
  if (MODE === 'code' && !/^\d{10,15}$/.test(NUMBER)) {
    throw new Error(
      'code mode needs WA_PAIR_NUMBER as digits only including the country ' +
      `code (e.g. 919363904228). Got: ${JSON.stringify(process.env.WA_PAIR_NUMBER || '')}`);
  }
  log(MODE === 'code'
    ? `pairing by code for ${NUMBER.replace(/\d(?=\d{4})/g, '*')}`
    : 'pairing by QR — scan it off the screen with the phone');

  fs.rmSync(AUTH_DIR, { recursive: true, force: true });
  fs.mkdirSync(AUTH_DIR, { recursive: true });
  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
  const { version } = await fetchLatestBaileysVersion();
  log('WhatsApp web version', version.join('.'));

  let paired = null;
  for (let n = 1; n <= ATTEMPTS && !paired; n++) {
    paired = await attempt(state, saveCreds, version, n);
  }
  if (!paired) {
    throw new Error(
      `nothing was scanned or entered across ${ATTEMPTS} tries. Re-run and use ` +
      'whatever is at the BOTTOM of the log.');
  }

  await new Promise(r => setTimeout(r, 8000));
  save();
  try { paired.end(undefined); } catch { /* already closing */ }
  fs.rmSync(QR_PNG, { force: true });   // do not leave a live QR lying around

  // Reconnect from the file just written. This is the point of the step: it
  // proves the saved session logs in, instead of a broken session looking like
  // success and failing later on a run nobody is watching.
  log('verifying the saved session by reconnecting with it...');
  await new Promise(r => setTimeout(r, 5000));
  const { sock: verified } = await connect({ mode: 'restore' });
  log('the saved session logs in correctly.');

  let jid = null;
  try {
    jid = await resolveChannel(verified);
  } catch (e) {
    log('channel lookup failed:', e.message);
  }
  if (jid) {
    console.log(`\nCHANNEL_JID=${jid}`);
    log('channel is reachable — the bot is ready to post.');
  } else {
    log('! Paired and logged in, but the SlideEgg channel was not found. ' +
        'Check that this number is still an admin of the channel.');
  }

  save();
  try { verified.end(undefined); } catch { /* already closing */ }
  log('done. The encrypted session is ready to commit.');
  process.exit(0);
}

main().catch((e) => {
  fs.rmSync(QR_PNG, { force: true });
  console.error('\nPAIRING FAILED:', e.message);
  process.exit(1);
});
