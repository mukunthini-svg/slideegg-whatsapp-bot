#!/usr/bin/env python3
"""
SlideEgg -> WhatsApp Channel daily auto-poster.

Runs once a day (6:00 PM IST via GitHub Actions cron).

Flow:
  1. Read /latest-powerpoint-templates (newest first, 24 per page, ?page=N)
  2. Parse the JSON-LD ImageObject array -> title, template url, thumbnail
  3. Drop anything already in state/seen.json (so nothing posts twice)
  4. Best-effort: pull the meta description from each new template page
  5. Post image + caption to the WhatsApp Channel via Whapi.cloud
  6. Save state/seen.json back (GitHub Actions commits it)

Why not the sitemap: slideegg's sitemap <lastmod> values lag several days
behind the live listing page, so they cannot answer "what went up today".
The listing page is live and strictly newest-first, so a diff against
seen.json is both simpler and more accurate.

Env vars:
  WHAPI_TOKEN     (required for live posting) Whapi.cloud API token
  WHAPI_CHANNEL   (optional) Channel id (1203630xxx@newsletter), an invite code,
                  or the full https://whatsapp.com/channel/<code> link.
                  Defaults to SlideEgg's official channel; invite codes and links
                  are resolved to a channel id automatically.
  DRY_RUN         "1" = build posts but do not send (default when no token)
  MAX_POSTS       max templates to post in one run (default 5)
  SCAN_PAGES      how many listing pages to scan, 24 templates each (default 3)
"""

import base64
import json
import os
import re
import subprocess
import sys
import time
import html
import pathlib
import datetime as dt
from urllib.parse import urlparse

import requests

# ---------------------------------------------------------------- config

SITE = "https://www.slideegg.com"
LATEST = f"{SITE}/latest-powerpoint-templates"
BLOG = f"{SITE}/blog/"

WHAPI_BASE = "https://gate.whapi.cloud"
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

ROOT = pathlib.Path(__file__).parent
STATE_DIR = ROOT / "state"
SEEN_FILE = STATE_DIR / "seen.json"
LOG_FILE = STATE_DIR / "last_run.json"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")

# SlideEgg's official WhatsApp Channel
# https://whatsapp.com/channel/0029Vb7WIkq35fLwXKie5521
DEFAULT_CHANNEL = "0029Vb7WIkq35fLwXKie5521"

TOKEN = os.environ.get("WHAPI_TOKEN", "").strip()
CHANNEL = os.environ.get("WHAPI_CHANNEL", "").strip() or DEFAULT_CHANNEL
# CHANNEL is replaced by the resolved '...@newsletter' id at run time; keep the
# original link/invite so a rejected id can be resolved again from scratch.
RAW_CHANNEL = CHANNEL
MAX_POSTS = int(os.environ.get("MAX_POSTS", "5"))
SCAN_PAGES = max(1, int(os.environ.get("SCAN_PAGES", "3")))

# How posts actually reach WhatsApp.
#   "whapi"    paid HTTP API (gate.whapi.cloud)
#   "baileys"  free, self-hosted: a Node helper holding one WhatsApp connection
# Only the transport differs — finding items, de-duplicating, captions, images
# and logging are identical either way.
SENDER = os.environ.get("SENDER", "whapi").strip().lower()
WA_SESSION_KEY = os.environ.get("WA_SESSION_KEY", "").strip()
WA_SESSION_FILE = STATE_DIR / "wa-session.enc"


def _credential_problem():
    """Why this run cannot post, or None if it can."""
    if SENDER == "baileys":
        if not WA_SESSION_KEY:
            return "WA_SESSION_KEY missing/empty"
        if not WA_SESSION_FILE.exists():
            return "no paired WhatsApp session — run the Pair workflow once"
        return None
    if SENDER != "whapi":
        return f"unknown SENDER {SENDER!r} (expected 'whapi' or 'baileys')"
    return "WHAPI_TOKEN missing/empty" if not TOKEN else None


CREDENTIAL_PROBLEM = _credential_problem()
DRY_RUN = (os.environ.get("DRY_RUN", "").strip() == "1"
           or CREDENTIAL_PROBLEM is not None)

# Which sources to watch: "templates", "blog", or "templates,blog"
SOURCES = {s.strip().lower() for s in
           os.environ.get("SOURCES", "templates,blog").split(",") if s.strip()}

# A blog post only counts as new if it was PUBLISHED this recently. SlideEgg
# continually re-edits old posts for SEO, which bumps sitemap lastmod and can
# push a 2024 article back to the top of the index — without this guard those
# refreshes would be announced as new.
MAX_AGE_DAYS = int(os.environ.get("MAX_AGE_DAYS", "14"))

