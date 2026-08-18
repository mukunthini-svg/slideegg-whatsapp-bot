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
MAX_POSTS = int(os.environ.get("MAX_POSTS", "5"))
SCAN_PAGES = max(1, int(os.environ.get("SCAN_PAGES", "3")))
DRY_RUN = os.environ.get("DRY_RUN", "").strip() == "1" or not TOKEN

# Which sources to watch: "templates", "blog", or "templates,blog"
SOURCES = {s.strip().lower() for s in
           os.environ.get("SOURCES", "templates,blog").split(",") if s.strip()}

# A blog post only counts as new if it was PUBLISHED this recently. SlideEgg
# continually re-edits old posts for SEO, which bumps sitemap lastmod and can
# push a 2024 article back to the top of the index — without this guard those
# refreshes would be announced as new.
MAX_AGE_DAYS = int(os.environ.get("MAX_AGE_DAYS", "14"))

# Quiet hours (IST, 24h). Outside this window the run exits without posting;
# anything published meanwhile is picked up at the next allowed run.
ACTIVE_FROM = int(os.environ.get("ACTIVE_FROM", "8"))
ACTIVE_TO = int(os.environ.get("ACTIVE_TO", "21"))

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


def scan_latest(pages):
    """Scan the newest-first listing pages. Returns items in newest-first order."""
    out, seen_urls = [], set()
    for n in range(1, pages + 1):
        url = LATEST if n == 1 else f"{LATEST}?page={n}"
        r = get(url)
        if not r:
            break
        found = parse_listing(r.text)
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
    if not m:
        log(f"! cannot read a channel invite code from {raw!r}")
        return None
    code = m.group(1)

    try:
        r = SESSION.get(f"{WHAPI_BASE}/newsletters/{code}",
                        headers=whapi_auth(), timeout=60)
    except requests.RequestException as e:
        log(f"! channel lookup failed: {e}")
        return None

    if r.status_code != 200:
        log(f"! channel lookup HTTP {r.status_code}: {r.text[:300]}")
        return None

    try:
        data = r.json()
    except ValueError:
        log("! channel lookup returned non-JSON")
        return None

    node = data.get("newsletter") or data
    cid = node.get("id") or node.get("jid") or node.get("chat_id")
    if not cid:
        log(f"! no channel id in response: {json.dumps(data)[:300]}")
        return None
    if not str(cid).endswith("@newsletter"):
        cid = f"{cid}@newsletter"

    name = (node.get("name") or node.get("subject")
            or (node.get("thread_metadata") or {}).get("name") or "?")
    log(f"  resolved channel: {name} -> {cid}")

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"invite": raw, "id": cid, "name": name}, indent=1))
    return cid


def whapi_post(item):
    """Send an image+caption post to the WhatsApp Channel. Returns True on success."""
    caption = build_caption(item)
    headers = {"Authorization": f"Bearer {TOKEN}",
               "Content-Type": "application/json",
               "Accept": "application/json"}

    media = fetch_media(item.get("image"))

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
            return True
        log(f"  ! {label} HTTP {r.status_code}: {r.text[:250]}")
        # auth/permission problems will not improve on the next attempt
        if r.status_code in (401, 403):
            return False
    return False


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
    log(f"run start | {now:%Y-%m-%d %H:%M} IST | sources={','.join(sorted(SOURCES))} "
        f"| dry_run={DRY_RUN}")

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

    # oldest-first within each source, so the channel reads chronologically
    fresh = [it for it in reversed(listing) if it["url"] not in seen]
    fresh_blog = [u for u in reversed(blog_urls) if u not in seen]
    log(f"{len(fresh)} new template(s), {len(fresh_blog)} unseen blog url(s)")

    # Blog posts need their publish date checked before they count as new.
    blog_items, blog_skipped = [], []
    for u in fresh_blog:
        if len(fresh) + len(blog_items) >= MAX_POSTS:
            break
        item = load_blog_post(u)
        if item:
            blog_items.append(item)
        else:
            blog_skipped.append(u)

    queue = blog_items + fresh
    to_post = queue[:MAX_POSTS]
    if len(queue) > MAX_POSTS:
        log(f"capping at {MAX_POSTS}; {len(queue) - MAX_POSTS} will go out next run")

    # An old post that resurfaced is not worth re-checking every 30 minutes.
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
            if whapi_post(item):
                posted.append(item["url"])
                seen.add(item["url"])
            else:
                failed.append(item["url"])
            time.sleep(8)  # gentle pacing between channel posts

    if not DRY_RUN:
        save_seen(seen)

    LOG_FILE.write_text(json.dumps(
        {"run": now.isoformat(), "mode": "dry" if DRY_RUN else "live",
         "templates_scanned": len(listing), "blog_scanned": len(blog_urls),
         "new_templates": len(fresh), "new_blog": len(blog_items),
         "blog_skipped_as_refresh": len(blog_skipped),
         "posted": len(posted), "failed": len(failed),
         "failed_urls": failed[:20]}, indent=1))

    log(f"done | posted={len(posted)} failed={len(failed)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
