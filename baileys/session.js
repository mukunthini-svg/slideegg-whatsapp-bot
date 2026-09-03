/**
 * The WhatsApp login, packed into one encrypted file.
 *
 * Baileys keeps its credentials as a folder of small JSON files. That folder
 * IS the WhatsApp login — anyone holding a copy can read and send as this
 * number — and the repository it has to live in is public, so it is stored
 * as a single AES-256-GCM blob keyed on the WA_SESSION_KEY secret. GitHub
 * never exposes secrets on a public repo, so the committed file is inert to
 * everyone else.
 *
 * The folder is only ever JSON files one level deep, so it is packed as a
 * plain {filename: contents} map rather than a tarball: no extra dependency,
 * and a corrupted blob fails loudly at JSON.parse instead of silently
 * restoring half a session.
 */
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import zlib from 'node:zlib';

const MAGIC = Buffer.from('SEWA1');   // format marker, so a wrong file fails clearly
const IV_LEN = 12;
const TAG_LEN = 16;

function keyOf(secret) {
  if (!secret || String(secret).length < 16) {
    throw new Error(
      'WA_SESSION_KEY is missing or shorter than 16 characters. It is the ' +
      'only thing protecting the WhatsApp login in a public repo.');
  }
  return crypto.createHash('sha256').update(String(secret)).digest();
}

export function encrypt(plain, secret) {
  const iv = crypto.randomBytes(IV_LEN);
  const cipher = crypto.createCipheriv('aes-256-gcm', keyOf(secret), iv);
  const body = Buffer.concat([cipher.update(plain), cipher.final()]);
  return Buffer.concat([MAGIC, iv, cipher.getAuthTag(), body]);
}

export function decrypt(blob, secret) {
  if (blob.length < MAGIC.length + IV_LEN + TAG_LEN) {
    throw new Error('session blob is truncated');
  }
  if (!blob.subarray(0, MAGIC.length).equals(MAGIC)) {
    throw new Error('not a session file (bad magic) — was it committed as text?');
  }
  let off = MAGIC.length;
  const iv = blob.subarray(off, off += IV_LEN);
  const tag = blob.subarray(off, off += TAG_LEN);
  const decipher = crypto.createDecipheriv('aes-256-gcm', keyOf(secret), iv);
  decipher.setAuthTag(tag);
  try {
    return Buffer.concat([decipher.update(blob.subarray(off)), decipher.final()]);
  } catch {
    // GCM only fails this way on a wrong key or a modified file.
    throw new Error(
      'could not decrypt the session: WA_SESSION_KEY does not match the one ' +
      'used to pair, or the file was altered. Re-pair to start a new session.');
  }
}

/** Read an auth folder into a {filename: contents} map. */
function readFolder(dir) {
  const out = {};
  for (const name of fs.readdirSync(dir)) {
    const full = path.join(dir, name);
    if (fs.statSync(full).isFile()) out[name] = fs.readFileSync(full, 'utf8');
  }
  return out;
}

/** Write a {filename: contents} map back out as an auth folder. */
function writeFolder(dir, files) {
  fs.mkdirSync(dir, { recursive: true });
  for (const [name, contents] of Object.entries(files)) {
    // Defend against a tampered blob trying to escape the folder.
    if (name.includes('/') || name.includes('\\') || name.includes('..')) {
      throw new Error(`refusing to write suspicious session filename: ${name}`);
    }
    fs.writeFileSync(path.join(dir, name), contents);
  }
}

/**
 * Restore the auth folder from an encrypted blob.
 * Returns false when there is no session yet — the caller should pair.
 */
export function restore(blobPath, dir, secret) {
  if (!fs.existsSync(blobPath)) return false;
  const stat = fs.statSync(blobPath);
  if (stat.size === 0) return false;

  const files = JSON.parse(
    zlib.gunzipSync(decrypt(fs.readFileSync(blobPath), secret)).toString('utf8'));
  if (!files || typeof files !== 'object' || !files['creds.json']) {
    throw new Error('session file has no creds.json — it is not a usable login');
  }
  fs.rmSync(dir, { recursive: true, force: true });
  writeFolder(dir, files);
  return true;
}

/**
 * Pack the auth folder back into the encrypted blob.
 *
 * Returns 0 when the credentials are unchanged and nothing was written. That
 * check matters more than it looks: every encryption uses a fresh IV, so an
 * unchanged session would still produce a different file, and the scheduled job
 * would commit a "new" session to git every single hour for ever.
 */
export function persist(blobPath, dir, secret) {
  const files = readFolder(dir);
  if (!files['creds.json']) {
    throw new Error('refusing to save a session with no creds.json');
  }

  if (fs.existsSync(blobPath) && fs.statSync(blobPath).size > 0) {
    try {
      const current = zlib.gunzipSync(
        decrypt(fs.readFileSync(blobPath), secret)).toString('utf8');
      if (current === JSON.stringify(files)) return 0;
    } catch {
      // Unreadable or keyed differently — overwrite it with what works now.
    }
  }

  fs.mkdirSync(path.dirname(blobPath), { recursive: true });
  const packed = encrypt(
    zlib.gzipSync(Buffer.from(JSON.stringify(files)), { level: 9 }), secret);
  // Write-then-rename, so an interrupted run cannot leave a half-written
  // session behind — that would lock the number out until a re-pair.
  const tmp = `${blobPath}.tmp`;
  fs.writeFileSync(tmp, packed);
  fs.renameSync(tmp, blobPath);
  return packed.length;
}
