#!/usr/bin/env python3
"""Tests for the email reports. The reports are how anyone finds out something
is wrong, so a report that quietly says "all fine" during an outage would be
worse than no report at all — most of these checks are about that."""
import csv
import json
import os
import pathlib
import sys
import datetime as dt

sys.path.insert(0, str(pathlib.Path(__file__).parent))
os.environ.setdefault("MAIL_FROM", "")
os.environ.setdefault("MAIL_APP_PASSWORD", "")
import report as R

fails = []


def check(n, c, x=""):
    print(f"  {'PASS' if c else 'FAIL'}  {n}{'  -> ' + str(x) if not c and x else ''}")
    if not c:
        fails.append(n)


NOW = dt.datetime.now(R.IST)
TODAY = NOW.date()


def write_posts(rows):
    R.STATE.mkdir(parents=True, exist_ok=True)
    with R.POSTS_CSV.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["date", "time_ist", "year", "month", "month_name", "week",
                    "day_name", "type", "title", "url", "image_url"])
        for d, t, kind, title in rows:
            w.writerow([d, t, d[:4], int(d[5:7]), "X", "W", "Day", kind, title,
                        f"https://www.slideegg.com/{title.lower().replace(' ', '-')}", ""])


def write_state(run=None, health=None):
    if run is None:
        run = {"run": NOW.isoformat(), "mode": "live", "failed": 0, "posted": 2}
    R.LAST_RUN.write_text(json.dumps(run))
    if health is None:
        health = {"last_new_item_at": NOW.isoformat()}
    R.HEALTH.write_text(json.dumps(health))


print("\nDAILY REPORT")
write_posts([
    (str(TODAY), "09:15", "template", "Student Self Care"),
    (str(TODAY), "10:02", "template", "Palliative Care"),
    (str(TODAY), "11:40", "blog", "Gemini Spark Guide"),
    (str(TODAY - dt.timedelta(days=1)), "14:00", "template", "Yesterday Item"),
])
write_state()
subj, html = R.build_daily(NOW)
check("subject shows today's count", "3 posts" in subj, subj)
check("subject has no alarm when healthy", "NEEDS ATTENTION" not in subj, subj)
check("counts templates and blog separately", ">2<" in html and ">1<" in html)
check("today's items listed", "Student Self Care" in html)
check("yesterday's item excluded", "Yesterday Item" not in html)
check("status tile reads OK", ">OK<" in html)

print("\nDAILY REPORT — quiet day vs broken day")
write_posts([])
write_state()
subj, html = R.build_daily(NOW)
check("zero posts is reported as normal, not as an error",
      "No posts today" in html and "NEEDS ATTENTION" not in subj)
check("explains why zero is fine", "published nothing new" in html)

write_state(run={"run": NOW.isoformat(), "mode": "live", "failed": 2,
                 "posted": 0, "failed_urls": ["https://x/a"]})
subj, html = R.build_daily(NOW)
check("failed sends raise the alarm", "NEEDS ATTENTION" in subj, subj)
check("failure detail is shown", "failed to send" in html)

write_state(run={"run": NOW.isoformat(), "mode": "dry",
                 "why_dry": "WHAPI_TOKEN missing/empty", "failed": 0})
subj, html = R.build_daily(NOW)
check("silent preview mode raises the alarm", "NEEDS ATTENTION" in subj)
check("preview reason is named", "WHAPI_TOKEN missing/empty" in html)

write_state(health={"last_new_item_at": (NOW - dt.timedelta(hours=40)).isoformat()})
subj, html = R.build_daily(NOW)
check("40h of silence raises the alarm (the two-day outage)", "NEEDS ATTENTION" in subj)
check("points at the stale-page check", "page1_top3" in html)

write_state(run={"run": (NOW - dt.timedelta(hours=9)).isoformat(),
                 "mode": "live", "failed": 0})
subj, html = R.build_daily(NOW)
check("bot not running for 9h raises the alarm", "NEEDS ATTENTION" in subj)

R.LAST_RUN.unlink(missing_ok=True)
subj, html = R.build_daily(NOW)
check("missing run record raises the alarm", "NEEDS ATTENTION" in subj)

print("\nWEEKLY REPORT")
rows = []
for i in range(10):
    d = TODAY - dt.timedelta(days=i)
    rows.append((str(d), "10:00", "template", f"Item {i}"))
write_posts(rows)
write_state()
subj, html = R.build_weekly(NOW)
check("counts only the last 7 days", "7 posts" in subj, subj)
check("excludes day 8 and older", "Item 8" not in html and "Item 9" not in html)
check("includes day 7 boundary", "Item 6" in html)
check("shows a day-by-day breakdown", "▇" in html)
check("shows an average per day", "1.0" in html)

print("\nROBUSTNESS")
R.POSTS_CSV.unlink(missing_ok=True)
subj, html = R.build_daily(NOW)
check("missing csv does not crash", "0 posts" in subj, subj)

R.POSTS_CSV.write_text("date,time_ist\nnot-a-date,10:00\n,\n")
check("malformed csv rows are skipped, no crash", R.load_rows() is not None)
subj, html = R.build_daily(NOW)
check("malformed csv still produces a report", "SlideEgg" in html)

write_posts([(str(TODAY), "09:00", "template", "Tom & Jerry <script>")])
write_state()
_, html = R.build_daily(NOW)
check("html in a title is escaped", "&lt;script&gt;" in html and "<script>" not in html)

check("no credentials -> send fails loudly instead of pretending",
      R.send("x", "<p>y</p>") == 1)

for f in (R.POSTS_CSV, R.LAST_RUN, R.HEALTH):
    f.unlink(missing_ok=True)
print("\nALL PASS" if not fails else f"\nFAILURES: {fails}")
sys.exit(1 if fails else 0)
