#!/usr/bin/env python3
"""Offline test: fakes the network so the whole pipeline can be verified
without reaching slideegg.com or whapi.cloud.

Fixtures mirror the real page structure verified in a live browser on
2026-08-18 (JSON-LD ImageObject array, whitespace-padded values, the
/image/webpv2/... -> /image/catalog/....png thumbnail relationship)."""
import base64
import json
import pathlib
import os
import sys

os.environ["DRY_RUN"] = "1"
os.environ["MAX_POSTS"] = "5"
os.environ["SCAN_PAGES"] = "2"
os.environ["ACTIVE_FROM"] = "0"
os.environ["ACTIVE_TO"] = "24"
os.environ["SOURCES"] = "templates"
os.environ.pop("WHAPI_TOKEN", None)
os.environ.pop("WHAPI_CHANNEL", None)

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import slideegg_daily as S

failures = []


def check(name, cond, extra=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}{'   -> ' + str(extra) if not cond and extra else ''}")
    if not cond:
        failures.append(name)


# ------------------------------------------------------------------ fixtures

def ld_item(name, slug, img_id):
    return {
        "@context": "https://schema.org/",
        "@type": "ImageObject",
        "name": name,
        "contentUrl": f"https://www.slideegg.com/image/webpv2/670/{img_id}-{slug}-670.webp",
        "creator": {"@type": "Organization", "name": "SlideEgg"},
        "license": "https://creativecommons.org/licenses/by/4.0/",
        "acquireLicensePage": f"https://www.slideegg.com/{slug}",
    }


def listing_page(items, extras=""):
    site_ld = {"@context": "https://schema.org", "@type": "WebSite", "name": "SlideEgg"}
    return f"""<html><head>
<script type="application/ld+json">{json.dumps(site_ld)}</script>
<script type="application/ld+json">{json.dumps(items)}</script>
{extras}</head><body></body></html>"""


PAGE1 = listing_page([
    ld_item("Data Storytelling PowerPoint Presentation And Google Slides", "data-storytelling", "102291"),
    ld_item("Cultural Heritage Preservation PowerPoint And Google Slides", "cultural-heritage-preservation", "102292"),
    ld_item("Tech Branding Kit PowerPoint Presentation And Google Slides", "tech-branding-kit", "502565"),
    # noise that must be filtered out
    {"@type": "ImageObject", "name": "Blog post", "contentUrl": "x.webp",
     "acquireLicensePage": "https://www.slideegg.com/blog/how-to-present"},
    {"@type": "ImageObject", "name": "Category", "contentUrl": "y.webp",
     "acquireLicensePage": "https://www.slideegg.com/category/business"},
    {"@type": "ImageObject", "name": "Interactive", "contentUrl": "z.webp",
     "acquireLicensePage": "https://www.slideegg.com/interactive/multiple-choice"},
    {"@type": "ImageObject", "name": "Offsite", "contentUrl": "w.webp",
     "acquireLicensePage": "https://evil.example.com/steal"},
])

PAGE2 = listing_page([
    ld_item("Key Financial Assumptions PowerPoint And Google Slides", "key-financial-assumptions", "66661"),
    ld_item("Already Posted Template", "already-posted-template", "66660"),
])

PAGE3 = listing_page([ld_item("Should Not Be Scanned", "page-three-item", "55555")])

TEMPLATE_PAGE = """<html><head>
<title>Data Storytelling PPT And Google Slides | SlideEgg Free Templates</title>
<meta property="og:title" content="Data Storytelling PowerPoint Presentation And Google Slides" />
<meta property="og:image" content="https://www.slideegg.com/image/catalog/102291-data-storytelling.png" />
<meta property="og:description" content="Turn raw numbers into a story your audience remembers.   Editable in PowerPoint, Google Slides &amp; Canva." />
</head><body></body></html>"""

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 4000
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 4000


class FakeResp:
    def __init__(self, body, status=200, url=""):
        self.status_code, self.url = status, url
        self._b = body.encode() if isinstance(body, str) else body

    @property
    def content(self):
        return self._b

    @property
    def text(self):
        return self._b.decode()

    def json(self):
        return json.loads(self._b.decode())


ROUTES = {
    S.LATEST: PAGE1,
    f"{S.LATEST}?page=2": PAGE2,
    f"{S.LATEST}?page=3": PAGE3,
    "https://www.slideegg.com/data-storytelling": TEMPLATE_PAGE,
    "https://www.slideegg.com/image/catalog/102291-data-storytelling.png": PNG,
    "https://www.slideegg.com/image/catalog/66661-key-financial-assumptions.png": JPG,
}

