// QR pairing fallback: prints a scannable QR in the Actions log.
// Used when the pair workflow is run with the phone box set to "qr".
import makeWASocket, { useMultiFileAuthState, Browsers, fetchLatestBaileysVersion } from '@itsliaaa/baileys';
import { execSync } from 'node:child_process';
import fs from 'node:fs';
import { AUTH_DIR, save, log } from './wa.js';

let qrterm = null;
try { qrterm = (await import('qrcode-terminal')).default; } catch { log('installing qrcode-terminal'); execSync('npm i --no-save --no-audit --no-fund qrcode-terminal@0.12.0', { stdio: 'inherit' }); qrterm = (await import('qrcode-terminal')).default; }

const quiet = { level: 'silent', child: () => quiet, trace: () => {}, debug: () => {}, info: () => {}, warn: () => {}, error: () => {}, fatal: () => {} };

fs.rmSync(AUTH_DIR, { recursive: true, force: true });
fs.mkdirSync(AUTH_DIR, { recursive: true });

const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
const { version } = await fetchLatestBaileysVersion();
log('WhatsApp web version', version.join('.'));

const sock = makeWASocket({ version, auth: state, logger: quiet, browser: Browsers.macOS('Desktop'), syncFullHistory: false, markOnlineOnConnect: false });
sock.ev.on('creds.update', saveCreds);

let done = false;
let shots = 0;
const finish = async (why) => { if (done) return; done = true; log('finishing:', why); await new Promise(r => setTimeout(r, 6000)); save(); log('session saved.'); process.exit(0); };

sock.ev.on('connection.update', async (u) => { if (u.qr) { shots += 1; console.log('==== SCAN THIS QR #' + shots + ' ===='); qrterm.generate(u.qr, { small: true }); console.log('WhatsApp -> Settings -> Linked devices -> Link a device -> scan'); } if (u.connection === 'open') { log('connected as', sock.user?.id || '?'); await finish('connection open'); } if (u.connection === 'close') { const code = u.lastDisconnect?.error?.output?.statusCode; log('socket closed, code', code || '?'); if (code === 515) await finish('restart required after scan'); } });

setTimeout(() => { log('timed out waiting for the scan'); process.exit(1); }, 280000);