# Quiet hours (IST, 24h). ACTIVE_FROM=0 / ACTIVE_TO=24 means round the clock.
# Outside the window the run exits without posting; anything published
# meanwhile is picked up at the next allowed run.
ACTIVE_FROM = int(os.environ.get("ACTIVE_FROM", "0"))
ACTIVE_TO = int(os.environ.get("ACTIVE_TO", "24"))

# Hard ceiling on posts per calendar day (IST), across every run of that day.
# SlideEgg publishes far more than this, so the day's quota goes to the NEWEST
# items and the overflow is retired rather than queued — otherwise the channel
# would fall permanently behind and start announcing week-old templates.
DAILY_LIMIT = int(os.environ.get("DAILY_LIMIT", "8"))
DAILY_FILE = STATE_DIR / "daily.json"

# URL path segments that are category/listing pages, not individual templates
NON_TEMPLATE_PAT = re.compile(
    r"/(blog|category|categories|tag|search|about|contact|pricing|login|signup|"
    r"privacy|terms|sitemap|author|newsletter|interactive|redesign)(/|$)", re.I)

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})


def log(msg):
    print(f"[{dt.datetime.now(IST):%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def get(url, **kw):
    kw.setdefault("timeout", 45)
    last = None
    for attempt in range(3):
        try:
            r = SESSION.get(url, **kw)
            if r.status_code == 200:
                return r
            last = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            last = str(e)
        time.sleep(2 * (attempt + 1))
    log(f"  ! failed {url} ({last})")
    return None


# ---------------------------------------------------------------- listing

# Every listing page embeds a JSON-LD array of ImageObject entries, one per
# template card, carrying the title, the template page url and the thumbnail.
# That is far more stable than parsing the card markup.
LD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.I | re.S)

# .../image/webpv2/670/102291-data-storytelling-670.webp
#   -> .../image/catalog/102291-data-storytelling.png   (the og:image original)
THUMB_SUFFIX_RE = re.compile(r"-\d+\.(?:webp|jpe?g|png)$", re.I)


def catalog_png(content_url):
    """Derive the full-size PNG from a listing thumbnail url."""
    if not content_url:
        return None
    fname = content_url.rstrip("/").split("/")[-1]
    base = THUMB_SUFFIX_RE.sub("", fname)
    if not base:
        return None
    return f"{SITE}/image/catalog/{base}.png"


def is_template_url(url):
    p = urlparse(url)
    if p.netloc and "slideegg.com" not in p.netloc:
        return False
    path = p.path.rstrip("/")
    if not path or NON_TEMPLATE_PAT.search(path):
        return False
    return len([s for s in path.split("/") if s]) == 1


def parse_listing(html_text):
    """Pull template entries out of a listing page's JSON-LD."""
    items = []
    for block in LD_RE.findall(html_text):
        try:
            data = json.loads(block.strip())
        except ValueError:
            continue
        if not isinstance(data, list):
            continue
        for node in data:
            if not isinstance(node, dict) or node.get("@type") != "ImageObject":
                continue
            url = (node.get("acquireLicensePage") or "").strip()
            if not is_template_url(url):
                continue
            title = re.sub(r"\s+", " ", (node.get("name") or "")).strip()
            if not title:
                title = url.rstrip("/").split("/")[-1].replace("-", " ").title()
            items.append({
                "url": url,
                "title": title,
                "image": catalog_png(node.get("contentUrl")),
                "thumb": node.get("contentUrl"),
                "kind": "template",
            })
    return items


DIAG = {}


def scan_latest(pages):
    """Scan the newest-first listing pages. Returns items in newest-first order."""
    out, seen_urls = [], set()
    for n in range(1, pages + 1):
        url = LATEST if n == 1 else f"{LATEST}?page={n}"
        # Defeat any intermediary cache: without this a stale copy of page 1
        # makes the run look healthy while silently finding nothing new.
        r = get(url, headers={"Cache-Control": "no-cache", "Pragma": "no-cache"})
        if not r:
            break
        found = parse_listing(r.text)
        if n == 1:
            DIAG["page1_top3"] = [it["url"].rstrip("/").split("/")[-1] for it in found[:3]]
            DIAG["page1_count"] = len(found)
            DIAG["page1_http_date"] = r.headers.get("date")
            DIAG["page1_age"] = r.headers.get("age")
            DIAG["page1_x_cache"] = r.headers.get("x-cache")
            DIAG["page1_bytes"] = len(r.content)
            log(f"  page 1 newest: {DIAG['page1_top3']}")
            log(f"  page 1 served: date={DIAG['page1_http_date']} "
                f"age={DIAG['page1_age']} x-cache={DIAG['page1_x_cache']}")
        log(f"  page {n}: {len(found)} templates")
        if not found:
            break
        for it in found:
            if it["url"] not in seen_urls:
                seen_urls.add(it["url"])
                out.append(it)
        time.sleep(1)
    return out


