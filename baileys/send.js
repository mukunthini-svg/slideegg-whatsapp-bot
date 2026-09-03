/**
 * The sender: one WhatsApp connection per run, driven over stdin.
 *
 * slideegg_daily.py keeps doing everything it already does — finding new
 * templates, de-duplicating, building captions, downloading images, writing the
 * log — and only the transport changes. Python starts this process once, writes
 * one JSON object per line, and reads one JSON reply per line:
 *
 *   ->  {"caption": "...", "media": "data:image/png;base64,..."}
 *   <-  {"ok": true}
 *
 * Opening a fresh WhatsApp connection for every single post would look like an
 * attack; holding one connection for the whole run is what a real linked
 * device does.
 *
 * Ready and shutdown are announced on the same channel:
 *   <-  {"ready": true, "channel": "...@newsletter"}
 *   <-  {"bye": true, "sent": 3, "failed": 0}
 */
import readline from 'node:readline';
import { connect, save, resolveChannel, log } from './wa.js';

const reply = (obj) => process.stdout.write(JSON.stringify(obj) + '\n');

/** "data:image/png;base64,xxxx" -> Buffer, or null if it is not a data URI. */
function mediaBuffer(media) {
  if (typeof media !== 'string') return null;
  const m = /^data:([^;,]+);base64,(.+)$/s.exec(media);
  if (!m) return null;
  const buf = Buffer.from(m[2], 'base64');
  return buf.length > 1024 ? buf : null;
}

async function main() {
  let sock;
  try {
    ({ sock } = await connect({ mode: 'restore' }));
  } catch (e) {
    reply({ ready: false, error: e.message });
    process.exit(2);
  }

  const channel = await resolveChannel(sock);
  if (!channel) {
    reply({ ready: false, error: 'the SlideEgg channel could not be found — is this number still an admin?' });
    try { save(); } catch { /* a failed lookup must not cost us the session */ }
    process.exit(3);
  }

  reply({ ready: true, channel });

  let sent = 0, failed = 0;
  const rl = readline.createInterface({ input: process.stdin, terminal: false });

  for await (const line of rl) {
    const raw = line.trim();
    if (!raw) continue;

    let job;
    try {
      job = JSON.parse(raw);
    } catch {
      reply({ ok: false, error: 'unparseable job line' });
      failed++;
      continue;
    }

    const caption = String(job.caption || '').trim();
    if (!caption) {
      reply({ ok: false, error: 'empty caption' });
      failed++;
      continue;
    }

    const image = mediaBuffer(job.media);
    try {
      if (image) {
        await sock.sendMessage(channel, { image, caption });
        reply({ ok: true, kind: 'image' });
      } else {
        // No usable image is not a reason to skip the post — the link and
        // title are the point, the picture is decoration.
        await sock.sendMessage(channel, { text: caption });
        reply({ ok: true, kind: 'text' });
      }
      sent++;
    } catch (e) {
      // Retry once as plain text: almost every send failure here is the media
      // upload, and a text post is far better than nothing.
      if (image) {
        try {
          await sock.sendMessage(channel, { text: caption });
          reply({ ok: true, kind: 'text-fallback', note: e.message });
          sent++;
          continue;
        } catch (e2) {
          reply({ ok: false, error: `${e.message} / fallback: ${e2.message}` });
          failed++;
          continue;
        }
      }
      reply({ ok: false, error: e.message });
      failed++;
    }

    // WhatsApp dislikes bursts. A short, slightly irregular gap between posts
    // looks like a person and costs nothing.
    await new Promise(r => setTimeout(r, 4000 + Math.random() * 3000));
  }

  // Credentials rotate during a session; saving on the way out is what keeps
  // the next run from having to re-pair.
  try {
    save();
  } catch (e) {
    log('! could not save the session:', e.message);
  }
  reply({ bye: true, sent, failed });

  // Close the socket, never logout() — logout unlinks the device and would
  // force a re-pair on the next run.
  try { sock.end?.(undefined); } catch { /* already closed */ }
  process.exit(failed && !sent ? 4 : 0);
}

main().catch((e) => {
  reply({ ready: false, error: e.message });
  process.exit(2);
});