fetched = []
S.get = lambda url, **kw: (fetched.append(url), ROUTES.get(url))[1] and FakeResp(ROUTES[url], url=url)

# ------------------------------------------------------------------ run

S.STATE_DIR.mkdir(parents=True, exist_ok=True)
S.SEEN_FILE.write_text(json.dumps(
    {"urls": ["https://www.slideegg.com/already-posted-template"]}))

print("=" * 70)
rc = S.main()
print("=" * 70)
print("\nLISTING / FILTERING")

run = json.loads(S.LOG_FILE.read_text())
check("exit code 0", rc == 0)
check("scanned both pages", f"{S.LATEST}?page=2" in fetched)
check("SCAN_PAGES honoured (page 3 untouched)", f"{S.LATEST}?page=3" not in fetched)
check("5 real templates found, noise dropped", run["templates_scanned"] == 5, run)
check("already-seen template excluded", run["new_templates"] == 4, run)
check("blog url filtered", not any("blog" in u for u in fetched))
check("category url filtered", not any("category" in u for u in fetched))
check("interactive url filtered", not any("interactive" in u for u in fetched))
check("offsite url filtered", not any("evil.example.com" in u for u in fetched))

items = S.parse_listing(PAGE1)
check("website JSON-LD block ignored", len(items) == 3, len(items))
check("thumbnail -> catalog png derived",
      items[0]["image"] == "https://www.slideegg.com/image/catalog/102291-data-storytelling.png",
      items[0]["image"])
check("catalog_png strips size suffix",
      S.catalog_png("https://www.slideegg.com/image/webpv2/670/502565-tech-branding-kit-670.webp")
      == "https://www.slideegg.com/image/catalog/502565-tech-branding-kit.png")
check("catalog_png handles jpg thumbs",
      S.catalog_png("/image/webpv2/670/1-a-b-300.jpg")
      == "https://www.slideegg.com/image/catalog/1-a-b.png")
check("catalog_png tolerates None", S.catalog_png(None) is None)
check("whitespace-padded loc tolerated",
      S.is_template_url("  https://www.slideegg.com/data-storytelling  ".strip()))
check("malformed JSON-LD does not crash",
      S.parse_listing('<script type="application/ld+json">{oops</script>') == [])
check("page with no JSON-LD returns empty", S.parse_listing("<html></html>") == [])

print("\nPOST ORDER / ENRICHMENT")
check("posted oldest-first (page-2 item first)",
      fetched.index("https://www.slideegg.com/key-financial-assumptions")
      < fetched.index("https://www.slideegg.com/data-storytelling"))

item = S.enrich({"url": "https://www.slideegg.com/data-storytelling",
                 "title": "listing title", "image": None})
check("og:title overrides listing title",
      item["title"] == "Data Storytelling PowerPoint Presentation And Google Slides", item["title"])
check("SEO suffix stripped", "SlideEgg Free Templates" not in item["title"])
check("og:image picked up", item["image"].endswith("102291-data-storytelling.png"))
check("html entity decoded", "&amp;" not in item["desc"])

missing = S.enrich({"url": "https://www.slideegg.com/no-such-page",
                    "title": "kept", "image": "img.png"})
check("enrich survives a dead page", missing["title"] == "kept" and missing["image"] == "img.png")

cap = S.build_caption(item)
check("caption has title", item["title"] in cap)
check("caption has url", item["url"] in cap)
check("caption has hashtags", "#SlideEgg" in cap)

long_desc = S.build_caption({"url": "u", "title": "T", "desc": "word " * 200})
check("long description truncated", len(long_desc) < 400, len(long_desc))

print("\nMEDIA")
media = S.fetch_media("https://www.slideegg.com/image/catalog/102291-data-storytelling.png")
check("png sniffed despite octet-stream header", media.startswith("data:image/png;base64,"), media[:40])
check("base64 payload round-trips",
      base64.b64decode(media.split(",", 1)[1])[:8] == b"\x89PNG\r\n\x1a\n")
check("jpeg sniffed",
      S.fetch_media("https://www.slideegg.com/image/catalog/66661-key-financial-assumptions.png")
      .startswith("data:image/jpeg;base64,"))
ROUTES["tiny"] = b"xx"
check("tiny file rejected", S.fetch_media("tiny") is None)
ROUTES["notimg"] = b"<html>404 page</html>" + b"\x00" * 4000
check("html masquerading as image rejected", S.fetch_media("notimg") is None)
check("no url -> no media", S.fetch_media(None) is None)


