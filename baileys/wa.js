/**
 * Connecting to WhatsApp, and finding the channel to post into.
 *
 * Shared by pair.js (first-time login) and send.js (every run afterwards).
 */
import makeWASocket, {
  DisconnectReason, useMultiFileAuthState, Browsers, fetchLatestBaileysVersion,
} from '@itsliaaa/baileys';
import fs from 'node:fs';
import path from 'node:path';
import { restore, persist } from './session.js';

export const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
export const AUTH_DIR = path.join(ROOT, '.wa-auth');            // scratch, never committed
export const BLOB = path.join(ROOT, 'state', 'wa-session.enc'); // committed, encrypted
export const SECRET = process.env.WA_SESSION_KEY || '';

/** The channel invite from the public link: whatsapp.com/channel/<code> */
export const INVITE = (process.env.WA_CHANNEL_INVITE || '0029Vb7WIkq35fLwXKie5521').trim();

export const log = (...a) => console.error('[wa]', ...a);

/** Baileys is chatty at info level; only surface real problems. */
const quietLogger = {
  level: 'silent',
  child: () => quietLogger,
  trace: () => {}, debug: () => {}, info: () => {},
  warn: () => {}, error: () => {}, fatal: () => {},
};

/**
 * Open a connection. `mode` is 'restore' (a session must already exist) or
 * 'pair' (start fresh and expect the caller to request a pairing code).
 *
 * Resolves once WhatsApp reports the connection open.
 */
export async function connect({ mode = 'restore', onPairingReady = null } = {}) {
  fs.mkdirSync(AUTH_DIR, { recursive: true });

  if (mode === 'restore') {
    if (!restore(BLOB, AUTH_DIR, SECRET)) {
      throw new Error(
        'no saved WhatsApp session. Run the "Pair WhatsApp" workflow once to ' +
        'link the number, then this will work on every run.');
    }
    log('session restored from', path.relative(ROOT, BLOB));
  } else {
    fs.rmSync(AUTH_DIR, { recursive: true, force: true });
    fs.mkdirSync(AUTH_DIR, { recursive: true });
  }

  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
  const { version } = await fetchLatestBaileysVersion();
  log('WhatsApp web version', version.join('.'));

  const sock = makeWASocket({
    version,
    auth: state,
    logger: quietLogger,
    // Identify as a desktop browser: this is what a linked device looks like,
    // and it is what the pairing-code flow expects.
    browser: Browsers.macOS('Desktop'),
    syncFullHistory: false,
    markOnlineOnConnect: false,   // do not steal delivery from the real phone
    generateHighQualityLinkPreview: false,
  });

  sock.ev.on('creds.update', saveCreds);

  let askedForCode = false;
  await new Promise((resolve, reject) => {
    const timer = setTimeout(
      () => reject(new Error('timed out waiting for WhatsApp to connect')),
      mode === 'pair' ? 240000 : 90000);

    sock.ev.on('connection.update', async (u) => {
      const { connection, lastDisconnect, qr } = u;

      if (qr && mode === 'pair' && onPairingReady && !askedForCode) {
        askedForCode = true;
        // The QR is useless over a chat transcript; ask for the 8-character
        // code instead and let the caller print it.
        try {
          await onPairingReady(sock);
        } catch (e) {
          clearTimeout(timer);
          reject(e);
        }
      }

      if (connection === 'open') {
        clearTimeout(timer);
        log('connected as', sock.user?.id || '(unknown)');
        resolve();
      }

      if (connection === 'close') {
        const code = lastDisconnect?.error?.output?.statusCode;
        if (code === DisconnectReason.loggedOut) {
          clearTimeout(timer);
          reject(new Error(
            'WhatsApp logged this device out. The link was removed on the ' +
            'phone, or the number was blocked. Re-pair to continue.'));
        } else if (code === DisconnectReason.restartRequired && mode === 'pair') {
          // Normal immediately after pairing: the socket restarts once.
          clearTimeout(timer);
          resolve('restart');
        } else if (connection === 'close' && mode === 'restore') {
          clearTimeout(timer);
          reject(new Error(`connection closed before it opened (code ${code})`));
        }
      }
    });
  });

  return { sock, saveCreds };
}

/** Save the current auth folder back into the encrypted blob. */
export function save() {
  const bytes = persist(BLOB, AUTH_DIR, SECRET);
  log(`session saved (${bytes} bytes encrypted)`);
}

/**
 * Turn the public channel invite into the '...@newsletter' id Baileys sends to.
 *
 * Resolving by invite is the reliable route: the numeric id changes if the
 * channel is ever recreated, and the cached id is what broke the Whapi
 * version. Falls back to matching by name across subscribed channels.
 */
export async function resolveChannel(sock, { invite = INVITE, name = 'slideegg' } = {}) {
  try {
    const meta = await sock.newsletterMetadata('invite', invite);
    if (meta?.id) {
      log(`channel resolved by invite: ${meta.id} (${meta.name || 'unnamed'})`);
      return meta.id;
    }
  } catch (e) {
    log('invite lookup failed:', e.message);
  }

  try {
    const subscribed = await sock.newsletterSubscribed();
    const list = Array.isArray(subscribed) ? subscribed : (subscribed?.newsletters || []);
    const hit = list.find(n =>
      String(n?.name || n?.threadMetadata?.name?.text || '')
        .toLowerCase().includes(name.toLowerCase()));
    if (hit?.id) {
      log(`channel resolved by name: ${hit.id}`);
      return hit.id;
    }
    log(`no channel matched "${name}" among ${list.length} subscribed`);
  } catch (e) {
    log('subscribed lookup failed:', e.message);
  }

  return null;
}
