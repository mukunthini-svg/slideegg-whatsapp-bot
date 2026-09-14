#!/usr/bin/env python3
"""Checks for the mail policy: one weekly list, and silence unless broken.

Nothing here touches the network. report.send is replaced with a recorder, so
every assertion is about whether a mail WOULD have gone out and what was in it.

    python test_alerts.py
"""
import datetime as dt
import importlib
import json
import pathlib
import shutil
import sys
import tempfile

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
NOW = dt.datetime(2026, 9, 7, 9, 0, tzinfo=IST)

ok = fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  ok    {name}")
    else:
        fail += 1
        print(f"  FAIL  {name} {extra}")


class Mailbox:
    """Stands in for report.send and remembers what was handed to it."""

    def __init__(self, report):
        self.sent = []
        report.send = self._send

    def _send(self, subject, html):
        self.sent.append((subject, html))
        return 0

    @property
    def subjects(self):
        return [s for s, _ in self.sent]

    def clear(self):
        self.sent.clear()


def fresh_state(tmp, *, last_run=None, health=None, posts=None, alert=None):
    """Point report.py at a throwaway state/ directory."""
    state = pathlib.Path(tmp) / "state"
    state.mkdir(parents=True, exist_ok=True)
    for f in state.iterdir():
        f.unlink()

    if last_run is not None:
        (state / "last_run.json").write_text(json.dumps(last_run))
    if health is not None:
        (state / "health.json").write_text(json.dumps(health))
    if alert is not None:
        (state / "alert.json").write_text(json.dumps(alert))
    if posts is not None:
        rows = ["date,time_ist,type,title,url"] + posts
        (state / "posts.csv").write_text("\n".join(rows) + "\n")

    report.STATE = state
    report.LAST_RUN = state / "last_run.json"
    report.HEALTH = state / "health.json"
    report.ALERT_STATE = state / "alert.json"
    report.POSTS_CSV = state / "posts.csv"
    return state


HEALTHY_RUN = {"run": NOW.isoformat(), "mode": "live", "sender": "baileys",
               "sender_error": None, "posted": 1, "failed": 0, "problem": None}
FRESH_HEALTH = {"last_new_item_at": NOW.isoformat()}


sys.path.insert(0, str(pathlib.Path(__file__).parent))
report = importlib.import_module("report")
tmp = tempfile.mkdtemp(prefix="slideegg-alert-")
box = Mailbox(report)

