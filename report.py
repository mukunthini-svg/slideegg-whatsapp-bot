#!/usr/bin/env python3
"""Email for the SlideEgg WhatsApp auto-poster.

Two mails leave this file and no others, which is deliberate — the owner
asked for one weekly list and to hear nothing else unless posting breaks:

  python report.py --weekly    Monday morning: the list of posts from the
                               last 7 days. Sent even if the week was empty.
  python report.py --alert     Runs after every posting run and USUALLY SENDS
                               NOTHING. Mails only when posting is broken, at
                               most once a day for the same problem, plus one
                               all-clear when it recovers.
  python report.py --daily     the old end-of-day summary. No longer on any
                               schedule; kept for running by hand.

  ... --dry-run                write report_preview.html instead of sending.

Reads state/posts.csv (the permanent record) plus state/last_run.json and
state/health.json (to report errors and staleness), and keeps its own
state/alert.json so it knows what it has already complained about.

Sending: Brevo is used when BREVO_API_KEY is set, otherwise Gmail SMTP.
Gmail App Passwords are unavailable on Google Workspace accounts unless the
domain admin enables them, which is why Brevo is the default route here.

Env vars:
  BREVO_API_KEY      Brevo (ex-Sendinblue) API key — the preferred route
  MAIL_FROM          verified sender address, e.g. mukunthini@slideegg.com
  MAIL_TO            recipient(s), comma separated. Default admin@slideegg.com
  MAIL_APP_PASSWORD  only for the Gmail fallback (16-char App Password)
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
# Remembers which problem has already been mailed about, so an hourly workflow
# cannot turn one broken session into twenty-four identical emails.
ALERT_STATE = STATE / "alert.json"

BREVO_KEY = os.environ.get("BREVO_API_KEY", "").strip()
MAIL_FROM = os.environ.get("MAIL_FROM", "").strip()
MAIL_PASS = os.environ.get("MAIL_APP_PASSWORD", "").strip()
# One recipient, by request: admin@slideegg.com and nobody else. The workflows
# deliberately do NOT pass MAIL_TO any more — a stale secret pointing at a
# personal inbox was exactly the mail that was asked to stop, and a default in
# code can be read and checked whereas a secret cannot. The override survives
# only for running this by hand.
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

    # The poster writes its own verdict here now. It stays green in GitHub on
    # purpose (a red run emails the repository owner hourly, which is what we
    # were asked to stop), so this field is the only place a crash or a stall
    # shows up.
    if run.get("problem"):
        # Only the first letter — .capitalize() would lowercase the rest and
        # turn "RuntimeError" into "runtimeerror" in the subject line.
        p = str(run["problem"])
        return True, (p[:1].upper() + p[1:]),\
            "Open the run log for the full detail."

    if run.get("failed"):
        return True, f"{run['failed']} post(s) failed to send",\
            f"Failed URLs: {', '.join(run.get('failed_urls', [])) or 'see the run log'}"

    if run.get("sender_error"):
        return True, "The WhatsApp connection is broken",\
            f"{run['sender_error']}. Usually this means the linked device was " \
            "removed from the phone — run the Pair workflow once and scan the QR."

    if run.get("mode") == "dry":
        return True, "The bot is in preview mode — nothing is being sent",\
            f"Reason given: {run.get('why_dry') or 'unknown'}. " \
            "Check the 'mode' input on the workflow and the WA_SESSION_KEY secret."

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
    """The one routine email: what went out this week, and nothing else.

    This used to carry a stats grid, a day-by-day bar chart and a health
    banner. It was asked to be the list of posts only, so that is all it is
    now — problems travel by their own alert mail instead of riding along in
    a report that is a week out of date by the time anything is wrong.
    """
    rows = load_rows()
    end = now.date()
    start = end - dt.timedelta(days=6)
    week = in_range(rows, start, end)

    subject = (f"[SlideEgg WhatsApp] {len(week)} post"
               f"{'' if len(week) == 1 else 's'} — {start:%d %b} to {end:%d %b %Y}")
    return subject, shell(
        f"Posted this week · {start:%d %b} – {end:%d %b %Y}",
        f"{len(week)} post{'' if len(week) == 1 else 's'} went out to the "
        f"WhatsApp Channel over the last 7 days.",
        table(week))


# ---------------------------------------------------------------- alerts

# The poster runs every hour. Without a cooldown, one broken session would
# send twenty-four identical mails a day, which is the inbox flood this whole
# change exists to end. One mail per problem, and a second only if it is still
# broken a day later.
ALERT_COOLDOWN_HOURS = 24


def load_alert_state():
    return load_json(ALERT_STATE)


def save_alert_state(d):
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        ALERT_STATE.write_text(json.dumps(d, indent=1))
    except OSError as e:
        log(f"! could not save the alert state: {e}")


def hours_since(ts, now):
    if not ts:
        return None
    try:
        then = dt.datetime.fromisoformat(ts)
    except ValueError:
        return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=IST)
    return (now - then).total_seconds() / 3600.0


def build_alert(headline, detail, now):
    box = (f"<div style='border-left:5px solid #C0392B;background:#FDF1EF;"
           f"padding:14px 16px'>"
           f"<div style='font:700 15px system-ui;color:#C0392B'>{esc(headline)}</div>"
           f"<div style='font:13px system-ui;color:#444;margin-top:6px'>{esc(detail)}</div>"
           f"</div>")
    fix = ("<h3 style='font:600 15px system-ui;color:#222;margin:22px 0 8px'>"
           "What usually fixes it</h3>"
           "<ol style='font:13px system-ui;color:#444;padding-left:18px;margin:0'>"
           "<li style='margin-bottom:6px'>On the phone holding the bot number, open "
           "<b>WhatsApp &rarr; Linked devices</b>. If <b>Ubuntu / Chrome</b> is gone, "
           "the session was logged out — run the <b>Pair WhatsApp</b> workflow once "
           "and scan the QR.</li>"
           "<li style='margin-bottom:6px'>Check that number is still an <b>admin</b> "
           "of the SlideEgg channel.</li>"
           "<li>Open the latest run log and read the last twenty lines.</li>"
           "</ol>")
    return (f"[SlideEgg WhatsApp] Posting needs attention — {headline}",
            shell(f"Alert · {now:%d %b %Y, %H:%M} IST",
                  "The auto-poster hit a problem and stopped delivering.",
                  box + fix))


def build_recovery(now):
    box = ("<div style='border-left:5px solid #1E8449;background:#F0F8F3;"
           "padding:14px 16px'>"
           "<div style='font:700 15px system-ui;color:#1E8449'>Posting is working again</div>"
           "<div style='font:13px system-ui;color:#444;margin-top:6px'>"
           "The problem reported earlier has cleared on its own. Nothing to do.</div>"
           "</div>")
    return ("[SlideEgg WhatsApp] Posting is working again",
            shell(f"Recovered · {now:%d %b %Y, %H:%M} IST",
                  "This is the all-clear for the alert sent earlier.", box))


def run_alert(now, dry=False):
    """Mail the one address only when something is actually wrong.

    Silence is the normal outcome. Returns 0 whatever happens: this runs
    inside the posting workflow, and a non-zero exit there would turn the run
    red and mail the repository owner — the exact thing being replaced.
    """
    problem, headline, detail = health_note(now)
    st = load_alert_state()
    open_headline = st.get("open")

    if not problem:
        if open_headline:
            log(f"recovered from: {open_headline}")
            subject, html = build_recovery(now)
            if not dry:
                send(subject, html)
                save_alert_state({"open": None, "recovered_at": now.isoformat()})
            else:
                preview(subject, html)
        else:
            log("healthy — no alert sent")
        return 0

    since = hours_since(st.get("sent_at"), now)
    if open_headline == headline and since is not None and since < ALERT_COOLDOWN_HOURS:
        log(f"problem unchanged ({headline}) and last mailed {since:.1f}h ago "
            f"— staying quiet until {ALERT_COOLDOWN_HOURS}h have passed")
        return 0

    log(f"alerting: {headline}")
    subject, html = build_alert(headline, detail, now)
    if dry:
        preview(subject, html)
        return 0
    if send(subject, html) == 0:
        save_alert_state({"open": headline, "sent_at": now.isoformat()})
    return 0


# ---------------------------------------------------------------- sending

def send_brevo(subject, html):
    """Send through Brevo's REST API."""
    import requests
    payload = {
        "sender": {"name": "SlideEgg WhatsApp Bot", "email": MAIL_FROM},
        "to": [{"email": a} for a in MAIL_TO],
        "subject": subject,
        "htmlContent": html,
    }
    try:
        r = requests.post("https://api.brevo.com/v3/smtp/email",
                          headers={"api-key": BREVO_KEY,
                                   "content-type": "application/json",
                                   "accept": "application/json"},
                          json=payload, timeout=60)
    except Exception as e:                       # noqa: BLE001
        log(f"! Brevo request failed: {type(e).__name__}: {e}")
        return 1

    if r.status_code in (200, 201, 202):
        log(f"sent via Brevo to {', '.join(MAIL_TO)}: {subject}")
        return 0

    body = r.text[:400]
    log(f"! Brevo HTTP {r.status_code}: {body}")
    if r.status_code == 401:
        log("! The API key was rejected. Create a fresh one in Brevo under "
            "SMTP & API -> API keys, and update the BREVO_API_KEY secret.")
    elif "sender" in body.lower():
        log(f"! Brevo will not send from {MAIL_FROM} until that address is "
            "verified. In Brevo: Senders & IP -> Senders -> Add a sender, then "
            "click the confirmation link Brevo emails to that address.")
    return 1


def send(subject, html):
    if not MAIL_FROM:
        log("! MAIL_FROM is not set — cannot send")
        return 1

    if BREVO_KEY:
        return send_brevo(subject, html)

    if not MAIL_PASS:
        log("! No BREVO_API_KEY and no MAIL_APP_PASSWORD — cannot send. "
            "Set BREVO_API_KEY (recommended: Gmail App Passwords are blocked "
            "on Google Workspace accounts).")
        return 1

    log("BREVO_API_KEY not set, falling back to Gmail SMTP")
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


def preview(subject, html):
    print("SUBJECT:", subject)
    out = ROOT / "report_preview.html"
    out.write_text(html, encoding="utf-8")
    print("preview written to", out)


def main():
    now = dt.datetime.now(IST)
    dry = "--dry-run" in sys.argv

    # The alert decides for itself whether to send anything at all, so it does
    # not go through the build/send path the reports use.
    if "--alert" in sys.argv:
        return run_alert(now, dry=dry)

    if "--weekly" in sys.argv:
        subject, html = build_weekly(now)
    else:
        subject, html = build_daily(now)

    if dry:
        preview(subject, html)
        return 0
    return send(subject, html)


if __name__ == "__main__":
    sys.exit(main())