# ---------------------------------------------------------------- blog

# /blog/<category>/<slug>/ — category index pages have one segment fewer
BLOG_POST_RE = re.compile(
    r'href=["\'](https://www\.slideegg\.com/blog/[a-z0-9-]+/[a-z0-9-]+/)["\']', re.I)

PUBLISHED_RE = re.compile(r'"datePublished"\s*:\s*"([^"]+)"', re.I)


def parse_iso_date(s):
    if not s:
        return None
    try:
        d = dt.datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
    except ValueError:
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s.strip())
        return dt.date(*map(int, m.groups())) if m else None
    return (d.astimezone(IST).date() if d.tzinfo else d.date())


def scan_blog():
    """Blog index, newest-first. Returns entries with their publish date.

    Deliberately NOT driven by blog/sitemap.xml: its <lastmod> tracks edits,
    and SlideEgg refreshes old posts daily, so lastmod says nothing about
    whether a post is new.
    """
    r = get(BLOG)
    if not r:
        return []
    urls = []
    for u in BLOG_POST_RE.findall(r.text):
        if u not in urls:
            urls.append(u)
    log(f"  blog index: {len(urls)} posts listed")
    return urls


def load_blog_post(url):
    """Fetch a blog post and return an item, or None if it is not new enough."""
    r = get(url)
    if not r:
        return None
    t = r.text

    published = parse_iso_date(first_match([PUBLISHED_RE.pattern], t))
    age = (dt.datetime.now(IST).date() - published).days if published else None
    if age is not None and age > MAX_AGE_DAYS:
        log(f"  skip (published {published}, {age}d old — an SEO refresh, not a new post)")
        return None

    title = first_match(META_PATTERNS["title"], t) or url.rstrip("/").split("/")[-1]
    title = re.sub(r"\s*[\|\-–]\s*SlideEgg.*$", "", title, flags=re.I).strip()
    return {
        "url": url,
        "title": re.sub(r"\s+", " ", title),
        "image": abs_url(first_match(META_PATTERNS["image"], t)),
        "desc": first_match(META_PATTERNS["desc"], t),
        "kind": "blog",
        "published": str(published) if published else None,
    }


# ---------------------------------------------------------------- scraping

META_PATTERNS = {
    "title": [r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\'](.*?)["\']',
              r'<meta[^>]+name=["\']twitter:title["\'][^>]+content=["\'](.*?)["\']',
              r'<title[^>]*>(.*?)</title>'],
    "image": [r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\'](.*?)["\']',
              r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\'](.*?)["\']'],
    "desc":  [r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\'](.*?)["\']',
              r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']'],
}


def first_match(patterns, text):
    for pat in patterns:
        m = re.search(pat, text, re.I | re.S)
        if m and m.group(1).strip():
            return html.unescape(m.group(1).strip())
    return None


def abs_url(u):
    if not u:
        return None
    if u.startswith("//"):
        return "https:" + u
    if u.startswith("/"):
        return SITE + u
    return u


def enrich(item):
    """Best-effort: add a description, and trust the page's own og:image
    over our derived one. Never fails the item — the listing data alone
    is already enough to post."""
    r = get(item["url"])
    if not r:
        log("  (no description — page fetch failed, posting listing data)")
        return item
    t = r.text

    desc = first_match(META_PATTERNS["desc"], t)
    if desc:
        item["desc"] = desc

    og = abs_url(first_match(META_PATTERNS["image"], t))
    if og:
        item["image"] = og

    og_title = first_match(META_PATTERNS["title"], t)
    if og_title:
        og_title = re.sub(r"\s*[\|\-–]\s*SlideEgg.*$", "", og_title, flags=re.I)
        og_title = re.sub(r"\s+", " ", og_title).strip()
        if og_title:
            item["title"] = og_title
    return item


# ---------------------------------------------------------------- caption

def build_caption(item):
    title = item["title"]
    desc = re.sub(r"\s+", " ", (item.get("desc") or "").strip())
    if len(desc) > 200:
        desc = desc[:197].rsplit(" ", 1)[0] + "..."

    if item.get("kind") == "blog":
        header = "📖 *New on the SlideEgg Blog*"
        cta = "👉 Read it here:"
        tags = "#SlideEgg #PresentationTips #PowerPoint #GoogleSlides"
        perks = []
    else:
        header = "✨ *New Template on SlideEgg*"
        cta = "👉 Download free:"
        tags = "#SlideEgg #PowerPointTemplates #GoogleSlides #Presentation"
        perks = ["", "✅ Fully editable", "✅ PowerPoint + Google Slides + Canva"]

    lines = [header, "", f"*{title}*"]
    if desc:
        lines += ["", desc]
    lines += perks
    lines += ["", cta, item["url"], "", tags]
    return "\n".join(lines)


