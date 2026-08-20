#!/usr/bin/env python3
"""Email reports for the SlideEgg WhatsApp auto-poster.

  python report.py --daily     one summary for today (run at end of day)
  python report.py --weekly    last 7 days, sent with a link to the sheet
  python report.py --daily --dry-run     print the email instead of sending

Reads state/posts.csv (the permanent record) plus state/last_run.json and
state/health.json (to report errors and staleness).

Env vars:
  MAIL_FROM          sending Gmail address, e.g. mukunthini@slideegg.com
  MAIL_APP_PASSWORD  16-character Google App Password (NOT the login password)
  MAIL_TO            recipient(s), comma separated. Default admin@slideegg.com
  SHEET_URL          optional link shown in the email (Google Sheet, once set up)
  REPO_URL           repository link, used for the CSV link and log links
"""

import csv
import json
import os
import pathlib
import smtplib
import sys
import datetime as dt
from email.message import EmailMessage

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
ROOT = pathlib.Path(__file__).parent
STATE = ROOT / "state"
POSTS_CSV = STATE / "posts.csv"
LAST_RUN = STATE / "last_run.json"
HEALTH = STATE / "health.json"

MAIL_FROM = os.environ.get("MAIL_FROM", "").strip()
MAIL_PASS = os.environ.get("MAIL_APP_PASSWORD", "").strip()
MAIL_TO = [a.strip() for a in
           os.environ.get("MAIL_TO", "admin@slideegg.com").split(",") if a.strip()]
SHEET_URL = os.environ.get("SHEET_URL", "").strip()
REPO_URL = os.environ.get(
    "REPO_URL", "https://github.com/mukunthini-svg/slideegg-whatsapp-bot").rstrip("/")
CSV_URL = f"{REPO_URL}/blob/main/state/posts.csv"
ACTIONS_URL = f"{REPO_URL}/actions"

BRAND = "#1F5C8B"


def log(m):
    print(f"[report] {m}", flush=True)


# ---------------------------------------------------------------- data

def load_rows():
    if not POSTS_CSV.exists():
        return []
    try:
        with POSTS_CSV.open(encoding="utf-8") as fh:
            return [r for r in csv.DictReader(fh) if r.get("date")]
    except (OSError, csv.Error) as e:
        log(f"! could not read posts.csv: {e}")
        return []


def load_json(path):
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (ValueError, OSError):
            pass
    return {}


def in_range(rows, start, end):
    """Rows with start <= date <= end (all dates)."""
    out = []
    for r in rows:
        try:
            d = dt.date.fromisoformat(r["date"])
        except (ValueError, KeyError):
            continue
        if start <= d <= end:
            out.append(r)
    return out


def health_note(now):
    """Returns (is_problem, headline, detail)."""
    run = load_json(LAST_RUN)
    h = load_json(HEALTH)

    if not run:
        return True, "No run record found",\
            "state/last_run.json is missing. The workflow may never have run."

    if run.get("failed"):
        return True, f"{run['failed']} post(s) failed to send",\
            f"Failed URLs: {', '.join(run.get('failed_urls', [])) or 'see the run log'}"

    if run.get("mode") == "dry":
        return True, "The bot is in preview mode — nothing is being sent",\
            f"Reason given: {run.get('why_dry') or 'unknown'}. " \
            "Check the 'mode' input on the workflow and the WHAPI_TOKEN secret."

    # staleness
    ts = h.get("last_new_item_at")
    if ts:
        try:
            then = dt.datetime.fromisoformat(ts)
            if then.tzinfo is None:
                then = then.replace(tzinfo=IST)
            quiet = (now - then).total_seconds() / 3600
            if quiet > 24:
                return True, f"Nothing new detected for {quiet:.0f} hours",\
                    "Compare diagnostics.page1_top3 in the latest run against the " \
                    "live site — the runner may be receiving a stale cached page."
        except ValueError:
            pass

    # how fresh is the last run itself
    try:
        last = dt.datetime.fromisoformat(run["run"])
        if last.tzinfo is None:
            last = last.replace(tzinfo=IST)
        gap = (now - last).total_seconds() / 3600
        if gap > 6:
            return True, f"The bot has not run for {gap:.0f} hours",\
                "GitHub may have disabled the schedule. Open the Actions tab and check."
    except (KeyError, ValueError):
        pass

    return False, "All healthy", ""


# ---------------------------------------------------------------- rendering

