# Clear Sky — technical documentation

**Documented version: 1.21** · last updated 2026-09-25

This is the complete technical reference: what runs, where the data comes from, how a Telegram post becomes a
mark on a map, how an official alert becomes a colour, what is stored, what is sent, and how to change any of
it safely. It is **updated with every patch** — see [§18](#18-the-patch-checklist). `tests/test_docs.py` fails
the build when this file, `APP_VERSION` and `CHANGELOG.md` disagree, or when a route, a config key, an
environment variable or a database table exists in the code but not here.

The rules the code follows are in [SAFETY.md](SAFETY.md). This document says *how*; SAFETY.md says *why not
otherwise*. Deployment walkthrough: [DEPLOY.md](DEPLOY.md). Strings and languages: [I18N.md](I18N.md). The
regression corpus: [CORPUS.md](CORPUS.md). French quick start: [DEMARRAGE-FR.md](DEMARRAGE-FR.md).

---

## Contents

1. [What it is](#1-what-it-is)
2. [Principles the code enforces](#2-principles-the-code-enforces)
3. [System overview](#3-system-overview)
4. [Repository layout](#4-repository-layout)
5. [Runtime, configuration and secrets](#5-runtime-configuration-and-secrets)
6. [Official alert data](#6-official-alert-data)
7. [The Telegram feed and the map marks](#7-the-telegram-feed-and-the-map-marks)
8. [Translation](#8-translation)
9. [Notifications](#9-notifications)
10. [The two pages: Tactical and Light](#10-the-two-pages-tactical-and-light)
11. [Privacy and security](#11-privacy-and-security)
12. [HTTP API](#12-http-api)
13. [Storage](#13-storage)
14. [Testing and quality](#14-testing-and-quality)
15. [Deployment and the GitHub repository](#15-deployment-and-the-github-repository)
16. [Maintenance recipes](#16-maintenance-recipes)
17. [Known limitations and open items](#17-known-limitations-and-open-items)
18. [The patch checklist](#18-the-patch-checklist)

---

## 1. What it is

An **unofficial** air-raid and drone tracker for **Kyiv city, Kyiv oblast and the oblasts around it**
(Zhytomyr, Chernihiv, Sumy, Poltava, Cherkasy, Vinnytsia). One Python process — standard library only, plus
`cryptography` for Web Push — reads:

- the **official «Повітряна тривога» alert data** (which raions are under alert, at which level), and
- **six public Telegram channels** that report where drones and missiles are,

and serves two web pages from the same data:

| Page | Address | For |
|---|---|---|
| **Tactical** | `/m` (also `/`, `/k`, `/kyiv`) | The full map: every mark with its evidence, the feed, alerts, statistics, display modes, layers. |
| **Light** | `/light` (also `/l`) | One place (Home / Work / Kids / Pin): its raion's official status, what is within 30 km, what is heading there. |

Both install as separate PWAs. Production runs on Fly.io as `kyiv-air-watch-gb` (one shared-cpu-1x machine,
256 MB, a 1 GB volume for the database).

## 2. Principles the code enforces

Summarised here, argued in [SAFETY.md](SAFETY.md), and each pinned by tests:

- **The official alert decides the colour.** Red / yellow / nothing comes only from the official data, by
  raion. A Telegram post can neither start nor end an alert (`OFFICIAL_ALERTS_FROM_TELEGRAM = False`).
- **Nothing is invented.** No trajectory, ETA, threat type, altitude or position the post did not state. A
  course is drawn only if the post reported it; an ETA is a band, only toward the watched place, only for a
  stated course, never for ballistic / Kh-22 / MiG-31K.
- **Weapons that outrun a radius ignore the radius.** Ballistic, supersonic (Kh-22/32) and MiG-31K are
  "immediate": they concern the whole region (150 km), with no distance and no countdown.
- **Stale is never shown as live.** A target with no new report turns grey, then disappears.
- **A news item is not an observation of the sky.** Press releases, tallies and reported speech never become
  marks.
- **Banderol (S-8000) is a jet drone**, never a cruise missile — in parsing, tags, chaining and every language.
- **Places stay on the phone.** The four watched places are never sent to the server; a push subscription
  stores a ~10 km cell, never a village; the Light page works out the oblast and raion on the phone.
- **Every mark is traceable** to the post, the channel, the time and the rule that read it.

## 3. System overview

```
 official data                              app/server.py                                  browser
 ─────────────                              ─────────────                                  ───────
 siren.pp.ua (keyless proxy) ─┐
   or api.ukrainealarm.com    ├─▶ UkraineAlarm ──┐
 alerts.in.ua (token) ────────┤─▶ AlertsInUa ────┤  apply_snapshot()
 ubilling mirror (stand-in) ──┘─▶ Ubilling ──────┤        │
                                                 ▼        ▼
 5 Telegram channels ──▶ Telegram ──▶ Store ◀── State ──▶ /api/state ─────────▶ kyiv.html (/m)
   (t.me/s previews)       │  ingest()        (SQLite)     /api/markers ───────▶ light.html (/light)
 chyste_nebo ──▶ TelegramAPI ─┘ (Telegram API, preview off)
                           │  is_relevant                  /api/feed?lang=
                           │  translate_offline            /api/stream (SSE) ──▶ both
                           ▼                               /api/version (ping)
                     geo.parse_for_channel                        │
                           │                                      ▼
                     markers(): scope → chain → prune      Pusher / proximity_watch ──▶ Web Push ──▶ phone
                           │
                     Translations worker (DeepL / Google, on demand) ──▶ 'tr' event
```

Everything that arrives is **stored before it is interpreted**: a parsing bug is always replayable against the
real post, and a fix to the rules applies to posts already in the database.

## 4. Repository layout

```
clear-sky/
├── app/
│   ├── server.py           HTTP server, API, SSE, source pollers, SQLite store, marker pipeline, push
│   ├── geo.py              Ukrainian parsing: gazetteer, declensions, types, headings, counts, phases, altitude
│   ├── translate.py        offline UK→EN glossary; DeepL / Google machine translation with a circuit breaker
│   └── push.py             Web Push: VAPID ES256 JWT + RFC 8291 aes128gcm, on `cryptography` only
├── static/
│   ├── kyiv.html           Tactical page (one self-contained file)
│   ├── light.html          Light page (one self-contained file)
│   ├── i18n.js             every UI string in EN / UK / FR, place / raion / river / channel names
│   ├── glyphs.js           the weapon silhouettes (G, GLYPH_BY_TYPE), shared by both pages
│   ├── kyiv-map.json       27 oblasts + all raions, in km around Kyiv (geoBoundaries)
│   ├── light-map.json      raions of the 8 oblasts + nearby oblast outlines, rivers, roads, towns (built)
│   ├── kyiv-districts.json the 10 city districts of Kyiv (OSM)
│   ├── ukraine-map.json    oblast outlines in an affine lon/lat projection (Light page fallback lookup)
│   ├── admin.html          private dashboard: usage, source health, channels, lead time (/admin, ADMIN_KEY)
│   ├── index.html, mobile.html   older desktop / mobile views (/desktop, /ua)
│   ├── sw.js               service worker: push display and notification clicks
│   ├── manifest.json, light.webmanifest   the two PWAs
│   ├── logo-cs*.png        the Clear Sky logo: app icons (any / maskable), favicon, badge, the notice
│   └── logo*.png (NGO 07300), bf-logo*.png (Black Flame Studio), ukraine-map.LICENSE.md
├── data/
│   ├── ua_regions.json     official region id → oblast / parent raion / map shape (built)
│   └── corpus.jsonl        regression corpus of real posts and their expected readings
├── scripts/
│   ├── build_geo.py        rebuilds data/ua_regions.json and static/light-map.json
│   ├── corpus.py           add / review / pull corpus cases
│   ├── release.bat         ships a patch: checks all is committed, fly deploy, git push, shows the live version
│   ├── telegram-login.bat  one-time Telegram sign-in for the API reader (runs telegram_login.py)
│   ├── github-push.bat     creates the private GitHub repo and pushes (browser sign-in via GitHub CLI)
│   └── start.sh, dev.sh, tunnel.bat
├── tests/                  pytest suites (see §14) and tests/fixtures/
├── docs/                   this file, SAFETY, DEPLOY, I18N, CORPUS, DEMARRAGE-FR
├── .github/workflows/      ci.yml (tests on 3.9 and 3.12), fly-deploy.yml (optional auto-deploy)
├── Dockerfile · fly.toml · render.yaml · deploy-fly.bat · start.bat
├── config.example.json     template for config.json (config.json itself is never committed)
└── CHANGELOG.md · README.md · CONTRIBUTING.md · SECURITY.md · LICENSE
```

## 5. Runtime, configuration and secrets

### 5.1 The process

`python app/server.py` (`--demo` for fake alerts, `--port N`). One `ThreadingHTTPServer` and these threads:

| Thread | What it does | Cadence |
|---|---|---|
| `UkraineAlarm` | official alerts by raion (keyless proxy or keyed API) | `/alerts/status` every 10 s, full list on change or every 2 min |
| `AlertsInUa` | official alerts from alerts.in.ua, when a token is set | 15 s |
| `Ubilling` | oblast-level mirror; applies only while the raion source is down | 30 s |
| `Telegram` | reads the channels' `t.me/s/` previews (all but those the API reads) | 30 s; 10 s while a missile threat is open |
| `TelegramAPI` | reads `telegram_api_channels` (chyste_nebo) through the Telegram client API | updates as published + catch-up every 30 s (10 s in a missile threat) |
| `Translations` | machine-translates feed posts into a language somebody reads | on demand, ~3 calls/s max |
| `Pusher` | sends Web Push | queue |
| `proximity` | per-subscriber distance checks → push | continuous |
| `AlertsInUa.backfill` | a month of history for the favourite regions | once at start |

`poll_gap()` shortens the official-source and Telegram intervals while a missile threat is open; the pages
follow `/api/version`'s `msl` level (0 → 15 s, 1 → 5 s, 2 → 1 s pings).

### 5.2 Configuration keys

`config.json` is created from `DEFAULT_CONFIG` on first run and is **git-ignored**. `config.example.json` is
the committed template.

| Key | Default | Meaning |
|---|---|---|
| `port` | `8642` | HTTP port (`PORT` wins). |
| `bind` | `0.0.0.0` | `127.0.0.1` keeps it on the machine. |
| `marker_ttl_minutes` | `45` | Hard ceiling on a mark's life. |
| `track_stale_minutes` | `5` | No new report for this long → grey. |
| `alerts_in_ua_token` | `""` | alerts.in.ua token; when set, alerts.in.ua is the primary alert source. |
| `ukrainealarm_key` | `""` | Official API key; when set, the API is read directly instead of the proxy. |
| `use_siren_proxy` | `true` | Read the keyless siren.pp.ua proxy of the official API when there is no key. |
| `telegram_api_channels` | `["chyste_nebo"]` | Channels read through the Telegram API (their web preview is off); needs the `TG_*` secrets. |
| `use_ubilling_fallback` | `true` | Run the oblast-level mirror (stand-in only). |
| `poll_ukrainealarm_seconds` | `10` | Official raion data interval. |
| `poll_alerts_seconds` | `15` | alerts.in.ua interval. |
| `poll_ubilling_seconds` | `30` | Mirror interval. |
| `poll_telegram_seconds` | `30` | Telegram interval. |
| `poll_telegram_missile_seconds` | `10` | Telegram interval while a missile threat is open. |
| `telegram_channels` | the six | Informational only — `AUTHORITATIVE_CHANNELS` in code is what is read. |
| `favourites` | `["31","14"]` | Regions for history backfill and desktop notifications. |
| `history_backfill_hours` | `6` | History pulled at start. |
| `desktop_notifications` | `true` | Local desktop notifications (when running on a PC). |
| `deepl_key` | `""` | DeepL API key for EN/FR translation of the feed. |
| `google_translate_key` | `""` | Google Cloud Translation key (alternative). |
| `access_key` | `""` | Locks the WHOLE app behind a key — private deployments only. |
| `demo` | `false` | Fake alerts and posts. |

### 5.3 Environment variables

Environment variables win over `config.json`. On Fly they are set as **secrets** (`fly secrets set …`).

| Variable | Meaning |
|---|---|
| `PORT` | Listening port (Fly sets it). |
| `DB_PATH` | SQLite file (Fly: on the volume). |
| `CONFIG_PATH` | Alternative `config.json` location. |
| `ADMIN_KEY` | Protects `/admin` and its APIs only. The map stays public. |
| `ACCESS_KEY` | Puts the whole app behind a key. **Never on the public app.** |
| `ALERTS_IN_UA_TOKEN` | alerts.in.ua token. |
| `UKRAINEALARM_KEY` | Official ukrainealarm API key. |
| `DEEPL_KEY` | DeepL key (`…:fx` = free plan → api-free.deepl.com). |
| `GOOGLE_TRANSLATE_KEY` | Google Cloud Translation key. |
| `TRANSLATOR` | `google` (default) allows the free Google endpoint as last resort; anything else disables it. |
| `TG_API_ID` | Telegram API app id (my.telegram.org). |
| `TG_API_HASH` | Telegram API app hash. |
| `TG_SESSION` | The signed-in Telegram session (a key to that account). Set only by `scripts/telegram-login.bat`, via `fly secrets import`. |

Secrets live only in `config.json` (git-ignored) or in Fly secrets. They are never committed, never printed
by the scripts, and `.dockerignore` keeps `config.json` out of the image.

## 6. Official alert data

### 6.1 Sources and precedence

| Source | Granularity | Key | Role |
|---|---|---|---|
| **api.ukrainealarm.com** — the backend of the government app | oblast, raion, hromada; red/yellow level; official reason text | `UKRAINEALARM_KEY` | Primary when a key is set. |
| **siren.pp.ua** — keyless read-only proxy of the same API | same | none | **Primary without a key (production today).** |
| **alerts.in.ua** | oblast, raion, hromada, city; level; threats | `ALERTS_IN_UA_TOKEN` | Primary when a token is set (the raion source then only reports health). |
| **ubilling mirror** | **one on/off per oblast**, usually no start time | none | Stand-in only, while the raion source has not answered for 90 s. |

The mirror lights an oblast as soon as any raion in it is under alert. That is why it can only stand in: it
painted the whole of Kyiv oblast red while Boryspil raion had been clear for ten minutes (24 Sep 2026). When it
stands in, the Light page says "official data for the whole oblast only — your raion is unknown", and an alert
with no known start shows no "since" (`since_known: false`).

The ukrainealarm reader polls `/alerts/status` every 10 s and reads the full `/alerts` list when the change index
moves — and **at least every 30 s** regardless, so a level change on an alert already on (yellow → red) is never
more than half a minute behind (it was up to 2 min before 1.19).

### 6.2 The official levels

Since 11 Sep 2026 each alert carries the government app's danger level:

| Level | Official meaning | Shown as |
|---|---|---|
| `red` | massed drone, missile, or drone-and-missile threat | red; "AIR RAID ALERT" |
| `yellow` | drone threat | yellow; "ALERT · YELLOW LEVEL" |
| none (mirror) | unknown | treated as **red** — never as "maybe" |

The official reason ("Дронова загроза (жовтий рівень)") is kept word for word in the alert's `notes` and shown
as-is in Ukrainian; EN/FR show the level's definition.

### 6.3 The normalised alert

Every source is normalised to one shape (dicts in `State.active`, rows in `alerts`):

`key, source, location_uid, location_title, location_title_en, location_type (oblast | raion | hromada | city),
oblast_uid, alert_type (air_raid | artillery_shelling | urban_fights | chemical | nuclear | info), alert_level
(red | yellow | null), started_at, finished_at, notes, threats[]` — plus, from the official raion source,
`raion_uid`, `raion_key` (the map shape), `raion_title`; and from the mirror, `since_known`.

`started_at` for the official source is the earliest of the alert's `lastUpdate` and its levels' `createdAt`,
normalised to `YYYY-MM-DDTHH:MM:SSZ`.

### 6.4 From an official id to a place on the map

The API names a raion or hromada by a bare numeric id. `data/ua_regions.json`, built by `scripts/build_geo.py`
from the official region list, maps:

- `states`: official oblast id → this app's oblast uid (Crimea `9999` → `29`);
- `districts`: raion id → name, oblast, **shape key** on the map (`boryspil`, `korostenskyi` …);
- `communities`: hromada id → name, parent raion (cities listed at the top level, like "м. Харків та … громада",
  are hromadas of their oblast, not the oblast);
- `ignore`: the API's test region.

An id not in the table is **kept, never dropped**, placed as well as its name allows, and logged once
("rebuild ua_regions.json"). Oblast uids follow alerts.in.ua: 31 Kyiv city, 14 Kyiv oblast, 10 Zhytomyr,
25 Chernihiv, 20 Sumy, 19 Poltava, 24 Cherkasy, 4 Vinnytsia.

### 6.5 Reconciliation

`State.apply_snapshot(source, alerts, authoritative_levels)` diffs a source's full list against the active set
and emits `start`, `update`, `threat` and `end` events (stored in `events`, published on SSE). A source only
**closes** its own alerts or alerts at levels it is authoritative for — so the mirror (authoritative for
`oblast`) can never close a raion alert, and the raion source (authoritative for every level) takes back what
the mirror painted when it returns. `snapshot()` merges duplicates and computes each oblast's status: **A**
(an oblast-wide alert), **P** (only raions / hromadas), **N**.

### 6.6 What decides a colour, page by page

- **Tactical**: each raion shape is coloured by alerts with its `raion_key` (fallback: name stems); an
  oblast-wide alert colours all its raions. The banner uses the watched oblast pair (city ⇄ oblast) plus the
  home's own raion (found by point-in-shape on the map's outlines). The calm line never says "no alert in the
  oblast" while any raion of it is under one.
- **Light**: see [§10.2](#102-light-light).

## 7. The Telegram feed and the map marks

### 7.1 The channels

Only these are read (`AUTHORITATIVE_CHANNELS`, in code on purpose — deployed configs list 33 channels, and a
config whitelist would have changed nothing on any machine):

| Channel | Kind | Parser |
|---|---|---|
| `kyiv_airdef` | fast live tracker over Kyiv | `parse_kyiv_airdef` (one mark per line, ✈️ = drone, "A - B" routes) |
| `chyste_nebo` | fast live tracker; **states drone heights** | same — its web preview is off, so it is read through the **Telegram API** (`TelegramAPI`, §7.2) once the `TG_*` secrets are set; without them it shows as a failed source |
| `kievinfo_kyiv` | live tracker + news | same, with the news guard |
| `war_monitor` | wider picture, jet drones | general `parse_post` |
| `eRadarrua` | per-oblast group counts + live lines | `parse_eradar`, `parse_eradar_summary` (counts on oblast tags, never marks) |
| `kpszsu` | the Air Force | general parser + morning summary (`parse_af_summary`, launch totals) |

A channel whose preview shows no messages is marked failed with "web preview disabled by the channel", so "no
drones reported" and "cannot read this channel" never look the same.

### 7.2 From page to stored post

Two readers, one path. `Telegram.poll()` parses a channel's preview HTML; `TelegramAPI` receives a channel's
posts as Telegram updates (and re-reads the last 20 every 30 s). Both hand `(post_id, time, text)` to
`Telegram.ingest()`, so a post read through the API is stored, tagged, parsed and shown exactly like one read
from a preview. The preview reader skips a channel the API is reading (`api_channels`).

`Telegram.ingest()` skips posts already stored, runs `tag_feed_text()` and
`is_relevant()` (news, fundraising, culture → dropped), stores the post with the **offline** English (§8) and
publishes a `feed` event. Tagging order matters: forecasts first (`FORECAST_RX` — "оцінка загроз", "може
відбутись у будь-який момент" — never raise anything), then the news guard (`geo.looks_like_news`: strong news
words, reported speech and consequences, length, summaries), then threat tags. `banderol_missiles` removes
`cruise_missiles` and `unspecified_missiles`.

### 7.3 From post to mark (`geo.py`)

`parse_for_channel(channel, text)` picks the channel's parser; each returns candidates with an **evidence**
block for every value:

- **Type** — `TYPE_RX`: drones (Shahed / Geran / "мопед"), jet drones, cruise, ballistic, supersonic
  (Kh-22/32, before cruise), `banderol_missiles` (Banderol / S-8000 / "реактивні ракети з БпЛА"), KAB, tactical
  and strategic aviation, MiG-31K. No type word → `unknown` (drawn as an upright ⚠, never guessed).
- **Position** — the gazetteer with declension-aware stems (hyphen lookbehind: `Коцюбинське` ≠
  `Михайло-Коцюбинське`); aliases like "Велика Димерка" ← "димерк"; a stated part of an oblast is not its centre.
- **Heading** — "курсом на", compass words, "→", "A - B" routes (`bearing()`); a heading is marked *stated*
  only when the post stated it. A compass word after "з / зі / від" is where it comes **from** ("з півночі" = flying
  south), also after the destination (`_FROM_COMPASS`).
- **Where it is vs where it is going** (`_place_role`): a place after "на", "в район", "до", "в напрямку", "в бік"
  is the **destination**; after "від", "з", "повз", "над", "біля", "в районі" it is where the target **is**. After
  "на", a place in the locative ("на Позняках", "на Троєщині") is where it is. Only a destination named
  ("на Васильків з північного заходу", "курсом на Київ"): the mark is drawn **at the destination as an approach**
  (`approach: true`, `place: "→ X"`, low confidence) — also when the post's only other place is its own oblast
  heading. On live channels, "Від Глевахи на Васильків" is at Hlevakha heading for Vasylkiv; "йдуть на Васильків"
  alone (`dest_only`) stays at the channel's previous report (≤ 8 min) turned toward the destination, or else is an
  approach. In tracking, a later approach about a target already on the map gives it its destination and course
  instead of moving it; an approach point is never a step of a track.
- **Count**, **altitude** (only when written: "знижується", "низько", metres "висота 2200", a bare "1600, Вороньків"
  in a short live post, kilometres "висота 4,4км"), **phase** (`PHASE_LADDER`:
  prep → launch → entering…, with a per-sentence hedge guard: "може відбутись" is preparation, not a launch).
- **Immediate types** — `IMMEDIATE_TYPES = {ballistic, supersonic, mig31k}`, `REGION_ALERT_KM = 150`.

### 7.4 Scope, chaining, staleness

`State.markers()`:

1. parses the recent feed into candidates; merges identical ones from the same post (a count);
2. drops what is outside the scope: `in_scope()` = oblast uid in `REGION_UIDS` or within `SCOPE_KM = 330` km
   of Kyiv;
3. `_chain_and_prune()` links consecutive reports of the same target (same family, time and distance windows,
   roughly ahead of the earlier heading) into a track with `history` — each entry keeps its own report's stated
   height (`alt_m`, `alt_state`), so the pages can show the heights the posts gave in sequence; marks `stale` after
   `track_stale_minutes`; drops an unknown after 5 min, others after `max(stale × 3, 15)` min; drops
   everything in an oblast with **no active alert of any kind** once 3 min old; missiles age on their own
   `MISSILE_TTL_MIN`;
4. outcomes (down, impact, lost) close the track they belong to.

Fires, ground damage and the channels' own "чисто" are not drawn (`HIDDEN_STATUS` on the pages).

## 8. Translation

The feed is Ukrainian; EN and FR readers get a translation.

- **At ingest: the offline glossary only** (`translate_offline`) — instant. It never waits for a network call:
  the free Google endpoint refuses data-centre addresses, and each refusal used to cost ~3 s per post in front of
  the drone reports of the same poll. The glossary (`G`, `WORDS`) covers the channels' vocabulary, collected
  from ~1,000 real posts; anything left is transliterated.
- **Then, on demand: machine translation** (`Translations` worker): DeepL (`DEEPL_KEY`), else Google Cloud
  (`GOOGLE_TRANSLATE_KEY`), else the free Google endpoint. Domain words are fixed before (`PRE`, `PRE_FR`: "мопед"
  → Shahed, "реактив" → jet drone, "ударні" → strike drones) and after (`POST`, `POST_FR`).
- **Only for a language somebody reads.** A page in EN/FR sends `?lang=` on `/api/feed` and `/api/stream`; for
  the hour after, new posts are translated into it, and older ones in view are queued. The result is stored
  (`feed.text_en` + `en_mt = 1`, or `feed.text_fr`) and announced with a `tr` event; the page reloads the feed.
- **Circuit breaker**: a backend that refuses (401/403/429/456) rests 30 min, any other failure 5 min.
- French has no offline stand-in: without a machine translator, FR shows the English.

## 9. Notifications

- **Web Push** (`push.py`; the VAPID key pair is generated on first run and kept in the `kv` table). A subscription
  stores the browser's coarse location snapped to a **~10 km cell** (`HOME_CELL_KM`), never a name.
- **What is pushed**: MiG-31K airborne (country-wide), ballistic threat (Kyiv city/oblast), and from
  `proximity_watch`: a live, non-stale target within the subscriber's radius (+ half a cell), throttled to one
  push per 2 min per device — except immediate types, which alert the whole region with no throttle. Siren-start
  and all-clear pushes were removed on purpose (nothing to act on, and they train people to swipe).
- **In the page**: the Tactical page raises toasts with sound/vibration scaled by zone (near / approach /
  observe), and a **shoot-down notice** for a track the reader was warned about — worded so it can never read
  as an all-clear.

## 10. The two pages: Tactical and Light

### 10.1 Tactical (`/m`)

One self-contained `kyiv.html`: an SVG map in a km projection around Kyiv (`proj`/`unproj`, `KX`, `KY`),
level-of-detail classes by zoom, OSM tiles under the vector layers below 150 km width. Main parts:

- `render()` — official colours (raions, oblasts, banner band, raion chips, alerts tab);
- `tick()` — marks: silhouettes per weapon (`G`, `GLYPH_BY_TYPE` from `static/glyphs.js`), turned only to a stated course (`orientOf`),
  uncertainty ring up to 25 km, 5-minute tail, labels, off-screen edge indicators;
- `threatStrip()` — the banner stack, coloured by `officialLevel()`; `idleLine()` when calm;
- the four places (`SLOTS`: Home / Work / Kids / Pin — `localStorage` only), zones 10 / 30 / 60 km (their
  labels sized in screen pixels, `--k`), the conditional ETA band (`etaBand`);
- **the 📍 pin**: press and hold on open map (a ring fills while the finger stays; a drag only starts past
  `HOLD_SLOP_PX` = 10 screen px; the browser's long-press menu is suppressed), or tap the empty Pin chip to arm the
  map (`armPin`, banner with Cancel) and tap where it goes (`dropPin`);
- tabs: **Feed**; **Alerts** (the live tally of what is on the map, then the official alerts); **Stats** — the Air
  Force's official figures only (`windows` 24 h / 7 d / 30 d and each summary with a link to its post,
  `af_days`); **Map** (layers);
- **approach marks** (drawn at the town they are heading to): a dashed course coming **into** the town from the
  side the post named, no ray or cone ahead, never dead-reckoned (Est.), no ETA; label "→ Vasylkiv from the NW";
  on Light "heading for Vasylkiv — where it is now was not said";
- **mark labels never overlap**: after each tick the label blocks are placed in screen pixels — which lines are
  drawn is read back from the CSS (`getComputedStyle`), candidates right / left / a line lower or higher, clear of
  other labels, other marks' glyphs and the map's edge; placed by priority (to a watched place, inbound,
  descending, others, outcomes); an ordinary label that fits nowhere loses its second line (`nolbl2`), then is
  left out (`nolbl`). The boxes (`LBLBOX`) hide the town names under them (`layoutTownLabels`);
- the OSM tile credit sits small in the bottom corner; the Crimea Cossack is drawn in `#obllbl`, above the raion
  outlines, one path per colour;
- display modes: normal, day (inverted, WCAG-checked), night (dim, arrows only), blackout (2G);
- the **notice** (disclaimer, language, oblast): shown **once per opening** — `sessionStorage.disc_seen`, shared
  with the Light page, so switching Light ⇄ Tactical does not show it again; always reachable from the menu.
  Changing the language inside it reloads the page **and shows it again** in the new language
  (`pickLang(l, true)` clears `disc_seen`); "I understand" is a full-width button, centred (`.dok`).

### 10.2 Light (`/light`)

One place, what concerns it, nothing else. Built to load and be read on 2G (page ≈ 19 KB gzipped, map data
≈ 41 KB, fetched after the status is on screen; `test_light` holds both under budget).

- **Status = the place's raion**, from the official data only. The raion is found **on the phone**: point in
  polygon on `light-map.json`'s raion outlines. A place within **1.5 km of a border** belongs to both raions
  (the outlines are accurate to a few hundred metres; a house on the line gets either side's alert). Outside
  the covered oblasts it falls back to the oblast (`ukraine-map.json`).
- States: **R** / **Y** (alert over the place's unit, official level + reason), **H** (a hromada inside the
  raion is under alert), **P** (raion unknown, part of the oblast), **G** (no alert over the unit — neighbours
  under alert are *named*, never painted on the place), **U** (no data). "Nothing reported" never says "safe".
- **Radar: 30 km** (`R_KM`), rings at 10 / 20 / 30 km, over a local map: raions shaded by alert, oblast borders,
  Kyiv's districts, rivers, main roads, towns, villages and neighbourhoods (from `/api/places`), labels placed
  without overlap. Marks are **the same silhouettes as the Tactical map** (`static/glyphs.js`): turned to a
  course only when the post reported one (`markOrient`), upright with a "?" otherwise; the ⚠ for unknown
  types stays upright with a small orange course arrow; a dotted tail joins earlier reports; each mark
  carries its list number. The list shows the same silhouette beside each row.
- **List**: number, type, count, distance, zone, "→ here" (stated course within 28° of the place), place,
  course, **height** when a post stated one (descending in crimson; a run of stated heights as
  "↓ 2,2 км → 1,6 км → 600 м" via `altTrend()` in `i18n.js`), age, ETA band. The height is also written beside
  the mark on the radar. **Tap a row** for what the post said (original + translation), the track so far,
  altitude when stated, the source and time.
- **The radar zooms by itself** (1× = the 30 km disc, up to 4×): pinch, double-tap, the + / − / ⟲ buttons,
  ctrl + wheel. Only the map zooms — never the page; unzoomed, one finger still scrolls the page (`touch-action:
  pan-y`), zoomed in, one finger moves the map. Symbols and names sit in groups scaled by `1/zoom` (`--iz`), so
  they keep their size; names are placed again (more of them) once the fingers stop. Nothing on it can be
  selected as text.
- **Tap a mark on the radar**: the nearest mark within a finger's width (`tapAt`, `RT`) opens its row as a card
  over the part of the map it is not on (`#rcard`), kept current on every repaint; a tap on open map closes it.
- **Approaching (30–100 km)**: listed apart, only targets whose **stated** course points at the place.
- Immediate types: a banner for the whole region, no distance, no countdown.

### 10.3 The switch

Both pages carry the **LIGHT | TACTICAL** switch (UK: ПРОСТА | ТАКТИЧНА, FR: LÉGÈRE | TACTIQUE): a full-width
two-segment control on Light, each half captioned; a high-contrast pill in the Tactical header.

### 10.4 Languages

`static/i18n.js`: every string in EN / UK / FR (`t()`, `tn()` for plurals), place, raion, river and district
names, `OBL_N` / `oblFull()`. A test fails when a key is missing in one language. See [I18N.md](I18N.md).
The language is a **drop-down** on both pages (`langSelect()`: the Tactical menu and notice, the Light header —
flag and code only — and notice); `pickLang()` stores it and reloads.

## 11. Privacy and security

- The four places never leave the phone; the Light page's requests carry no place (checked in the render tests).
- Push subscriptions: a ~10 km cell only; older stored homes are coarsened at start.
- The Telegram session (`TG_SESSION`) is a key to a whole Telegram account. It is created on the owner's PC by
  `scripts/telegram-login.bat` and goes straight into Fly's secret store (`fly secrets import`, read from stdin):
  never on a command line, in a file, in the logs, in `config.json` or in the repository. Revoke it in Telegram:
  Settings → Devices → "Clear Sky server" → Terminate.
- Usage counters (`usage`, `usage_seen`): a daily salted hash, no IP address, no location, no per-person
  history.
- Flags and the corpus review are behind `ADMIN_KEY`.
- No secret is committed: `config.json`, `vapid.json`, `.env`, `*.pem`, databases and `.flyapp` are
  git-ignored; the history was scanned for tokens before the repository was published.
- Static files are served gzipped with an ETag (`Cache-Control: no-cache`): a phone revalidates instead of
  downloading again, and still picks up a new version immediately.

## 12. HTTP API

All JSON unless stated. Public unless marked 🔒 (`ADMIN_KEY`).

| Route | Returns |
|---|---|
| `GET /`, `/m`, `/k`, `/kyiv` | Tactical page |
| `GET /light`, `/l` | Light page |
| `GET /desktop`, `/index.html`, `/dash` | older desktop view |
| `GET /ua`, `/mobile`, `/mobile.html` | older mobile view |
| `GET /manifest.json`, `/light.webmanifest`, `/sw.js`, `/favicon.ico`, `/static/*` | PWA files, assets (gzip + ETag) |
| `GET /healthz` | liveness |
| `GET /api/version` | `{v, now, missile, msl, build, app}` — the cheap ping pages poll |
| `GET /api/state` | active alerts, per-oblast status, sources' health, єРадар counts, config summary |
| `GET /api/markers` | live marks with evidence, history, staleness |
| `GET /api/feed?limit=&lang=` | recent posts (`text`, `text_en`, `text_fr`, `en_mt`, tags); `lang` asks for translation |
| `GET /api/stream?lang=` | SSE: `start`, `end`, `threat`, `update`, `feed`, `eradar`, `tr` |
| `GET /api/history?hours=&oblast=` | past alerts |
| `GET /api/events_log?limit=` | alert events |
| `GET /api/impacts?hours=` | explosions and confirmed shoot-downs |
| `GET /api/stats?days=` | statistics; the Stats tab reads only `windows` and `af_days` (the Air Force summaries, each with its `post`) |
| `GET /api/places?oblast=` | gazetteer (names, coordinates, oblast) |
| `POST /api/push/subscribe`, `/api/push/unsubscribe`, `/api/push/test`; `GET /api/push/key` | Web Push |
| `POST /api/flag` | "this reading is wrong" report |
| 🔒 `GET /admin`, `/api/usage` | dashboard, usage counters |
| 🔒 `GET /api/health` | official sources (ok, last check, detail), every channel read (posts / marks 24 h, share read 7 d, last post, reader state), translation backends, lead time over the official alert in Kyiv + oblast (30 days) |
| 🔒 `GET /api/flags`, `/api/corpus`; `POST /api/corpus/review`, `/api/corpus/translate` | flags and corpus review — no longer in the dashboard (1.19); used by `scripts/corpus.py` |

## 13. Storage

SQLite in WAL mode (`DB_PATH`; on Fly, the volume). Tables:

| Table | Holds |
|---|---|
| `alerts` | every alert seen (active and finished) — the normalised shape |
| `events` | start / end / threat / update events |
| `feed` | posts: `post_id, channel, ts, text, tags, text_en, text_fr, en_mt` |
| `kv` | small settings: the VAPID key pair, the usage counter's daily salt, and similar |
| `push_subs` | push subscriptions with a ~10 km cell |
| `marker_log` | every mark as computed, with its evidence |
| `outcomes` | shoot-downs, impacts, losses |
| `channel_stats` | per channel per day: posts, posts with a reading, flagged readings |
| `flags` | readings people reported as wrong |
| `corpus_reviews` | verdicts from `/admin` review, merged back with `scripts/corpus.py pull` |
| `usage`, `usage_seen` | anonymous usage counters |

Columns are added in place by `Store.__init__` (`ALTER TABLE … ADD COLUMN`), so an old database upgrades itself.

## 14. Testing and quality

```bash
pip install -r requirements-dev.txt
python -m pytest              # ~410 tests
python -m ruff check app tests scripts
python app/server.py --demo --port 8099
```

| Suite | Pins |
|---|---|
| `test_geo`, `test_tagging`, `test_misreads`, `test_odesa`, `test_news_and_banderol` | parsing, tags, news guard, Banderol |
| `test_phases`, `test_immediate`, `test_marks`, `test_prune` | lifecycle, immediate types, silhouettes, staleness |
| `test_raion_alerts`, `test_scope`, `test_banner` | official data by raion, channels, scope, banner |
| `test_light`, `test_places`, `test_privacy_push`, `test_usage` | Light page, places stay local, push cell, counters |
| `test_translation`, `test_i18n`, `test_modes` | translation path, three languages, display-mode contrast |
| `test_corpus`, `test_review_source`, `test_af_summary` | the regression corpus, review, launch totals |
| `test_cadence`, `test_version`, `test_keys`, `test_docs` | refresh rates, build info, keys, this documentation |
| `test_official_stats`, `test_ui_details` | Air Force-only stats, the dashboard's health view and lead time, pin, zoom, language, logo |
| `test_destination`, `test_scripts_parse` | where it is vs where it is going; every page script parses (node) |

**The corpus** (`data/corpus.jsonl`, [CORPUS.md](CORPUS.md)): real posts with their expected reading; a
changed reading fails the suite until it is re-verified as the intended change.

**Render checks** (before each release, not in CI): Playwright against a local server, Tactical in three
languages × phone/desktop × modes, Light for several places, no JS errors, no overflow, no request carrying a
place.

CI (`.github/workflows/ci.yml`) runs ruff and pytest on Python 3.9 and 3.12, starts the service and checks it
answers.

## 15. Deployment and the GitHub repository

- **The working folder** is `Desktop\clear-sky-main` on the owner's PC: a Git clone of the private repository
  **Dread92/clear-sky**. Patches are committed there.
- **Shipping a patch**: double-click **`scripts\release.bat`**. It refuses to run if anything is not committed,
  deploys to Fly.io, pushes to GitHub, and prints the live `/api/version` — the version shown must be the
  new one.
- **First deployment, or changing a key**: `deploy-fly.bat` (Windows) — sign-in, app, volume, the dashboard
  key, the optional DeepL key, deploy. Details in [DEPLOY.md](DEPLOY.md). `fly deploy -a kyiv-air-watch-gb`
  alone also works.
- **GitHub**: `scripts\github-push.bat` creates the **private** repository `<you>/clear-sky` with the GitHub CLI
  (sign-in in the browser; no token is ever typed or stored by the script) and pushes; run again to push new
  commits. Optional auto-deploy: add the repository secret `FLY_API_TOKEN` and `fly-deploy.yml` deploys every
  push to `main`.

## 16. Maintenance recipes

**Rebuild the geography** (after an administrative change, or when the log says "not in ua_regions.json"):
`python scripts/build_geo.py` — downloads the official region list, rewrites `data/ua_regions.json` and
`static/light-map.json`, prints any raion it could not match to a map shape. Run the tests; commit both files.

**Telegram sign-in for the API reader**: create an app on <https://my.telegram.org> (API development tools),
then run `scripts\telegram-login.bat`: it asks for the api_id, the api_hash and the phone number of the reading
account (a dedicated account is best), signs in with the code Telegram sends, joins the channel, and stores the
three secrets on Fly. If the sources panel ever shows "Telegram session no longer valid", run it again.

**Add or remove a channel**: edit `AUTHORITATIVE_CHANNELS` in `server.py` (and `DEFAULT_CONFIG` for the record);
a channel whose web preview is off also goes in `telegram_api_channels`;
a fixed format gets a parser in `geo.py` registered in `CHANNEL_PARSER`; add real posts to the corpus
(`scripts/corpus.py add`); a mixed channel goes in `NEWS_CHANNELS`.

**Change the region**: `REGION_UIDS` and `SCOPE_KM` in `server.py`, `SCOPE` in `build_geo.py` (then rebuild),
the Light page's place list (`/api/places?oblast=…`).

**Add a string**: all three blocks of `i18n.js`; `test_i18n` checks.

**Regenerate the app icons** from the master logo `docs/brand/clear-sky-logo.png` (transparent PNG): the files
are `static/logo-cs-512.png` / `-192` (logo on `#0b0d11`, 90 %), `logo-cs-maskable-512.png` (56 %, inside the
maskable safe circle), `logo-cs-64.png` (the radar emblem alone, transparent — favicon), `logo-cs-badge.png`
(white on transparent, for Android's notification badge) and `logo-cs.png` (280 px, the notice). The 07300 and
Black Flame logos (`logo*.png`, `bf-logo*.png`) stay as they are.

**Official data by key instead of the proxy**: request a key at <https://api.ukrainealarm.com/>, then
`fly secrets set UKRAINEALARM_KEY=…` — the same code reads the API directly.

## 17. Known limitations and open items

- `chyste_nebo` is read through the Telegram API with a user session. Telegram can restrict an account that
  reads channels by program; a dedicated account keeps the owner's own out of that risk. Its post formats have
  not been seen yet (the preview never showed them): check the first live posts and tune the parser.
- siren.pp.ua is a volunteer proxy of the official API; an official key is more robust (§16).
- Without a DeepL / Google key, English is the offline glossary and French shows English.
- Ukrainian declension of place names after a preposition ("біля …") is not implemented; labels avoid it.
- `outcomes` is written but nothing reads it yet (`marker_log` and `channel_stats` feed the dashboard's health view).
- About 265 corpus cases are still pending review; the review left the dashboard in 1.19 and is done from the
  terminal (`scripts/corpus.py`, [CORPUS.md](CORPUS.md)) when needed.
- Hromada alerts have no shapes of their own: on the Tactical map they colour their parent raion; on Light they
  are named ("alert in part of your raion").

## 18. The patch checklist

Every patch, in this order:

1. Code + tests (a behaviour change gets a test that fails without it).
2. `python -m pytest` and `python -m ruff check app tests scripts` clean; render checks for page changes.
3. Bump the version in three places: `APP_VERSION` in `app/server.py`, and in `static/kyiv.html` both
   `const APP_VERSION` and the `buildtag` footer (`tests/test_version.py` checks they agree).
4. Add the `## X.Y.0 — date` entry at the top of `CHANGELOG.md` (Added / Changed / Fixed / Removed).
5. Update **this file**: the header's *Documented version* and date, and every section the patch touches
   (routes → §12, config / env → §5, tables → §13, sources → §6–7, pages → §10, open items → §17).
   `tests/test_docs.py` enforces the version, routes, config keys, env vars and tables.
6. Commit in the working folder (never `config.json`), then ship with `scripts\release.bat` (deploy + push).
7. Post the patch note to users once it is live.