# ---------------------------------------------------------------- whapi

INVITE_RE = re.compile(r"(?:whatsapp\.com/channel/)?([A-Za-z0-9_-]{20,30})\s*$")


def whapi_auth():
    return {"Authorization": f"Bearer {TOKEN}", "Accept": "application/json"}


def resolve_channel(raw):
    """Turn an invite code or channel link into a '...@newsletter' id.

    Already-resolved ids pass straight through. The result is cached in
    state/channel.json so this only costs one API call, ever.
    """
    raw = (raw or "").strip()
    if raw.endswith("@newsletter"):
        return raw

    cache = STATE_DIR / "channel.json"
    if cache.exists():
        try:
            c = json.loads(cache.read_text())
            if c.get("invite") == raw and c.get("id"):
                return c["id"]
        except (ValueError, OSError):
            pass

    m = INVITE_RE.search(raw)
    code = m.group(1) if m else None

    # /newsletters/{id} only accepts a numeric '...@newsletter' id, so an
    # invite code has to be matched against the channels this number is in.
    try:
        r = SESSION.get(f"{WHAPI_BASE}/newsletters", params={"count": 100},
                        headers=whapi_auth(), timeout=60)
    except requests.RequestException as e:
        log(f"! channel list failed: {e}")
        return None

    if r.status_code != 200:
        log(f"! channel list HTTP {r.status_code}: {r.text[:300]}")
        return None

    try:
        data = r.json()
    except ValueError:
        log("! channel list returned non-JSON")
        return None

    if isinstance(data, dict):
        for key in ("newsletters", "items", "chats"):
            if key in data:          # an empty list here means "none", not "look elsewhere"
                items = data[key]
                break
        else:
            items = [data]
    else:
        items = data
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        log(f"! unexpected channel list shape: {json.dumps(data)[:300]}")
        return None

    def info(node):
        meta = node.get("thread_metadata") or {}
        cid = node.get("id") or node.get("jid") or node.get("chat_id") or ""
        return {
            "id": cid,
            "name": (node.get("name") or node.get("subject")
                     or meta.get("name") or "?"),
            "role": (node.get("role") or node.get("invite")
                     or meta.get("role") or ""),
            "blob": json.dumps(node),
        }

    chans = [info(n) for n in items if isinstance(n, dict)]
    log(f"  this number is in {len(chans)} channel(s):")
    for c in chans:
        log(f"    - {c['name']}  {c['id']}  {c['role']}")

    if not chans:
        log("! this WhatsApp number is not in any channel. Add it as a channel "
            "admin in WhatsApp (channel -> Manage admins), then re-run.")
        return None

    pick = None
    if code:                       # invite code appears somewhere in the entry
        pick = next((c for c in chans if code in c["blob"]), None)
    if not pick:                   # fall back to the channel name
        pick = next((c for c in chans if "slideegg" in c["name"].lower()), None)
    if not pick and len(chans) == 1:
        pick = chans[0]
    if not pick:
        log("! could not tell which of these channels to post to. Set the "
            "WHAPI_CHANNEL secret to the exact '...@newsletter' id above.")
        return None

    cid, name = str(pick["id"]), pick["name"]
    if not cid:
        log(f"! channel '{name}' has no id in the API response")
        return None
    if not cid.endswith("@newsletter"):
        cid = f"{cid}@newsletter"

    log(f"  resolved channel: {name} -> {cid}")

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"invite": raw, "id": cid, "name": name}, indent=1))
    return cid


_CHANNEL_REFRESHED = False


def refresh_channel():
    """The cached channel id was rejected — throw it away and resolve again.

    The id is cached forever once resolved, so a channel that is recreated,
    or an account that reconnects under a new session, would otherwise keep
    posting to a dead id and failing on every run. Runs at most once per run.
    Returns True only if a genuinely different id came back.
    """
    global CHANNEL, _CHANNEL_REFRESHED
    if _CHANNEL_REFRESHED:
        return False
    _CHANNEL_REFRESHED = True

    log("  the cached channel id was rejected — clearing it and resolving again")
    try:
        (STATE_DIR / "channel.json").unlink()
    except OSError:
        pass

    fresh = resolve_channel(RAW_CHANNEL)
    if not fresh:
        log("  ! could not resolve the channel at all. Either this WhatsApp "
            "number is no longer linked in Whapi, it is no longer an admin of "
            "the channel, or the Whapi plan no longer includes Channels.")
        return False
    if fresh == CHANNEL:
        log("  the same id came back, so the id is fine and the problem is "
            "access: check the Whapi plan and that the number is still a "
            "channel admin.")
        return False
    log(f"  channel id changed: {CHANNEL} -> {fresh}")
    CHANNEL = fresh
    return True


