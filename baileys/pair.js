/**
 * One-time WhatsApp pairing.
 *
 * Prints an 8-character pairing code, waits for it to be entered on the phone,
 * then writes the encrypted session so every later run can just connect.
 *
 * A QR code is useless here — nobody is watching a terminal — so this uses
 * WhatsApp's "Link with phone number instead" flow, which is a short code that
 * survives being read out of a log.
 *
 *   WA_PAIR_NUMBER   digits only, with country code, e.g. 919363904228
 *   WA_SESSION_KEY   the passphrase the session is encrypted with
 */
import { connect, save, resolveChannel, log } from './wa.js';

const NUMBER = (process.env.WA_PAIR_NUMBER || '').replace(/\D/g, '');

function banner(code) {
  const pretty = code.length === 8 ? `${code.slice(0, 4)}-${code.slice(4)}` : code;
  const line = '='.repeat(52);
  console.log(`\n${line}`);
  console.log('   PAIRING CODE:   ' + pretty);
  console.log(line);
  console.log('   On the phone holding this number:');
  console.log('     WhatsApp -> Settings -> Linked devices');
  console.log('     -> Link a device -> "Link with phone number instead"');
  console.log('     -> type the code above');
  console.log('   The code expires in a couple of minutes.');
  console.log(`${line}\n`);
}

async function main() {
  if (!/^\d{10,15}$/.test(NUMBER)) {
    throw new Error(
      `WA_PAIR_NUMBER must be digits only including the country code ` +
      `(e.g. 919363904228). Got: ${JSON.stringify(process.env.WA_PAIR_NUMBER || '')}`);
  }
  log('pairing number', NUMBER.replace(/\d(?=\d{4})/g, '*'));

  const { sock } = await connect({
    mode: 'pair',
    onPairingReady: async (s) => {
      // Small delay: requesting the code the instant the socket opens is the
      // documented way to get a 428 back.
      await new Promise(r => setTimeout(r, 3000));
      const code = await s.requestPairingCode(NUMBER);
      banner(code);
    },
  });

  // Give WhatsApp a moment to finish writing the post-pair credentials
  // before they are packed up.
  await new Promise(r => setTimeout(r, 8000));
  save();

  // Confirm the channel is reachable now rather than discovering it is not on
  // the first real post.
  try {
    const jid = await resolveChannel(sock);
    if (jid) {
      console.log(`\nCHANNEL_JID=${jid}`);
      log('channel is reachable — the bot is ready to post.');
    } else {
      log('! paired, but the SlideEgg channel was not found. Check that this ' +
          'number is still an admin of the channel.');
    }
  } catch (e) {
    log('channel check failed (pairing itself is fine):', e.message);
  }

  await new Promise(r => setTimeout(r, 1500));
  save();
  log('done. The session is stored encrypted; commit it and the bot can run.');
  process.exit(0);
}

main().catch((e) => {
  console.error('\nPAIRING FAILED:', e.message);
  process.exit(1);
});
