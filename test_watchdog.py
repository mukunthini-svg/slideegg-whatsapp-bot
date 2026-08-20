#!/usr/bin/env python3
"""Watchdog tests. The watchdog is the alarm for silent failures — the two
outages this system has had both reported success, so this logic has to be
right or the alarm is worse than useless."""
import sys, json, os, pathlib, datetime as dt

sys.path.insert(0, str(pathlib.Path(__file__).parent))
os.environ.update(DRY_RUN="", WHAPI_TOKEN="tok", SCAN_PAGES="1", SOURCES="templates",
                  ACTIVE_FROM="0", ACTIVE_TO="24", DAILY_LIMIT="8", MAX_POSTS="8",
                  ALERT_AFTER_HOURS="24")
import slideegg_daily as S

fails = []


def check(n, c, x=""):
    print(f"  {'PASS' if c else 'FAIL'}  {n}{'  -> ' + str(x) if not c and x else ''}")
    if not c:
        fails.append(n)


NOW = dt.datetime.now(S.IST)


def setH(hours_ago):
    S.STATE_DIR.mkdir(parents=True, exist_ok=True)
    S.HEALTH_FILE.write_text(json.dumps(
        {"last_new_item_at": (NOW - dt.timedelta(hours=hours_ago)).isoformat()}))


print("\nWATCHDOG")
setH(1)
check("quiet 1h -> ok", S.watchdog(NOW, found_new=False, failed=0) == 0)

setH(23.5)
check("quiet 23.5h -> still ok", S.watchdog(NOW, found_new=False, failed=0) == 0)

setH(30)
check("quiet 30h -> ALARM (exit 1)", S.watchdog(NOW, found_new=False, failed=0) == 1)

setH(30)
check("a new item resets the clock", S.watchdog(NOW, found_new=True, failed=0) == 0)
h = json.loads(S.HEALTH_FILE.read_text())
check("clock actually written", h["last_new_item_at"][:16] == NOW.isoformat()[:16], h)

setH(1)
check("a failed post alarms immediately", S.watchdog(NOW, found_new=True, failed=2) == 1)

S.HEALTH_FILE.unlink(missing_ok=True)
check("no history -> no false alarm on a first ever run",
      S.watchdog(NOW, found_new=False, failed=0) == 0)

S.HEALTH_FILE.write_text("{{ corrupt")
check("corrupt health file -> no false alarm",
      S.watchdog(NOW, found_new=False, failed=0) == 0)

S.HEALTH_FILE.write_text(json.dumps(
    {"last_new_item_at": (NOW - dt.timedelta(hours=40)).replace(tzinfo=None).isoformat()}))
check("timestamp without a timezone still alarms",
      S.watchdog(NOW, found_new=False, failed=0) == 1)

setH(30)
was = S.DRY_RUN
S.DRY_RUN = True
S.watchdog(NOW, found_new=True, failed=0)
h = json.loads(S.HEALTH_FILE.read_text())
check("a dry run never advances the clock",
      (NOW - dt.datetime.fromisoformat(h["last_new_item_at"])).total_seconds() / 3600 > 29)
S.DRY_RUN = was

S.HEALTH_FILE.unlink(missing_ok=True)
print("\nALL PASS" if not fails else f"\nFAILURES: {fails}")
sys.exit(1 if fails else 0)