def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def table(rows):
    if not rows:
        return "<p style='color:#777'>No posts in this period.</p>"
    head = ("<tr>" + "".join(
        f"<th align='left' style='background:{BRAND};color:#fff;padding:7px 10px;"
        f"font:600 13px system-ui'>{h}</th>"
        for h in ("Date", "Time", "Type", "Title")) + "</tr>")
    body = ""
    for i, r in enumerate(rows):
        bg = "#F7F9FB" if i % 2 else "#fff"
        link = f"<a href='{esc(r.get('url',''))}' style='color:{BRAND};text-decoration:none'>{esc(r.get('title',''))}</a>"
        body += (f"<tr style='background:{bg}'>"
                 f"<td style='padding:6px 10px;font:13px system-ui;white-space:nowrap'>{esc(r.get('date',''))}</td>"
                 f"<td style='padding:6px 10px;font:13px system-ui;white-space:nowrap'>{esc(r.get('time_ist',''))}</td>"
                 f"<td style='padding:6px 10px;font:13px system-ui;white-space:nowrap'>{esc(r.get('type',''))}</td>"
                 f"<td style='padding:6px 10px;font:13px system-ui'>{link}</td></tr>")
    return ("<table cellspacing='0' cellpadding='0' "
            "style='border-collapse:collapse;width:100%;border:1px solid #DCE5EC'>"
            + head + body + "</table>")


def stat(label, value, color="#222"):
    return (f"<td align='center' style='padding:14px 10px;border:1px solid #DCE5EC;background:#fff'>"
            f"<div style='font:700 26px system-ui;color:{color}'>{value}</div>"
            f"<div style='font:12px system-ui;color:#777;margin-top:3px'>{label}</div></td>")


def shell(title, subtitle, inner):
    links = (f"<a href='{CSV_URL}' style='color:{BRAND}'>Open the sheet (CSV)</a>"
             f" &nbsp;·&nbsp; <a href='{ACTIONS_URL}' style='color:{BRAND}'>Run logs</a>")
    if SHEET_URL:
        links = (f"<a href='{SHEET_URL}' style='color:{BRAND}'>Open the Google Sheet</a>"
                 f" &nbsp;·&nbsp; " + links)
    return f"""<html><body style="margin:0;background:#F4F6F8;padding:22px">
<div style="max-width:660px;margin:auto;background:#fff;border:1px solid #DCE5EC">
  <div style="background:{BRAND};padding:18px 22px">
    <div style="font:700 19px system-ui;color:#fff">SlideEgg WhatsApp Channel</div>
    <div style="font:13px system-ui;color:#CFE0EC;margin-top:2px">{esc(title)}</div>
  </div>
  <div style="padding:22px">
    <p style="font:14px system-ui;color:#555;margin:0 0 16px">{subtitle}</p>
    {inner}
    <p style="font:13px system-ui;margin:22px 0 0">{links}</p>
    <p style="font:11px system-ui;color:#999;margin:14px 0 0">
      Sent automatically by the SlideEgg WhatsApp auto-poster.</p>
  </div>
</div></body></html>"""


def build_daily(now):
    rows = load_rows()
    today = now.date()
    todays = in_range(rows, today, today)
    tmpl = sum(1 for r in todays if r.get("type") == "template")
    blog = sum(1 for r in todays if r.get("type") == "blog")
    problem, headline, detail = health_note(now)

    banner = ""
    if problem:
        banner = (f"<div style='border-left:5px solid #C0392B;background:#FDF1EF;"
                  f"padding:12px 14px;margin:0 0 16px'>"
                  f"<div style='font:700 14px system-ui;color:#C0392B'>Problem: {esc(headline)}</div>"
                  f"<div style='font:13px system-ui;color:#444;margin-top:5px'>{esc(detail)}</div></div>")
    elif not todays:
        banner = (f"<div style='border-left:5px solid #B8860B;background:#FDF8EC;"
                  f"padding:12px 14px;margin:0 0 16px'>"
                  f"<div style='font:700 14px system-ui;color:#8A6D0B'>No posts today</div>"
                  f"<div style='font:13px system-ui;color:#444;margin-top:5px'>"
                  f"The bot ran normally and reported no errors — SlideEgg simply "
                  f"published nothing new today.</div></div>")

    stats = ("<table cellspacing='0' cellpadding='0' style='border-collapse:collapse;width:100%'><tr>"
             + stat("Posts today", len(todays), BRAND)
             + stat("Templates", tmpl)
             + stat("Blog posts", blog)
             + stat("Status", "OK" if not problem else "CHECK",
                    "#1E8449" if not problem else "#C0392B")
             + "</tr></table>")

    inner = banner + stats + "<div style='height:18px'></div>" + table(todays)
    subject = (f"[SlideEgg WhatsApp] {today:%d %b %Y} — {len(todays)} post"
               f"{'' if len(todays) == 1 else 's'}"
               + (" — NEEDS ATTENTION" if problem else ""))
    return subject, shell(f"Daily report · {today:%A, %d %B %Y}",
                          "Everything published to the channel today.", inner)