def _send_once(item, caption, media):
    """One full round of send attempts. Returns 'ok' | 'channel' | 'fail'."""
    headers = {"Authorization": f"Bearer {TOKEN}",
               "Content-Type": "application/json",
               "Accept": "application/json"}

    attempts = []
    if media:
        attempts.append(("image (base64)", f"{WHAPI_BASE}/messages/image",
                         {"to": CHANNEL, "media": media, "caption": caption}))
    if item.get("image"):
        attempts.append(("image (url)", f"{WHAPI_BASE}/messages/image",
                         {"to": CHANNEL, "media": item["image"], "caption": caption}))
    attempts.append(("text", f"{WHAPI_BASE}/messages/text",
                     {"to": CHANNEL, "body": caption}))

    for label, endpoint, payload in attempts:
        try:
            r = SESSION.post(endpoint, headers=headers, json=payload, timeout=120)
        except requests.RequestException as e:
            log(f"  ! {label} send failed: {e}")
            continue
        if r.status_code in (200, 201):
            log(f"  -> posted [{label}]: {item['title']}")
            return "ok"

        body = r.text[:250]
        log(f"  ! {label} HTTP {r.status_code}: {body}")

        # The channel id itself is being rejected — falling through to the
        # text attempt cannot help, they all address the same channel.
        if r.status_code == 404 and "channel not found" in body.lower():
            return "channel"
        # auth/permission problems will not improve on the next attempt
        if r.status_code in (401, 403):
            return "fail"
    return "fail"


def whapi_post(item):
    """Send an image+caption post to the WhatsApp Channel. Returns True on success."""
    caption = build_caption(item)
    media = fetch_media(item.get("image"))

    outcome = _send_once(item, caption, media)
    if outcome == "ok":
        return True
    if outcome == "channel" and refresh_channel():
        # a different id came back, so this is worth exactly one more try
        return _send_once(item, caption, media) == "ok"
    return False


