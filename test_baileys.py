#!/usr/bin/env python3
"""Tests for the free Baileys transport.

The real thing needs a paired WhatsApp number, which no test can have, so the
Node helper is replaced by a stub that speaks the same one-JSON-object-per-line
protocol. What is actually being checked is the part that can silently lose
posts: that one process is shared by the whole run, that a helper which dies is
reported instead of hanging, and that the process is always closed — closing is
what writes the rotated WhatsApp credentials back, and skipping it would force a
re-pair.
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).parent))
os.environ.update(SENDER="baileys", WA_SESSION_KEY="x" * 32,
                  DRY_RUN="", ACTIVE_FROM="0", ACTIVE_TO="24")

TMP = pathlib.Path(tempfile.mkdtemp(prefix="baileys-test-"))
(TMP / "state").mkdir(parents=True, exist_ok=True)
(TMP / "state" / "wa-session.enc").write_bytes(b"pretend-session")

import slideegg_daily as S  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}"
          f"{'  -> ' + str(extra) if not cond and extra else ''}")
    if not cond:
        fails.append(name)


ITEM = {"url": "https://www.slideegg.com/x-template", "title": "X Template",
        "image": None, "kind": "template"}

STUB = r'''
const fs = require('fs');
const readline = require('readline');
const MODE = process.env.STUB_MODE || 'ok';
const REC = process.env.STUB_RECORD;
const say = (o) => process.stdout.write(JSON.stringify(o) + '\n');
const seen = [];

if (MODE === 'no-session') { say({ready:false, error:'no saved WhatsApp session'}); process.exit(2); }
if (MODE === 'no-channel') { say({ready:false, error:'the SlideEgg channel could not be found'}); process.exit(3); }
if (MODE === 'silent-death') { process.exit(9); }

say({ready:true, channel:'120363111111111111@newsletter'});

const rl = readline.createInterface({input: process.stdin, terminal:false});
rl.on('line', (line) => {
  const job = JSON.parse(line);
  seen.push(job);
  if (MODE === 'die-after-one' && seen.length > 1) { process.exit(7); }
  if (MODE === 'reject') { say({ok:false, error:'rate limited'}); return; }
  say({ok:true, kind: job.media ? 'image' : 'text'});
});
rl.on('close', () => {
  if (REC) fs.writeFileSync(REC, JSON.stringify({jobs: seen, closed: true}));
  say({bye:true, sent:seen.length, failed:0});
  process.exit(0);
});
'''


def fresh(mode="ok", record=None, stub=True, root_name=None):
    """Point the sender at a stub helper and hand back a clean instance."""
    root = TMP / (root_name or mode.replace("-", "_"))
    (root / "baileys" / "node_modules").mkdir(parents=True, exist_ok=True)
    if stub:
        (root / "baileys" / "send.js").write_text(STUB)
    S.ROOT = root
    os.environ["STUB_MODE"] = mode
    os.environ.pop("STUB_RECORD", None)
    if record:
        os.environ["STUB_RECORD"] = str(record)
    S.BAILEYS = S.BaileysSender()
    return S.BAILEYS


S.fetch_media = lambda u: None
S.build_caption = lambda it: f"CAPTION for {it['title']}"

print("\nHAPPY PATH")
rec = TMP / "rec.json"
b = fresh("ok", record=rec)
check("first post succeeds", S.send_post(ITEM) is True)
check("second post succeeds", S.send_post(ITEM) is True)
check("channel id was reported back", b.channel == "120363111111111111@newsletter")
first_pid = b.proc.pid
S.send_post(ITEM)
check("one helper serves the whole run, not one per post", b.proc.pid == first_pid)
b.close()
saved = json.loads(rec.read_text())
check("helper was closed cleanly so the session gets saved", saved["closed"] is True)
check("every post reached the helper", len(saved["jobs"]) == 3, saved)
check("the caption is what gets sent",
      saved["jobs"][0]["caption"] == "CAPTION for X Template", saved["jobs"][0])

print("\nIMAGES")
rec2 = TMP / "rec2.json"
b = fresh("ok", record=rec2)
S.fetch_media = lambda u: "data:image/png;base64,AAAA"
check("post with an image succeeds", S.send_post(ITEM) is True)
b.close()
check("the image is passed through to the helper",
      json.loads(rec2.read_text())["jobs"][0]["media"] == "data:image/png;base64,AAAA")
S.fetch_media = lambda u: None

print("\nWHEN THE HELPER CANNOT START")
b = fresh("no-session")
check("no paired session -> post fails, no exception", S.send_post(ITEM) is False)
check("the reason is recorded for the run log",
      "no saved WhatsApp session" in (b.broken or ""), b.broken)
check("a failed start is not retried on the next post", S.send_post(ITEM) is False)
check("no zombie process left behind", b.proc is None)
b.close()

b = fresh("no-channel")
check("channel not found -> post fails", S.send_post(ITEM) is False)
check("the admin-rights hint is preserved",
      "channel could not be found" in (b.broken or ""), b.broken)

b = fresh("silent-death")
check("helper that exits without speaking -> failure, not a hang",
      S.send_post(ITEM) is False)
check("silent death is explained", bool(b.broken), b.broken)

print("\nWHEN THE HELPER DIES MID-RUN")
b = fresh("die-after-one")
check("the post before the crash still counts", S.send_post(ITEM) is True)
check("the post after the crash fails", S.send_post(ITEM) is False)
check("the crash is recorded", bool(b.broken), b.broken)
b.close()

print("\nWHEN WHATSAPP REJECTS A POST")
b = fresh("reject")
check("a rejected send reports failure", S.send_post(ITEM) is False)
check("the connection stays up for the next item", b.broken is None)
check("a later post can still succeed on the same helper",
      S.send_post(ITEM) is False and b.proc is not None)
b.close()

print("\nMISSING INSTALL")
b = fresh("ok", stub=False, root_name="missing_script")
check("send.js missing -> clear failure", S.send_post(ITEM) is False)
check("the message names the missing file", "send.js is missing" in (b.broken or ""), b.broken)

root = TMP / "no_modules"
(root / "baileys").mkdir(parents=True, exist_ok=True)
(root / "baileys" / "send.js").write_text(STUB)
S.ROOT = root
S.BAILEYS = b = S.BaileysSender()
check("dependencies missing -> clear failure", S.send_post(ITEM) is False)
check("the message says to install", "npm ci" in (b.broken or ""), b.broken)

print("\nCLOSING IS ALWAYS SAFE")
b = S.BaileysSender()
b.close()
check("closing a helper that never started is a no-op", True)
b = fresh("ok")
b.close()
b.close()
check("closing twice does not raise", True)

print("\nCONFIG GUARDS")
check("baileys with no key would run dry",
      S._credential_problem.__doc__ is not None)
shutil.rmtree(TMP, ignore_errors=True)
print("\nALL PASS" if not fails else f"\nFAILURES: {fails}")
sys.exit(1 if fails else 0)
