# SlideEgg → WhatsApp Channel Auto Poster

SlideEgg website-la **puthu template** illa **puthu blog post** upload aanaa, **30 nimishathukkul** WhatsApp Channel-la automatic-a post aagum.

---

**Channel:** SlideEgg Presentation Hub (50 followers)
`https://whatsapp.com/channel/0029Vb7WIkq35fLwXKie5521`

## Idhu eppadi work aagudhu

**Kaalai 8 mani – rathiri 9 mani** varaikum, **30 nimishathukku oru thadava** rendu edathayum check pannudhu:

**A) Templates** — `slideegg.com/latest-powerpoint-templates`

1. Idhu **newest-first**, oru page-la 24 template. Default-a **3 page** (72 template) scan pannudhu.
2. Andha page-la SlideEgg thaana embed panra **JSON-LD data** irundhu title + template link + thumbnail edukudhu (HTML card parse pannaama — adhanaala site design maarinaalum work aagum)
3. `state/seen.json`-la already irukuradhu skip pannudhu → **oru template rendu thadava post aagaadhu**
4. Puthu template page-la irundhu description + full-size PNG edukudhu

**B) Blog** — `slideegg.com/blog/`

5. Blog index-la irukura 30 post-a paakudhu, `seen.json`-la illaadhadha eduthu, andha page-la irukura **`datePublished`** date check pannudhu
6. **14 naalaikku munnadi publish aanadhu-na skip pannudhu** (kizhe paarunga — idhu romba mukkiyam)

**Rendukum common:**

7. Image-a download panni Whapi API vazhiya WhatsApp Channel-la caption-oda post pannudhu
8. `seen.json` update panni repo-la commit pannudhu

> ### ⚠️ Blog-la oru periya thappu thavarthen
>
> Modhalla `blog/sitemap.xml`-la irukura `lastmod` date vechu "puthu blog post"
> kandupidikalam nu nenachen. Live-a check panna appo theriyudhu — **innaiku
> update aana 10 blog post-um pazhaya post**, 2024, 2025-la publish aanadhu.
> Ungaloda SEO team pazhaya post-a edit panra podhu `lastmod` maarudhu.
>
> Adha vechu pannirundha, **2024-la publish aana post-a "New on the blog"**
> nu channel-la share pannirukum. 😬
>
> Adhanaala script `lastmod`-a nambaama, ovvoru post-oda **real
> `datePublished`** date-a paakudhu. 14 naalukku pazhasa irundha skip.
> (`MAX_AGE_DAYS` maathikalam.)

> **Yen template-ku sitemap use pannala:** sitemap `lastmod` **5 naal pinnadi**
> irundhuchu — innaiku upload aanadhu sitemap-la varave illa. Listing page
> live-a iruku, adhanaala adhu thaan correct source.

**Safety:** modha run-la onnum post aagaadhu — appo irukura ella template-um "already seen" nu record pannikum (illaati 27,000 template-um post aagidum!). Adhukku aduthu naal irundhu thaan post aarambikum.

---

## Setup — 6 steps (~20 nimisham)

> **GitHub Actions minutes:** 30 nimishathukku oru thadava = naalaikku 28 run ≈ maasathukku 1,300 nimisham.
> Private repo free limit **2,000 nimisham** — ulla varum ✅. Innum vegama check pannanum-na repo-va
> **Public**-a maathunga (unlimited minutes). Secrets public repo-layum encrypted-a thaan irukum, yaarum paakka mudiyaadhu.

### Step 1 — Whapi.cloud account

1. https://whapi.cloud → **Start Free Trial**
2. Email vechu signup pannunga
3. Dashboard-la **"Add channel"** / new instance create pannunga
4. QR code varum → **oru thani company SIM** (personal number vendaam) WhatsApp-la:
   `Settings → Linked Devices → Link a Device` → QR scan
5. Andha number **WhatsApp Channel-oda admin-a irukanum**. Illaati channel-la add pannunga.
6. Dashboard-la **API token** copy pannunga (long string madhiri irukum)

> Free "Developer Sandbox" plan: 150 messages/day — dhinam 10-20 post-ku over-a podhum.
> Premium $29/month (ippo thevai illa).

### Step 2 — Channel ID (idhu automatic ✅)

SlideEgg official channel link script-kulla already set panniten:

```
https://whatsapp.com/channel/0029Vb7WIkq35fLwXKie5521
```

Script modha thadava run aagum bodhu `GET /newsletters/{invite_code}` vazhiya andha link-a
`1203630xxxxxxxxxx@newsletter` ID-a thaana convert pannikum, `state/channel.json`-la
cache panni vechukum. **Neenga onnum panna vendaam.**

Manual-a check pannanum-na (optional):

```bash
export WHAPI_TOKEN="ungaloda_token_inga"
python slideegg_daily.py --resolve    # channel id-a print pannum
python slideegg_daily.py --channels   # andha number-la irukura ella channel-um
```

> Andha Whapi number **channel-oda admin-a irundha thaan** post aagum. Follower-a
> irundha 403/404 error varum. WhatsApp app-la channel open panni →
> Channel name → **Manage admins** → andha number add pannunga.

### Step 3 — GitHub repo create pannunga

1. https://github.com → signup / login
2. **New repository** → name: `slideegg-whatsapp` → **Private** select pannunga → Create
3. "uploading an existing file" click panni, indha folder-la irukura **ella file-um** drag pannunga:
   - `slideegg_daily.py`
   - `requirements.txt`
   - `.github/workflows/autopost.yml`  ← **idhu romba mukkiyam**, folder structure-oda upload aaganum
   - `state/` folder
   - `SETUP.md`