print("\nCHANNEL RESOLUTION")


class FakeSession:
    def __init__(self, status=200, body=None):
        self.status, self.body, self.calls = status, body or {}, []

    def get(self, url, **kw):
        self.calls.append(url)
        return FakeResp(json.dumps(self.body), status=self.status, url=url)


chan_cache = S.STATE_DIR / "channel.json"
chan_cache.unlink(missing_ok=True)
real_session, real_token = S.SESSION, S.TOKEN
S.TOKEN = "fake-token"

S.SESSION = fs = FakeSession(body={"id": "120363301234567890@newsletter", "name": "SlideEgg Presentation Hub"})
got = S.resolve_channel("https://whatsapp.com/channel/0029Vb7WIkq35fLwXKie5521")
check("invite link -> channel id", got == "120363301234567890@newsletter", got)
check("hit /newsletters/<code>", fs.calls[0].endswith("/newsletters/0029Vb7WIkq35fLwXKie5521"), fs.calls)

S.SESSION = fs2 = FakeSession()
check("second call cached, no API hit",
      S.resolve_channel("https://whatsapp.com/channel/0029Vb7WIkq35fLwXKie5521")
      == "120363301234567890@newsletter" and not fs2.calls, fs2.calls)

S.SESSION = fs3 = FakeSession()
check("resolved id passes through", S.resolve_channel("1203633099@newsletter") == "1203633099@newsletter" and not fs3.calls)

chan_cache.unlink(missing_ok=True)
S.SESSION = FakeSession(body={"newsletter": {"id": "120363300000000001", "subject": "X"}})
check("nested payload + missing suffix handled",
      S.resolve_channel("0029Vb7WIkq35fLwXKie5599") == "120363300000000001@newsletter")

chan_cache.unlink(missing_ok=True)
S.SESSION = FakeSession(status=404, body={"error": "not found"})
check("404 returns None", S.resolve_channel("0029Vb7WIkq35fLwXKie5588") is None)

chan_cache.unlink(missing_ok=True)
check("garbage invite rejected", S.resolve_channel("nope") is None)
check("default channel = SlideEgg Presentation Hub invite",
      S.DEFAULT_CHANNEL == "0029Vb7WIkq35fLwXKie5521")

S.SESSION, S.TOKEN = real_session, real_token

print("\nSAFETY")
check("dry run did not mutate seen.json",
      len(json.loads(S.SEEN_FILE.read_text())["urls"]) == 1)

S.SEEN_FILE.unlink()
fetched.clear()
S.main()
run2 = json.loads(S.LOG_FILE.read_text())
check("first run posts nothing", run2["mode"] == "baseline" and run2["posted"] == 0, run2)
check("first run records baseline", json.loads(S.SEEN_FILE.read_text())["count"] == 5)
check("first run did not fetch template pages",
      not any(u.endswith("/data-storytelling") for u in fetched))

# empty listing must abort rather than treat everything as gone
S.SEEN_FILE.write_text(json.dumps({"urls": ["https://www.slideegg.com/x"]}))
ROUTES[S.LATEST] = "<html>site redesigned</html>"
rc3 = S.main()
check("empty listing aborts with error", rc3 == 1
      and json.loads(S.LOG_FILE.read_text())["mode"] == "error")
check("empty listing left seen.json intact",
      json.loads(S.SEEN_FILE.read_text())["urls"] == ["https://www.slideegg.com/x"])

for f in (S.SEEN_FILE, S.LOG_FILE, chan_cache):
    f.unlink(missing_ok=True)


# ================================================================== BLOG
print("\nBLOG")

import datetime as _dt
TODAY = _dt.datetime.now(S.IST).date()
RECENT = (TODAY - _dt.timedelta(days=2)).isoformat()
ANCIENT = (TODAY - _dt.timedelta(days=400)).isoformat()

BLOG_INDEX = """<html><body>
<a href="https://www.slideegg.com/blog/presentation-tips/brand-new-post/">new</a>
<a href="https://www.slideegg.com/blog/presentation-collections/refreshed-old-post/">old</a>
<a href="https://www.slideegg.com/blog/presentation-tips/">category page</a>
<a href="https://www.slideegg.com/blog/">index</a>
<a href="https://www.slideegg.com/blog/presentation-tips/brand-new-post/">dupe</a>
</body></html>"""