try:
    print("\n-- silence when everything is fine --")
    fresh_state(tmp, last_run=HEALTHY_RUN, health=FRESH_HEALTH)
    box.clear()
    rc = report.run_alert(NOW)
    check("a healthy run sends nothing", box.sent == [], box.subjects)
    check("and still exits 0", rc == 0, rc)
    check("no alert.json is created for a healthy run",
          not (pathlib.Path(tmp) / "state" / "alert.json").exists())

    print("\n-- a real problem gets exactly one mail --")
    broken = dict(HEALTHY_RUN, problem="2 post(s) failed to send this run")
    state = fresh_state(tmp, last_run=broken, health=FRESH_HEALTH)
    box.clear()
    report.run_alert(NOW)
    check("a broken run mails once", len(box.sent) == 1, box.subjects)
    check("the subject says attention is needed",
          box.sent and "needs attention" in box.subjects[0].lower(), box.subjects)
    check("the mail names the problem",
          box.sent and "failed to send" in box.sent[0][1])
    check("the mail explains the linked-device fix",
          box.sent and "Linked devices" in box.sent[0][1])
    saved = json.loads((state / "alert.json").read_text())
    check("the open problem is remembered", bool(saved.get("open")), saved)

    print("\n-- the hourly schedule cannot turn that into a flood --")
    box.clear()
    for hour in range(1, 24):          # 23 more runs, same problem, same day
        report.run_alert(NOW + dt.timedelta(hours=hour))
    check("23 further hourly runs send nothing", box.sent == [],
          f"{len(box.sent)} mails: {box.subjects}")

    print("\n-- but a problem still broken a day later is raised again --")
    box.clear()
    report.run_alert(NOW + dt.timedelta(hours=25))
    check("it mails again after the cooldown", len(box.sent) == 1, box.subjects)

    print("\n-- a DIFFERENT problem is never suppressed --")
    fresh_state(tmp,
                last_run=dict(HEALTHY_RUN, problem="the run crashed: OSError: disk full"),
                health=FRESH_HEALTH,
                alert={"open": "2 post(s) failed to send this run",
                       "sent_at": NOW.isoformat()})
    box.clear()
    report.run_alert(NOW + dt.timedelta(hours=1))
    check("a new problem mails immediately", len(box.sent) == 1, box.subjects)
    check("and names the new problem, not the old one",
          box.sent and "crashed" in box.sent[0][1].lower())

    print("\n-- recovery is announced once, then silence --")
    state = fresh_state(tmp, last_run=HEALTHY_RUN, health=FRESH_HEALTH,
                        alert={"open": "2 post(s) failed to send this run",
                               "sent_at": NOW.isoformat()})
    box.clear()
    report.run_alert(NOW + dt.timedelta(hours=2))
    check("recovery sends one all-clear", len(box.sent) == 1, box.subjects)
    check("worded as working again",
          box.sent and "working again" in box.subjects[0].lower(), box.subjects)
    box.clear()
    report.run_alert(NOW + dt.timedelta(hours=3))
    check("and never repeats the all-clear", box.sent == [], box.subjects)

    print("\n-- the specific failures we have actually hit --")
    for label, run, expect in [
        ("a logged-out WhatsApp session",
         dict(HEALTHY_RUN, sender_error="connection closed: logged out"),
         "logged out"),
        ("silently dropping into preview mode",
         dict(HEALTHY_RUN, mode="dry", why_dry="WA_SESSION_KEY missing/empty"),
         "preview mode"),
        ("the workflow not running at all",
         dict(HEALTHY_RUN, run=(NOW - dt.timedelta(hours=30)).isoformat()),
         "has not run"),
    ]:
        fresh_state(tmp, last_run=run, health=FRESH_HEALTH)
        box.clear()
        report.run_alert(NOW)
        got = (box.sent[0][0] + box.sent[0][1]).lower() if box.sent else ""
        check(f"{label} is caught", expect in got, got[:120])

    print("\n-- a stale site is caught even though every run 'succeeded' --")
    fresh_state(tmp, last_run=HEALTHY_RUN,
                health={"last_new_item_at": (NOW - dt.timedelta(hours=40)).isoformat()})
    box.clear()
    report.run_alert(NOW)
    check("a 40-hour silence raises the alarm", len(box.sent) == 1, box.subjects)

    print("\n-- the weekly email is the list of posts and nothing else --")
    posts = [
        "2026-09-02,10:14,template,Sales Funnel PPT,https://slideegg.com/a",
        "2026-09-03,11:02,blog,How to design a deck,https://slideegg.com/b",
        "2026-09-05,09:31,template,Roadmap Slide,https://slideegg.com/c",
    ]
    fresh_state(tmp, last_run=HEALTHY_RUN, health=FRESH_HEALTH, posts=posts)
    subject, html = report.build_weekly(NOW)
    check("every post is listed",
          all(t in html for t in ("Sales Funnel PPT", "How to design a deck",
                                  "Roadmap Slide")))
    check("each one links to its page", html.count("https://slideegg.com/") >= 3)
    check("the subject carries the count", "3 posts" in subject, subject)
    check("no day-by-day bar chart", "▇" not in html)
    check("no stats grid", "Avg / day" not in html and "Posts this week" not in html)
    check("no health banner riding along", "Problem:" not in html)

    print("\n-- an empty week still reports honestly --")
    fresh_state(tmp, last_run=HEALTHY_RUN, health=FRESH_HEALTH, posts=[])
    subject, html = report.build_weekly(NOW)
    check("it says zero rather than breaking", "0 posts" in subject, subject)
    check("and says so in the body", "No posts in this period" in html)

    print("\n-- posts outside the 7-day window are excluded --")
    fresh_state(tmp, last_run=HEALTHY_RUN, health=FRESH_HEALTH, posts=[
        "2026-09-05,09:31,template,Inside the week,https://slideegg.com/in",
        "2026-08-01,09:31,template,Five weeks ago,https://slideegg.com/old",
    ])
    subject, html = report.build_weekly(NOW)
    check("this week's post is in", "Inside the week" in html)
    check("last month's post is out", "Five weeks ago" not in html)

    print("\n-- dry-run never sends --")
    fresh_state(tmp, last_run=dict(HEALTHY_RUN, problem="everything is on fire"),
                health=FRESH_HEALTH)
    box.clear()
    report.run_alert(NOW, dry=True)
    check("a dry alert sends nothing", box.sent == [], box.subjects)
    check("and records nothing, so the real alert still fires later",
          not (pathlib.Path(tmp) / "state" / "alert.json").exists())

finally:
    shutil.rmtree(tmp, ignore_errors=True)
    for leftover in ("report_preview.html",):
        pathlib.Path(leftover).unlink(missing_ok=True)

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