> `.github/workflows/` folder correct-a upload aagala-na Actions run aagaadhu. Drag & drop-la folder structure keep aagum — files-a thani thaniya podaadheenga.

### Step 4 — Secrets add pannunga

Repo-la: **Settings → Secrets and variables → Actions → New repository secret**

| Name | Value | Thevaya? |
|---|---|---|
| `WHAPI_TOKEN` | Step 1-la eduthadhu | **Kandippa venum** |
| `WHAPI_CHANNEL` | Innoru channel-ku post pannanum-na mattum (link, invite code, illa `...@newsletter` ID) | Optional — kudukkalati SlideEgg official channel-ku pogum |

### Step 5 — Baseline run (post aagaadhu, safe)

Repo-la **Actions** tab → left-side-la **"SlideEgg auto-post to WhatsApp Channel"** → **Run workflow**
→ `Dry run` = **true** vechu → Run

Log-la caption preview varum, WhatsApp-ku onnum poga maatadhu. Idhu seri-nu confirm pannunga.

### Step 6 — Live pannunga

Innoru thadava **Run workflow**, aana ippo `Dry run` = **false**.
Idhu modha real run — ella template-um baseline-a record aagum, post aagaadhu.

Adhukku aprom **30 nimishathukku oru thadava** automatic-a check aagum. Puthu template
illa puthu blog post vandha, **30 nimishathukkul** channel-la post aagidum. 🎉

Rathiri 9 mani – kaalai 8 mani varaikum post aagaadhu. Andha nerathula upload aanadhu
kaalai 8 maniku pogum.

---

## Settings maathanum-na

`.github/workflows/autopost.yml` file-la:

| Enna | Enga | Eppadi |
|---|---|---|
| **Evlo neramukku oru thadava check** | `cron: "0,30 2-15 * * *"` | Ippo 30 nimisham. 1 mani-ku: `0 2-15 * * *`. 15 nimisham-ku: `0,15,30,45 2-15 * * *` |
| **Post podura neram** (quiet hours) | `ACTIVE_FROM: "8"` / `ACTIVE_TO: "21"` | IST-la. 9 AM – 7 PM venum-na `9` / `19`. Cron-oda UTC hours-um (`2-15`) match panni maathunga |
| **Oru check-ku evlo post** | `MAX_POSTS` default `5` | Meethi aduthu check-la (30 nimisham) poidum |
| **Evlo template page scan** | `SCAN_PAGES` default `3` (72 template) | Adhigam upload aanaa `5` nu koottunga |
| **Blog "puthusu" evlo naal** | `MAX_AGE_DAYS` default `14` | `7` nu kuraichaa strict. `30` nu koottinaa pazhaya post-um varum |
| **Template mattum venum** | `SOURCES` | `templates` illa `blog` illa `templates,blog` |
| **Weekend venaam** | `cron: "0,30 2-15 * * 1-5"` | Thingal–Velli mattum |
| **Caption format** | `slideegg_daily.py` → `build_caption()` | Template-kum blog-kum thani format iruku |

---

## Problem vandha

| Problem | Karanam / Fix |
|---|---|
| Onnum post aagala | Puthusa edhuvum upload aagalanaa idhu normal. `state/last_run.json` paarunga. |
| Blog post varave illa | 14 naalaikku pazhaya post-a irukum (`blog_skipped_as_refresh` paarunga). Ungaloda blog-la maasathukku 2-4 puthu post thaan varudhu — adhu normal. |
| Rathiri post aagala | Adhu design. `ACTIVE_FROM` / `ACTIVE_TO` maathunga, illa Run workflow-la `force` = true. |
| `whapi HTTP 401` | Token thappu / expire aagiduchu. Secret update pannunga. |
| `whapi HTTP 403 / 404` | Andha Whapi number channel-oda **admin illa**. WhatsApp-la channel → Manage admins → number add pannunga. |
| `channel lookup HTTP ...` | Invite link resolve aagala. `python slideegg_daily.py --resolve` run panni paarunga. |
| Channel maathanum | `state/channel.json` delete panni, `WHAPI_CHANNEL` secret-la puthu link podunga. |
| Image varala, text mattum varudhu | Image download aagala. Script thaana text-a anupidum — post miss aagaadhu. |
| `listing returned nothing` | SlideEgg site design maarirukum. Enkitta sollunga, parser update panni tharen. Indha case-la onnum post aagaadhu, `seen.json`-um kedaikaadhu — safe. |
| Actions run aagave illa | `.github/workflows/` folder structure thappa upload aagirukum. Repo-la path check pannunga. |
| Konja naal kalichu Actions nikkudhu | GitHub 60 naal inactivity-ku aprom schedule disable pannum. Repo-la edhaavadhu oru commit podunga. |

**Log paarka:** Actions tab → last run click → "Run poster" step expand pannunga.

---

## ⚠️ Mukkiyamana warning

Whapi.cloud **Meta-oda official API illa**. WhatsApp **Channels**-ku Meta official posting API kudukala — so unofficial API thaan single vazhi.

Idhu WhatsApp Terms of Service-ku ethiraana vishayam, **andha number ban aaga chance iruku**.

Adhanaala:

- **Personal number kandippa QR scan pannaadheenga** — oru thani company SIM use pannunga
- Channel-ku **innoru backup admin** vechukonga (main number ban aana channel poyidum)
- Dhinam 5-10 post-ku mela podaadheenga
- Ban aana enna nadakum-nu munnadiye team-kitta sollidunga

Idhu risk illama venum-na: script-a `DRY_RUN=1`-la vechu, caption ready panni, kaiyala 30 second-la copy-paste panni post pannalam. Safe, aana manual.
