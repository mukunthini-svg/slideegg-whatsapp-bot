#!/usr/bin/env python3
"""Tests for recovering from a rejected channel id.

The cached channel id is never re-checked once written, so a channel that is
recreated — or an account that reconnects under a new session — would leave the
bot posting to a dead id and failing on every run, for ever. These checks cover
that path, and make sure a rejected id never turns into a duplicate post."""
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
os.environ.update(WHAPI_TOKEN="tok", DRY_RUN="", ACTIVE_FROM="0", ACTIVE_TO="24")
import slideegg_daily as S

fails = []


def check(n, c, x=""):
    print(f"  {'PASS' if c else 'FAIL'}  {n}{'  -> ' + str(x) if not c and x else ''}")
    if not c:
        fails.append(n)


class Resp:
    def __init__(self, status, text=""):
        self.status_code, self.text = status, text

    def json(self):
        return json.loads(self.text)


class Session:
    """Records every send, and answers with a scripted status per channel id."""

    def __init__(self, behaviour, newsletters=None):
        self.behaviour = behaviour        # {channel_id: status_code}
        self.newsletters = newsletters or []
        self.posts, self.gets = [], []

    def post(self, url, headers=None, json=None, timeout=None):
        self.posts.append({"url": url, "to": json.get("to")})
        code = self.behaviour.get(json.get("to"), 200)
        if code == 404:
            return Resp(404, '{"error":"Channel not found","requestId":"x"}')
        if code in (200, 201):
            return Resp(200, "{}")
        return Resp(code, '{"error":"nope"}')

    def get(self, url, **kw):
        self.gets.append(url)
        return Resp(200, json_module.dumps({"newsletters": self.newsletters}))


import json as json_module

ITEM = {"url": "https://www.slideegg.com/x", "title": "X Template",
        "image": None, "kind": "template"}
DEAD = "120363408261271079@newsletter"
LIVE = "120363499999999999@newsletter"


def reset(cached_id=DEAD):
    S.STATE_DIR.mkdir(parents=True, exist_ok=True)
    (S.STATE_DIR / "channel.json").write_text(json.dumps(
        {"invite": S.RAW_CHANNEL, "id": cached_id, "name": "SlideEgg Presentation Hub"}))
    S._CHANNEL_REFRESHED = False
    S.CHANNEL = cached_id
    S.fetch_media = lambda u: None


print("\nCHANNEL RECOVERY")

# the real outage: cached id is dead, and a NEW id is available
reset()
sess = Session({DEAD: 404, LIVE: 200},
               newsletters=[{"id": LIVE, "name": "SlideEgg Presentation Hub",
                             "invite": S.RAW_CHANNEL}])
S.SESSION = sess
ok = S.whapi_post(ITEM)
check("recovers when the channel id changed", ok is True)
check("retried against the NEW id", sess.posts[-1]["to"] == LIVE, sess.posts)
check("stopped hammering the dead id",
      sum(1 for p in sess.posts if p["to"] == DEAD) == 1, sess.posts)
check("re-resolved via the channel list", any("/newsletters" in g for g in sess.gets))
check("new id written back to the cache",
      json.loads((S.STATE_DIR / "channel.json").read_text())["id"] == LIVE)

# access genuinely lost: same id comes back, so nothing can be done
reset()
sess = Session({DEAD: 404},
               newsletters=[{"id": DEAD, "name": "SlideEgg Presentation Hub",
                             "invite": S.RAW_CHANNEL}])
S.SESSION = sess
check("same id back -> reports failure, does not loop", S.whapi_post(ITEM) is False)
check("did not retry pointlessly",
      sum(1 for p in sess.posts if p["to"] == DEAD) == 1, sess.posts)

# number dropped out of every channel (disconnect / plan lost Channels)
reset()
sess = Session({DEAD: 404}, newsletters=[])
S.SESSION = sess
check("no channels at all -> failure", S.whapi_post(ITEM) is False)

# refresh happens at most once per run, however many posts fail
reset()
sess = Session({DEAD: 404}, newsletters=[])
S.SESSION = sess
S.whapi_post(ITEM)
before = len(sess.gets)
S.whapi_post(ITEM)
check("channel lookup runs once per run, not per post",
      len(sess.gets) == before, f"{before} -> {len(sess.gets)}")

# a 401 must NOT trigger a channel refresh — that is a token problem
reset()
sess = Session({DEAD: 401}, newsletters=[{"id": LIVE, "name": "SlideEgg"}])
S.SESSION = sess
check("401 fails without touching the channel cache", S.whapi_post(ITEM) is False)
check("401 did not re-resolve the channel", not sess.gets, sess.gets)
check("401 left the cached id alone",
      json.loads((S.STATE_DIR / "channel.json").read_text())["id"] == DEAD)

# healthy path unchanged
reset(LIVE)
sess = Session({LIVE: 200})
S.SESSION = sess
check("normal send still works", S.whapi_post(ITEM) is True)
check("normal send makes exactly one request", len(sess.posts) == 1)

(S.STATE_DIR / "channel.json").unlink(missing_ok=True)
print("\nALL PASS" if not fails else f"\nFAILURES: {fails}")
sys.exit(1 if fails else 0)
