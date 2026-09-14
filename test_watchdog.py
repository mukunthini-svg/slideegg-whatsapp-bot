#!/usr/bin/env python3
"""Watchdog tests. The watchdog is the alarm for silent failures — the two
outages this system has had both reported success, so this logic has to be
right or the alarm is worse than useless.

It no longer signals by failing the run. A red run emails the repository
owner personally every hour, and that was asked to stop, so the watchdog now
always exits 0 and writes its verdict into last_run.json['problem'], which
report.py --alert turns into a single email. These tests therefore assert on
the recorded verdict, not on the exit code — and they assert the exit code is
always 0, because a stray non-zero is exactly the regression that would start
the flood again.
"""
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


def run(found_new=False, failed=0):
    """Run the watchdog and return (exit code, recorded problem)."""
    S.LOG_FILE.unlink(missing_ok=True)
    rc = S.watchdog(NOW, found_new=found_new, failed=failed)
    try:
        problem = json.loads(S.LOG_FILE.read_text()).get("problem")
    except (OSError, ValueError):
        problem = "<nothing recorded>"
    return rc, problem


print("\nWATCHDOG")
setH(1)
rc, problem = run()
check("quiet 1h -> nothing wrong", problem is None, problem)

setH(23.5)
rc, problem = run()
check("quiet 23.5h -> still nothing wrong", problem is None, problem)

setH(30)
rc, problem = run()
check("quiet 30h -> a problem is recorded", problem and "nothing new" in problem,
      problem)
check("...but the run still exits 0 so no mail is sent by GitHub", rc == 0, rc)

setH(30)
rc, problem = run(found_new=True)
check("a new item resets the clock", problem is None, problem)
h = json.loads(S.HEALTH_FILE.read_text())
check("clock actually written", h["last_new_item_at"][:16] == NOW.isoformat()[:16], h)

setH(1)
rc, problem = run(found_new=True, failed=2)
check("a failed post is recorded immediately",
      problem and "2 post(s) failed" in problem, problem)
check("...and that run exits 0 too", rc == 0, rc)

S.HEALTH_FILE.unlink(missing_ok=True)
rc, problem = run()
check("no history -> no false alarm on a first ever run", problem is None, problem)

S.HEALTH_FILE.write_text("{{ corrupt")
rc, problem = run()
check("corrupt health file -> no false alarm", problem is None, problem)

S.HEALTH_FILE.write_text(json.dumps(
    {"last_new_item_at": (NOW - dt.timedelta(hours=40)).replace(tzinfo=None).isoformat()}))
rc, problem = run()
check("timestamp without a timezone still alarms",
      problem and "nothing new" in problem, problem)

# A healthy run must actively clear the field. Leaving a stale 'problem'
# behind would keep the alert mail alive long after the fault was fixed.
setH(1)
S.LOG_FILE.write_text(json.dumps({"problem": "an old fault, since fixed"}))
S.watchdog(NOW, found_new=True, failed=0)
check("a healthy run clears the previous problem",
      json.loads(S.LOG_FILE.read_text()).get("problem") is None,
      json.loads(S.LOG_FILE.read_text()).get("problem"))

# The verdict is written alongside whatever main() already recorded, not
# instead of it — the alert email and the run summary both read this file.
setH(1)
S.LOG_FILE.write_text(json.dumps({"posted": 3, "mode": "live"}))
S.watchdog(NOW, found_new=True, failed=1)
data = json.loads(S.LOG_FILE.read_text())
check("recording a problem preserves the rest of last_run.json",
      data.get("posted") == 3 and data.get("mode") == "live", data)

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
