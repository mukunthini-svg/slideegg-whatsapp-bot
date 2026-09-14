# SlideEgg → WhatsApp Channel auto-poster — full handover

**Read this first if you are an AI assistant picking this project up in a new chat.**
Everything needed to understand, run, repair or rebuild the system is in this
one file. Follow "Standing orders" (§2) exactly — those are the owner's
decisions, not suggestions, and several of them were reached the hard way.

Owner: mukunthini (Social Media Executive, SlideEgg). Prefers Tanglish
(Tamil written in English letters). Answer in Tanglish; keep technical terms
in English.

Last updated: 8 September 2026.

---

## 1. What it does, in one paragraph

Every hour, a GitHub Actions job scrapes slideegg.com for newly published
PowerPoint templates and blog posts, drops anything it has posted before,
builds a WhatsApp caption with the thumbnail, and posts it to SlideEgg's
official WhatsApp Channel — free, using a self-hosted WhatsApp Web
connection. It caps itself at 8 posts a day, keeps a permanent CSV record of
everything it sent, emails a list of the week's posts every Monday, and
emails an alert if posting breaks. No human touches it.

**Current status: live and working.** Free transport (Baileys) has been
delivering since 7 September 2026.

---

## 2. Standing orders (the owner's decisions — do not quietly change these)

| # | Order | Why it matters |
|---|---|---|
| 1 | **Free tools only.** Never propose a paid API again. | Whapi.cloud cost ₹2,900/month and its trial expiring is what broke the system for two weeks. The owner said "enaku free tools than venum" four separate times. |
| 2 | **Post new templates and blog posts automatically, 24/7.** | No approval step, no human in the loop. |
| 3 | **Maximum 8 posts per day.** | SlideEgg publishes far more than 8/day. Posting everything would look like spam. |
| 4 | **One weekly email to `admin@slideegg.com`: the list of posts, nothing else.** | No daily summaries, no charts, no stats grids. |
| 5 | **Email that same address only when posting actually breaks.** | And at most once a day for the same fault — see §9. |
| 6 | **Never email mukunthini personally.** | GitHub's own "workflow failed" mail was arriving every 15 minutes. This is why runs now always exit 0 (§9). |
| 7 | **Do not paste secrets into chat.** | Tokens and keys go straight into GitHub Secrets. A Whapi token (`LIAjhRiu…`) was exposed in chat once and should still be regenerated if that account is ever revived. |
| 8 | **Never log in as the owner or type their passwords.** | Ask them to do it; guide them through it. |

---

## 3. Quick facts