def build_weekly(now):
    rows = load_rows()
    end = now.date()
    start = end - dt.timedelta(days=6)
    week = in_range(rows, start, end)
    tmpl = sum(1 for r in week if r.get("type") == "template")
    blog = sum(1 for r in week if r.get("type") == "blog")
    problem, headline, detail = health_note(now)

    # per-day breakdown
    per_day = ""
    for i in range(7):
        d = start + dt.timedelta(days=i)
        n = len(in_range(week, d, d))
        bar = "▇" * min(n, 20)
        per_day += (f"<tr><td style='padding:4px 10px;font:13px system-ui;width:130px'>"
                    f"{d:%a %d %b}</td>"
                    f"<td style='padding:4px 10px;font:13px system-ui;color:{BRAND}'>"
                    f"{bar} <span style='color:#666'>{n}</span></td></tr>")

    banner = ""
    if problem:
        banner = (f"<div style='border-left:5px solid #C0392B;background:#FDF1EF;"
                  f"padding:12px 14px;margin:0 0 16px'>"
                  f"<div style='font:700 14px system-ui;color:#C0392B'>Problem: {esc(headline)}</div>"
                  f"<div style='font:13px system-ui;color:#444;margin-top:5px'>{esc(detail)}</div></div>")

    stats = ("<table cellspacing='0' cellpadding='0' style='border-collapse:collapse;width:100%'><tr>"
             + stat("Posts this week", len(week), BRAND)
             + stat("Templates", tmpl)
             + stat("Blog posts", blog)
             + stat("Avg / day", f"{len(week)/7:.1f}")
             + "</tr></table>")

    inner = (banner + stats
             + "<h3 style='font:600 15px system-ui;color:#222;margin:22px 0 8px'>Day by day</h3>"
             + "<table cellspacing='0' cellpadding='0' style='border-collapse:collapse;width:100%'>"
             + per_day + "</table>"
             + "<h3 style='font:600 15px system-ui;color:#222;margin:22px 0 8px'>Everything posted</h3>"
             + table(week))
    subject = (f"[SlideEgg WhatsApp] Weekly report {start:%d %b} – {end:%d %b %Y} "
               f"— {len(week)} posts")
    return subject, shell(f"Weekly report · {start:%d %b} – {end:%d %b %Y}",
                          f"{len(week)} posts published to the channel over the last 7 days.",
                          inner)


# ---------------------------------------------------------------- sending

def send(subject, html):
    if not MAIL_FROM or not MAIL_PASS:
        log("! MAIL_FROM / MAIL_APP_PASSWORD not set — cannot send")
        return 1
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = MAIL_FROM
    msg["To"] = ", ".join(MAIL_TO)
    msg.set_content("This report is formatted in HTML. "
                    f"Open the sheet: {CSV_URL}")
    msg.add_alternative(html, subtype="html")
    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=60) as s:
            s.starttls()
            s.login(MAIL_FROM, MAIL_PASS)
            s.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        log("! Gmail rejected the login. The password must be a 16-character "
            "App Password (Google Account -> Security -> App passwords), not "
            "the normal account password, and 2-Step Verification must be on.")
        return 1
    except Exception as e:                      # noqa: BLE001 - report anything
        log(f"! send failed: {type(e).__name__}: {e}")
        return 1
    log(f"sent to {', '.join(MAIL_TO)}: {subject}")
    return 0


def main():
    now = dt.datetime.now(IST)
    if "--weekly" in sys.argv:
        subject, html = build_weekly(now)
    else:
        subject, html = build_daily(now)

    if "--dry-run" in sys.argv:
        print("SUBJECT:", subject)
        out = ROOT / "report_preview.html"
        out.write_text(html, encoding="utf-8")
        print("preview written to", out)
        return 0
    return send(subject, html)


if __name__ == "__main__":
    sys.exit(main())