class BaileysSender:
    """One Node process, one WhatsApp connection, for the whole run.

    Reconnecting to WhatsApp for every individual post is exactly what an
    abusive client looks like, so the helper is started once, kept open while
    the run posts, and closed at the end — which is also when it saves the
    rotated credentials back.
    """

    def __init__(self):
        self.proc = None
        self.channel = None
        self.broken = None      # set once, so a dead sender is not retried
        self._started = False

    def _read(self):
        """Read one JSON reply, or None if the helper has gone away."""
        try:
            line = self.proc.stdout.readline()
        except (OSError, ValueError):
            return None
        if not line:
            return None
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            log(f"  ! baileys said something unparseable: {line.strip()[:200]}")
            return None

    def start(self):
        if self._started:
            return self.broken is None
        self._started = True

        script = ROOT / "baileys" / "send.js"
        if not script.exists():
            self.broken = "baileys/send.js is missing"
        elif not (ROOT / "baileys" / "node_modules").exists():
            self.broken = "baileys dependencies are not installed (npm ci)"
        if self.broken:
            log(f"  ! baileys: {self.broken}")
            return False

        try:
            self.proc = subprocess.Popen(
                ["node", str(script)],
                cwd=str(ROOT / "baileys"),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                text=True, bufsize=1,
                env={**os.environ,
                     "WA_SESSION_KEY": WA_SESSION_KEY,
                     "WA_CHANNEL_INVITE": RAW_CHANNEL})
        except OSError as e:
            self.broken = f"could not start node: {e}"
            log(f"  ! baileys: {self.broken}")
            return False

        hello = self._read()
        if not hello or not hello.get("ready"):
            self.broken = (hello or {}).get("error", "sender exited before it was ready")
            log(f"  ! baileys: {self.broken}")
            self.close()
            return False

        self.channel = hello.get("channel")
        log(f"  baileys ready, posting to {self.channel}")
        return True

    def post(self, item, caption, media):
        if not self.start():
            return False
        try:
            self.proc.stdin.write(json.dumps({"caption": caption, "media": media}) + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            self.broken = f"sender went away mid-run: {e}"
            log(f"  ! baileys: {self.broken}")
            return False

        res = self._read()
        if res is None:
            self.broken = "sender stopped responding"
            log(f"  ! baileys: {self.broken}")
            return False
        if res.get("ok"):
            log(f"  -> posted [{res.get('kind', 'sent')}]: {item['title']}")
            return True
        log(f"  ! baileys send failed: {res.get('error', 'unknown')}")
        return False

    def close(self):
        """Shut the helper down so it saves the session on its way out."""
        if not self.proc:
            return
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            # It only needs long enough to write the encrypted session back.
            self.proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            log("  ! baileys did not exit; killing it")
            self.proc.kill()
        self.proc = None


BAILEYS = BaileysSender()


def send_post(item):
    """Post one item to the channel using whichever transport is configured."""
    if SENDER == "baileys":
        return BAILEYS.post(item, build_caption(item), fetch_media(item.get("image")))
    return whapi_post(item)


def fetch_media(url):
    """Download the thumbnail and return a data URI.

    slideegg serves these PNGs as 'binary/octet-stream', which media-by-URL
    uploads tend to reject, so sending the bytes ourselves is more reliable.
    """
    if not url:
        return None
    r = get(url)
    if not r:
        return None
    blob = r.content
    if len(blob) < 1024:
        log(f"  ! image too small ({len(blob)}b), skipping media")
        return None
    if len(blob) > 15 * 1024 * 1024:
        log(f"  ! image too large ({len(blob) // 1024}kb), skipping media")
        return None

    # sniff the real type rather than trusting the server's header
    if blob[:8] == b"\x89PNG\r\n\x1a\n":
        mime = "image/png"
    elif blob[:3] == b"\xff\xd8\xff":
        mime = "image/jpeg"
    elif blob[:4] == b"RIFF" and blob[8:12] == b"WEBP":
        mime = "image/webp"
    else:
        log("  ! unrecognised image format, skipping media")
        return None

    return f"data:{mime};base64," + base64.b64encode(blob).decode()


def whapi_list_channels():
    """Helper: print channel ids for this Whapi number. Run with --channels."""
    if not TOKEN:
        print("Set WHAPI_TOKEN first.")
        return
    r = SESSION.get(f"{WHAPI_BASE}/newsletters", headers=whapi_auth(), timeout=60)
    print(f"HTTP {r.status_code}")
    try:
        data = r.json()
    except ValueError:
        print(r.text[:2000]); return
    items = data.get("newsletters") or data.get("items") or data
    print(json.dumps(items, indent=2)[:4000])


# ---------------------------------------------------------------- state

def load_seen():
    if SEEN_FILE.exists():
        try:
            d = json.loads(SEEN_FILE.read_text())
            return set(d.get("urls", []))
        except (ValueError, OSError):
            log("! seen.json unreadable, starting fresh")
    return set()


HEALTH_FILE = STATE_DIR / "health.json"

# The permanent record of everything ever published to the channel. This is
# the "sheet" — GitHub renders it as a table, and it imports straight into
# Excel or Google Sheets. Columns are split out (year / month / week) so that
# pivoting by period needs no formulas.
POSTS_CSV = STATE_DIR / "posts.csv"
CSV_HEADER = ["date", "time_ist", "year", "month", "month_name", "week",
              "day_name", "type", "title", "url", "image_url"]


def record_post(now, item):
    """Append one delivered post to the CSV record."""
    import csv as _csv
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    new_file = not POSTS_CSV.exists() or POSTS_CSV.stat().st_size == 0
    try:
        with POSTS_CSV.open("a", newline="", encoding="utf-8") as fh:
            w = _csv.writer(fh)
            if new_file:
                w.writerow(CSV_HEADER)
            w.writerow([
                now.strftime("%Y-%m-%d"),
                now.strftime("%H:%M"),
                now.year,
                now.month,
                now.strftime("%B"),
                f"{now.isocalendar()[0]}-W{now.isocalendar()[1]:02d}",
                now.strftime("%A"),
                item.get("kind", "template"),
                item.get("title", ""),
                item.get("url", ""),
                item.get("image", "") or "",
            ])
    except OSError as e:
        # never let bookkeeping break posting
        log(f"  ! could not write posts.csv: {e}")

# If nothing new has been detected for this long, something is wrong even
# though every run reports success. Both silent failures this system has had
# — a stale cached page, and a run that quietly went into preview mode —
# looked exactly like "no new content", so the only reliable alarm is time.
ALERT_AFTER_HOURS = int(os.environ.get("ALERT_AFTER_HOURS", "24"))


def load_health():
    if HEALTH_FILE.exists():
        try:
            return json.loads(HEALTH_FILE.read_text())
        except (ValueError, OSError):
            pass
    return {}


def update_health(now, found_new):
    h = load_health()
    if found_new:
        h["last_new_item_at"] = now.isoformat()
    h.setdefault("last_new_item_at", now.isoformat())
    h["last_checked_at"] = now.isoformat()
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    HEALTH_FILE.write_text(json.dumps(h, indent=1))
    return h


def hours_since_new(now, health):
    ts = health.get("last_new_item_at")
    if not ts:
        return 0.0
    try:
        then = dt.datetime.fromisoformat(ts)
    except ValueError:
        return 0.0
    if then.tzinfo is None:
        then = then.replace(tzinfo=IST)
    return (now - then).total_seconds() / 3600.0


def load_daily(today):
    """How many posts have already gone out today (IST)."""
    if DAILY_FILE.exists():
        try:
            d = json.loads(DAILY_FILE.read_text())
            if d.get("date") == str(today):
                return int(d.get("posted", 0))
        except (ValueError, OSError, TypeError):
            log("! daily.json unreadable, treating today as empty")
    return 0


def save_daily(today, posted):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    DAILY_FILE.write_text(json.dumps(
        {"date": str(today), "posted": posted, "limit": DAILY_LIMIT}, indent=1))


def save_seen(seen):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    urls = sorted(seen)
    # keep the file from growing forever
    if len(urls) > 20000:
        urls = urls[-20000:]
    SEEN_FILE.write_text(json.dumps(
        {"updated": dt.datetime.now(IST).isoformat(), "count": len(urls), "urls": urls},
        indent=1))


# ---------------------------------------------------------------- main

def main():
    global CHANNEL

    if "--channels" in sys.argv:
        whapi_list_channels()
        return 0

    if "--resolve" in sys.argv:
        if not TOKEN:
            print("Set WHAPI_TOKEN first."); return 1
        cid = resolve_channel(CHANNEL)
        print(cid or "could not resolve")
        return 0 if cid else 1

    if not DRY_RUN:
        CHANNEL = resolve_channel(CHANNEL)
        if not CHANNEL:
            log("! no usable channel id — aborting before posting")
            return 1

    now = dt.datetime.now(IST)
    if DRY_RUN:
        why = ("WHAPI_TOKEN is empty or missing" if not TOKEN
               else "DRY_RUN=1 was requested")
        reason = f" (dry because: {why})"
    else:
        reason = ""
    log(f"run start | {now:%Y-%m-%d %H:%M} IST | sources={','.join(sorted(SOURCES))} "
        f"| dry_run={DRY_RUN}{reason}")
    log(f"env check | WHAPI_TOKEN {'set, ' + str(len(TOKEN)) + ' chars' if TOKEN else 'NOT SET'} "
        f"| DRY_RUN env={os.environ.get('DRY_RUN', '(unset)')!r}")

    if not (ACTIVE_FROM <= now.hour < ACTIVE_TO) and "--force" not in sys.argv:
        log(f"quiet hours (posting runs {ACTIVE_FROM}:00-{ACTIVE_TO}:00 IST) — "
            f"nothing sent; anything new is picked up at the next allowed run")
        return 0

    seen = load_seen()
    first_run = not seen
    log(f"seen.json has {len(seen)} urls" + (" (FIRST RUN)" if first_run else ""))

    # ---- templates: the listing page is live and strictly newest-first
    listing = []
    if "templates" in SOURCES:
        log(f"scanning {SCAN_PAGES} template listing page(s)")
        listing = scan_latest(SCAN_PAGES)
        log(f"  {len(listing)} templates")
        if not listing:
            log("! template listing empty — site layout may have changed. Aborting.")
            LOG_FILE.write_text(json.dumps(
                {"run": now.isoformat(), "mode": "error",
                 "error": "empty template listing"}, indent=1))
            return 1

    # ---- blog: index order, publish date checked when we open each post
    blog_urls = []
    if "blog" in SOURCES:
        log("scanning blog index")
        blog_urls = scan_blog()

    known_urls = {it["url"] for it in listing} | set(blog_urls)

    if first_run:
        # Don't dump a backlog into the channel on the very first run.
        log("first run: recording current state as baseline, not posting")
        save_seen(known_urls)
        LOG_FILE.write_text(json.dumps(
            {"run": now.isoformat(), "mode": "baseline",
             "recorded": len(known_urls), "posted": 0}, indent=1))
        return 0

    today = now.date()
    posted_today = load_daily(today)
    room = max(0, DAILY_LIMIT - posted_today)
    log(f"daily budget: {posted_today}/{DAILY_LIMIT} used, {room} left")

    # newest-first: if the day's quota is tight, the freshest items win
    fresh = [it for it in listing if it["url"] not in seen]
    fresh_blog = [u for u in blog_urls if u not in seen]
    log(f"{len(fresh)} new template(s), {len(fresh_blog)} unseen blog url(s)")

    if room == 0:
        log("daily limit already reached — retiring today's leftovers so the "
            "channel does not fall behind, and stopping")
        seen.update(it["url"] for it in fresh)
        seen.update(fresh_blog)
        if not DRY_RUN:
            save_seen(seen)
        LOG_FILE.write_text(json.dumps(
            {"run": now.isoformat(), "mode": "dry" if DRY_RUN else "live",
             "daily_posted": posted_today, "daily_limit": DAILY_LIMIT,
             "posted": 0, "retired_over_limit": len(fresh) + len(fresh_blog),
             "diagnostics": DIAG}, indent=1))
        # today's quota is spent, which is a healthy state — but the watchdog
        # still needs to see whether the site is producing anything at all
        return watchdog(now, found_new=bool(fresh or fresh_blog), failed=0)

    budget = min(MAX_POSTS, room)

    # Blog posts need their publish date checked before they count as new.
    blog_items, blog_skipped = [], []
    for u in fresh_blog:
        if len(blog_items) >= budget:
            break
        item = load_blog_post(u)
        if item:
            blog_items.append(item)
        else:
            blog_skipped.append(u)

    # blog first (it is rarer and higher value), then the newest templates
    queue = blog_items + fresh
    to_post = queue[:budget]
    overflow = queue[budget:]

    # If the day's quota runs out here, retire the overflow instead of queuing
    # it; otherwise it just waits for the next run 15 minutes later.
    retired = 0
    if len(to_post) >= room and overflow:
        retired = len(overflow)
        log(f"daily limit reached this run — retiring {retired} item(s) "
            f"rather than letting a backlog build")
        seen.update(it["url"] for it in overflow)
    elif overflow:
        log(f"{len(overflow)} item(s) will go out on the next run")

    # An old post that resurfaced is not worth re-checking every 15 minutes.
    seen.update(blog_skipped)

    posted, failed = [], []
    for i, item in enumerate(to_post, 1):
        log(f"[{i}/{len(to_post)}] {item['kind']}: {item['url']}")
        if item["kind"] == "template":
            item = enrich(item)
        if DRY_RUN:
            print("-" * 60)
            print(build_caption(item))
            print(f"[image] {item.get('image')}")
            print("-" * 60)
            posted.append(item["url"])
        else:
            if send_post(item):
                posted.append(item["url"])
                seen.add(item["url"])
                record_post(dt.datetime.now(IST), item)
            else:
                failed.append(item["url"])
            # The Baileys helper paces itself between sends; only the HTTP
            # transport needs the wait here.
            if SENDER != "baileys":
                time.sleep(8)

    # Closing the helper is what saves the rotated WhatsApp credentials, so it
    # has to happen even when nothing was posted.
    if SENDER == "baileys":
        BAILEYS.close()

    if not DRY_RUN:
        save_seen(seen)
        save_daily(today, posted_today + len(posted))

    LOG_FILE.write_text(json.dumps(
        {"run": now.isoformat(), "mode": "dry" if DRY_RUN else "live",
         "why_dry": (None if not DRY_RUN else
                     (CREDENTIAL_PROBLEM or "DRY_RUN=1 requested")),
         "sender": SENDER,
         "sender_error": (BAILEYS.broken if SENDER == "baileys" else None),
         "token_chars": len(TOKEN),
         "daily_posted": posted_today + len(posted), "daily_limit": DAILY_LIMIT,
         "retired_over_limit": retired,
         "diagnostics": DIAG,
         "templates_scanned": len(listing), "blog_scanned": len(blog_urls),
         "new_templates": len(fresh), "new_blog": len(blog_items),
         "blog_skipped_as_refresh": len(blog_skipped),
         "posted": len(posted), "failed": len(failed),
         "failed_urls": failed[:20]}, indent=1))

    log(f"done | posted={len(posted)} failed={len(failed)}")

    return watchdog(now, found_new=bool(fresh or blog_items), failed=len(failed))


def watchdog(now, found_new, failed):
    """Fail the run when the channel has gone quiet for too long.

    A failed run turns the GitHub Actions run red and emails the repository
    owner, which is the whole point: every silent failure so far reported
    success. Posting has already happened by the time this runs, so raising
    the alarm can never stop a good run from delivering.
    """
    health = update_health(now, found_new) if not DRY_RUN else load_health()
    quiet = hours_since_new(now, health)

    if failed:
        log(f"! {failed} post(s) failed this run")
        return 1

    if quiet > ALERT_AFTER_HOURS:
        log("=" * 60)
        log(f"! WATCHDOG: nothing new detected for {quiet:.1f} hours "
            f"(limit {ALERT_AFTER_HOURS}h).")
        log("! Every run has reported success, so check these in order:")
        log("!  1. Open slideegg.com/latest-powerpoint-templates in a browser")
        log("!     and compare the newest titles with diagnostics.page1_top3")
        log("!     in state/last_run.json. Different = the runner is being")
        log("!     served a stale cached page.")
        log("!  2. Check mode is 'live' and why_dry is null.")
        log("!  3. Check the Whapi number is still linked and still a channel admin.")
        log("=" * 60)
        return 1

    log(f"watchdog ok | {quiet:.1f}h since the last new item "
        f"(alerts after {ALERT_AFTER_HOURS}h)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