| | |
|---|---|
| Repository | `github.com/mukunthini-svg/slideegg-whatsapp-bot` (**public**) |
| Site scraped | `https://www.slideegg.com` |
| Channel | `https://whatsapp.com/channel/0029Vb7WIkq35fLwXKie5521` |
| Bot number | `+91 9363904228` (the owner's own phone; it is a channel admin) |
| Transport | Baileys — WhatsApp Web protocol, self-hosted, **₹0/month** |
| Scheduler | GitHub Actions cron, hourly at minute 7 |
| Cap | 8 posts/day (IST calendar day) |
| Email recipient | `admin@slideegg.com` only |
| Email sender | Brevo REST API (Gmail SMTP is the unused fallback) |
| Runtime | Python 3.12 + Node 22, both installed by the workflow |

---

## 4. How one run works, step by step

```
GitHub Actions cron  →  python slideegg_daily.py
        │
        ├─ 1. Is it inside posting hours?  ACTIVE_FROM=0, ACTIVE_TO=24 → always yes
        │
        ├─ 2. Scrape 3 listing pages of /latest-powerpoint-templates
        │       Parse the JSON-LD ImageObject array → title, url, thumbnail
        │       (NOT the sitemap: its <lastmod> lags days behind the live page)
        │
        ├─ 3. Scrape /blog/ index → post urls
        │       Open each unseen post, read its publish date,
        │       discard anything older than MAX_AGE_DAYS=14
        │       (SlideEgg re-edits old posts for SEO, which pushes 2024
        │        articles back to the top of the index. Without this guard
        │        those refreshes get announced as brand new.)
        │
        ├─ 4. Drop every url already in state/seen.json
        │
        ├─ 5. Budget = min(MAX_POSTS, DAILY_LIMIT − already posted today)
        │       Queue order: blog posts first (rarer, higher value), then
        │       newest templates. Overflow beyond the day's quota is RETIRED
        │       (marked seen without posting), not queued — otherwise the
        │       channel falls permanently behind and starts announcing
        │       week-old templates.
        │
        ├─ 6. For each item: enrich (fetch meta description), build caption,
        │       download the thumbnail, hand it to the sender
        │
        ├─ 7. Baileys helper posts it to the channel
        │
        ├─ 8. Record: seen.json, daily.json, posts.csv, last_run.json,
        │       health.json
        │
        └─ 9. watchdog() decides if anything is wrong and writes its verdict
                to last_run.json["problem"]. Exit code is ALWAYS 0.
                            │
                            ▼
        python report.py --alert   (same job, scheduled runs only)
                Sends one email to admin@slideegg.com IF there is a problem
                AND it has not already mailed about that same problem in the
                last 24 hours. Otherwise silent.
                            │
                            ▼
        git commit state/  →  push
```

### The caption

```
✨ *New Template on SlideEgg*

*<title>*

<meta description, trimmed to 200 chars>

✅ Fully editable
✅ PowerPoint + Google Slides + Canva

👉 Download free:
<url>

#SlideEgg #PowerPointTemplates #GoogleSlides #Presentation
```

Blog posts use `📖 *New on the SlideEgg Blog*`, `👉 Read it here:` and
`#SlideEgg #PresentationTips #PowerPoint #GoogleSlides`, with no perks list.

---

## 5. File map

```
slideegg_daily.py          The poster. Scraping, de-duplication, captions,
                           budgets, state, watchdog. ~1180 lines.
report.py                  Email. Weekly list + problem alerts.
requirements.txt           requests>=2.31.0 — that is the whole dependency list.

baileys/
  package.json             @itsliaaa/baileys ^0.3.18-final, qrcode, qrcode-terminal
  wa.js                    connect(), save(), resolveChannel() — shared
  session.js               AES-256-GCM encryption of the WhatsApp login
  send.js                  One connection per run, driven over stdin/stdout JSON
  pair.js                  One-time login: prints a QR (or a pairing code)
  test_session.js          25 checks on the encryption

.github/workflows/
  autopost.yml             Hourly. The main job.
  report.yml               Monday 09:00 IST. The weekly email.
  pair.yml                 Manual only. Links the WhatsApp number.

state/                     Committed by the workflow after every run
  seen.json                Every url ever posted (capped at 20,000)
  daily.json               {"date": "2026-09-08", "posted": 3, "limit": 8}
  posts.csv                Permanent record — the weekly email reads this
  last_run.json            Diagnostics from the last run, incl. "problem"
  health.json              {"last_new_item_at": ..., "last_checked_at": ...}
  alert.json               Which problem has already been emailed, and when
  wa-session.enc           The encrypted WhatsApp login

test_offline.py            Poster logic, no network
test_watchdog.py           The alarm
test_report.py             Email building
test_alerts.py             Alert policy: silence, cooldown, recovery
test_baileys.py            Transport wiring
test_channel_recovery.py   Channel-id recovery

AppsScript/                DORMANT. An older Google Sheets + email reporter.
                           Its triggers were deleted on the owner's request.
                           Do not re-enable without asking.
```

---

## 6. Configuration

### GitHub Secrets (Settings → Secrets and variables → Actions)

| Secret | Used by | Purpose |
|---|---|---|
| `WA_SESSION_KEY` | autopost, pair | Passphrase encrypting `wa-session.enc`. **Changing it invalidates the session** — you would have to re-pair. Must be ≥16 chars. |
| `BREVO_API_KEY` | autopost, report | Sends the emails. |
| `MAIL_FROM` | autopost, report | Verified Brevo sender address. |
| `WA_CHANNEL_INVITE` | pair | Optional; defaults to the SlideEgg invite code in the source. |
| `WHAPI_TOKEN`, `WHAPI_CHANNEL` | — | Dead. The paid route. Safe to delete. |
| `MAIL_TO` | — | **Deliberately no longer passed to any workflow.** `report.py` hardcodes `admin@slideegg.com` as its default so the recipient is visible in source rather than hidden in a secret. |
| `MAIL_APP_PASSWORD` | fallback | Gmail SMTP fallback, unused. Google Workspace blocks App Passwords unless the domain admin enables them, which is why Brevo is primary. |

### Environment variables (set in `autopost.yml`)

| Var | Value | Meaning |
|---|---|---|
| `SENDER` | `baileys` | `baileys` (free) or `whapi` (dead paid route) |
| `DRY_RUN` | `0` | `1` = build everything, send nothing |
| `SOURCES` | `templates,blog` | What to watch |
| `SCAN_PAGES` | `3` | Listing pages scanned, 24 templates each |
| `MAX_POSTS` | `8` | Ceiling for a single run |
| `DAILY_LIMIT` | `8` | Ceiling for an IST calendar day — the real limit |
| `MAX_AGE_DAYS` | `14` | Blog posts older than this are not "new" |
| `ACTIVE_FROM` / `ACTIVE_TO` | `0` / `24` | Posting window, IST hours. 0/24 = always |
| `ALERT_AFTER_HOURS` | `24` | Silence longer than this is treated as a fault |
| `EXIT_ON_ERROR` | unset | Set to `1` only when debugging by hand, to get non-zero exit codes back |

### Schedules

| Workflow | Cron (UTC) | IST |
|---|---|---|
| autopost | `7 * * * *` | hourly at :07 |
| report | `30 3 * * 1` | Monday 09:00 |
| pair | manual only | — |

**Why hourly and not every 15 minutes:** each GitHub Actions run connects to
WhatsApp from a different datacenter IP. A real linked device does not
reconnect from a new address four times an hour; that pattern is what gets
numbers flagged and banned. Hourly is a deliberate trade. Since the cap is 8
posts/day, hourly checking is not the bottleneck anyway. The owner was told
this and accepted it.

---

## 7. The WhatsApp transport (this is the part that took the longest)

There is **no official Meta API for WhatsApp Channels at any price.** The
WhatsApp Business API (Wati, Gupshup, Meta Cloud API) sends to individual
phone numbers; it cannot post to a Channel. Every route to a Channel is
unofficial. Do not go looking for an official one — it does not exist.

We use **Baileys** (`@itsliaaa/baileys`, MIT), which speaks the WhatsApp Web
protocol directly and supports `…@newsletter` JIDs, i.e. Channels.

### How the session is stored

Baileys keeps credentials as a folder of small JSON files. **That folder is
the WhatsApp login** — anyone with a copy can read and send as the number —
and the repository is public. So `session.js` packs the folder into a single
AES-256-GCM blob keyed on `WA_SESSION_KEY` and commits that as
`state/wa-session.enc`. GitHub never exposes secrets on public repos, so the
committed file is inert to everyone else.

`persist()` writes nothing and returns 0 when the credentials have not
changed. Without that, a fresh random IV each run would commit a "new"
session every hour forever.

### How posting works

`slideegg_daily.py` starts `baileys/send.js` **once per run** and drives it
over stdin/stdout, one JSON object per line:

```
→  {"caption": "...", "media": "data:image/png;base64,..."}
←  {"ok": true}
```

One connection for the whole run. Opening a fresh WhatsApp connection per
post looks like an attack. `send.js` never calls `logout()` — that would
unlink the device permanently; it uses `sock.end(undefined)`.

### Pairing (only needed once, or after a logout)

Run the **Pair WhatsApp** workflow manually, choose `qr`, type `yes` in the
confirm box, open the running step's log, and point the phone camera at the
QR printed there. A fresh QR replaces the old one every ~100 seconds, so
there is no race. On success the workflow commits the new
`state/wa-session.enc`.

Three traps here, all of which cost real time:

1. **Pairing-code mode needs `['Ubuntu', 'Chrome', '20.0.04']` as the browser
   identity.** With `Browsers.macOS('Desktop')` WhatsApp happily *issues*
   codes but *rejects* every one of them with "Couldn't link device — get a
   new code", which reads exactly like an expiry problem and is not one.
   Fourteen pairing attempts were burned before this was found.
2. **Each retry needs its own fresh auth folder.** Reusing one folder across
   attempts leaves half-registered credentials behind, and WhatsApp answers
   every later connection with "device logged out" instead of a new code — so
   only the first attempt was ever real. `pair.js` now wipes and recreates
   `.wa-auth/` inside the retry loop.
3. **QR rendering.** `qrcode-terminal` paints modules with ANSI *background*
   colours, and GitHub's log viewer strips those — the QR renders as blank
   lines. Painting `██` per module works but is 74 characters wide, so the log
   wraps every row and destroys the pattern. The fix is
   `QRCode.toString({type:'utf8'})`, which uses half-blocks (`▀▄█`) — one
   character per module, two module rows per line, ~37 characters wide and
   square. Do not "improve" this.

---

## 8. Channel resolution

`baileys/wa.js` `resolveChannel()` asks WhatsApp for the channel by invite
code (`sock.newsletterMetadata('invite', INVITE)`), falling back to matching
by name across `sock.newsletterSubscribed()`.

**Trap:** `slideegg_daily.py` also has a `resolve_channel()` — that one is a
*Whapi HTTP call*. It used to run unconditionally in `main()`, so under
`SENDER=baileys` it called a dead Whapi account, got HTTP 404, and aborted the
whole run before posting. It is now gated:

```python
if not DRY_RUN and SENDER == "whapi":
    CHANNEL = resolve_channel(CHANNEL)
```

Leave that gate alone.

---

## 9. Email policy (changed 8 Sep 2026 — this is the current design)

Two emails leave this system and no others. Both go to
**`admin@slideegg.com`**.

### The weekly list — Monday 09:00 IST

`report.py --weekly` renders the rows of `state/posts.csv` from the last 7
days as a table: date, time, type, title (linked). That is the entire email.
It used to carry a stats grid, a day-by-day bar chart and a health banner;
those were removed on request. It is sent even if the week was empty, so
silence from the system is never ambiguous.

### The alert — only when broken

`report.py --alert` runs after **every scheduled** posting run and **usually
sends nothing**. It sends when `health_note()` reports a problem:

- `last_run.json["problem"]` is set (the watchdog's or a crash's verdict)
- posts failed to send
- `sender_error` is set (usually: the linked device was removed)
- the run went into preview mode unexpectedly
- nothing new detected for over 24 hours (catches a stale cached page)
- the workflow has not run for over 6 hours (catches GitHub disabling cron)
- `last_run.json` is missing entirely

**Cooldown:** `state/alert.json` remembers which problem was mailed and when.
The same problem is not mailed again for 24 hours. A *different* problem is
never suppressed. When the fault clears, one "Posting is working again"
email is sent, then silence. Without this, one broken session would send 24
identical emails a day — the exact flood this design exists to end.

Manual (`workflow_dispatch`) runs are excluded from alerting, so testing a
dry run by hand does not look like an outage.

### Why runs always exit 0

A red GitHub Actions run emails the repository owner personally. The owner
asked for that to stop. So `watchdog()` returns 0 always and records its
verdict in `last_run.json["problem"]`, and the `__main__` block catches every
exception, records it via `record_crash()`, and still exits 0. The alert
email is now the only alarm — which means **if you break the alert email, the
system fails silently.** Treat `test_alerts.py` as load-bearing.

Set `EXIT_ON_ERROR=1` to restore non-zero exits when debugging locally.

---

## 10. Runbook

| Symptom | Likely cause | Fix |
|---|---|---|
| Alert: "The WhatsApp connection is broken" / "logged out" | The linked device was removed from the phone, or WhatsApp logged it out | Run the **Pair WhatsApp** workflow, choose `qr`, confirm `yes`, scan the QR from the log |
| Alert: "nothing new for N hours" but the site has new templates | The runner is being served a stale cached page | Compare `diagnostics.page1_top3` in `state/last_run.json` with the live listing page |
| Alert: "the template listing page came back empty" | SlideEgg changed its HTML | Fix `parse_listing()` — it reads the JSON-LD `ImageObject` array |
| Alert: "The bot has not run for N hours" | GitHub disabled the schedule (it does this after 60 days of repo inactivity — normally prevented because every run commits state) | Open the Actions tab and re-enable |
| Alert: "the run crashed: …" | Anything unhandled | `last_run.json["traceback"]` has the last 2,000 chars |
| Posts stopped, no alert at all | The alert path itself is broken | Check `BREVO_API_KEY` and `MAIL_FROM`; run `report.py --alert --dry-run` |
| Duplicate posts | `state/seen.json` was not committed | Check the "Commit state" step; the `concurrency` group should prevent two runs overlapping |
| Old 2024 blog posts being announced | `MAX_AGE_DAYS` guard bypassed | It is 14 days; check `load_blog_post()` is still finding the publish date |

**Never delete `state/seen.json`.** That is the memory of everything already
posted; without it the bot re-announces the whole catalogue.

---

## 11. Rebuilding from zero

1. Create a repo (public = unlimited Actions minutes; private = 2,000/month).
2. Copy every file listed in §5.
3. Add secrets: `WA_SESSION_KEY` (any random ≥16 chars), `BREVO_API_KEY`,
   `MAIL_FROM` (verified in Brevo under Senders & IP → Senders).
4. Run **Pair WhatsApp** once from a phone that is an admin of the channel.
   Scan the QR from the log. It commits `state/wa-session.enc`.
5. Run **autopost** manually with `mode: dry` and read the log. Captions
   should look right and nothing should be sent.
6. First live run: the very first run with an empty `seen.json` writes a
   *baseline* — it records every url as seen without posting, so the channel
   is not flooded with the entire back catalogue. Posting starts from the
   next run.
7. Enable the schedules.

---

## 12. Things not to do

- **Do not propose a paid WhatsApp API.** See standing order 1.
- **Do not change `WA_SESSION_KEY`** unless you intend to re-pair.
- **Do not commit `.wa-auth/` or `qr.png`.** Both are live logins.
  `.gitignore` covers them; keep it that way.
- **Do not call `logout()`** in any Baileys code — it unlinks the device.
- **Do not drop the cron back to every 15 minutes** without telling the owner
  about the ban risk (§6).
- **Do not re-enable the Apps Script triggers** (`AppsScript/`) — the owner
  asked for those emails to stop.
- **Do not make runs exit non-zero** in normal operation (§9).

---

## 13. Open items

- `WA_SESSION_KEY` was generated inside a chat transcript and is therefore
  known to more parties than ideal. Rotating it costs one re-pair. Offered;
  not yet done.
- Optional: a separate ₹200 SIM as the bot number, so a WhatsApp ban would
  not cost the owner's personal number its channel-admin rights.
- The backlog from the Aug 24 – Sep 7 outage drains at 8 posts/day.