def blog_post(title, pub, desc="A useful guide."):
    return f'''<html><head>
<title>{title} | SlideEgg Blog</title>
<meta property="og:title" content="{title}" />
<meta property="og:description" content="{desc}" />
<meta property="og:image" content="https://www.slideegg.com/blog/wp-content/uploads/x.jpg" />
<script type="application/ld+json">{{"@type":"BlogPosting","datePublished":"{pub}T09:00:00+00:00"}}</script>
</head><body></body></html>'''

ROUTES[S.BLOG] = BLOG_INDEX
ROUTES["https://www.slideegg.com/blog/presentation-tips/brand-new-post/"] = blog_post("Brand New Post", RECENT)
ROUTES["https://www.slideegg.com/blog/presentation-collections/refreshed-old-post/"] = blog_post("Refreshed Old Post", ANCIENT)

urls = S.scan_blog()
check("blog index: only real posts", len(urls) == 2, urls)
check("blog category page excluded", not any(u.endswith("/presentation-tips/") for u in urls))
check("blog index page excluded", "https://www.slideegg.com/blog/" not in urls)
check("blog duplicate link collapsed", len(set(urls)) == len(urls))

new_post = S.load_blog_post("https://www.slideegg.com/blog/presentation-tips/brand-new-post/")
check("recent blog post accepted", new_post is not None)
check("blog kind tagged", new_post["kind"] == "blog")
check("blog SEO title suffix stripped", new_post["title"] == "Brand New Post", new_post["title"])
check("blog og:image picked up", new_post["image"].endswith("x.jpg"))

old_post = S.load_blog_post("https://www.slideegg.com/blog/presentation-collections/refreshed-old-post/")
check("SEO-refreshed old post REJECTED", old_post is None, "this is the whole point of MAX_AGE_DAYS")

check("parse_iso_date with offset", S.parse_iso_date("2026-08-18T09:00:00+00:00") == _dt.date(2026, 8, 18))
check("parse_iso_date bare", S.parse_iso_date("2026-08-18") == _dt.date(2026, 8, 18))
check("parse_iso_date junk", S.parse_iso_date("not a date") is None)
check("parse_iso_date None", S.parse_iso_date(None) is None)

blog_cap = S.build_caption(new_post)
check("blog caption uses blog header", "SlideEgg Blog" in blog_cap, blog_cap[:40])
check("blog caption has no template perks", "Fully editable" not in blog_cap)
check("blog caption has read CTA", "Read it here" in blog_cap)
tmpl_cap = S.build_caption({"url": "u", "title": "T", "desc": "d", "kind": "template"})
check("template caption differs from blog", "New Template on SlideEgg" in tmpl_cap)
check("template caption keeps perks", "Fully editable" in tmpl_cap)

# end-to-end with both sources
print("\nBOTH SOURCES END-TO-END")
S.SOURCES = {"templates", "blog"}
ROUTES[S.LATEST] = PAGE1
S.SEEN_FILE.write_text(json.dumps({"urls": [
    "https://www.slideegg.com/already-posted-template",
    "https://www.slideegg.com/data-storytelling",
    "https://www.slideegg.com/cultural-heritage-preservation",
    "https://www.slideegg.com/tech-branding-kit",
    "https://www.slideegg.com/key-financial-assumptions",
]}))
fetched.clear()
S.main()
both = json.loads(S.LOG_FILE.read_text())
check("both sources scanned", both["templates_scanned"] == 5 and both["blog_scanned"] == 2, both)
check("only the genuinely new blog post posted", both["posted"] == 1, both)
check("refreshed post counted as skipped", both["blog_skipped_as_refresh"] == 1, both)

# quiet hours
print("\nQUIET HOURS")
S.ACTIVE_FROM, S.ACTIVE_TO = 8, 21
now_h = _dt.datetime.now(S.IST).hour
S.ACTIVE_FROM, S.ACTIVE_TO = (now_h + 1) % 24, (now_h + 2) % 24
S.LOG_FILE.unlink(missing_ok=True)
fetched.clear()
rc_q = S.main()
check("quiet hours: exits cleanly", rc_q == 0)
check("quiet hours: nothing fetched", not fetched, fetched[:3])
check("quiet hours: no run record written", not S.LOG_FILE.exists())
S.ACTIVE_FROM, S.ACTIVE_TO = 0, 24

print(f"\n{len(failures)} FAILURE(S): {failures}" if failures else "\nALL CHECKS PASSED")
sys.exit(1 if failures else 0)
