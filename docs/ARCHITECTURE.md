# Architecture

One process. No framework, no build step, no client bundle. Python's standard library on the
server, one HTML file on the client. That is deliberate: this thing has to keep running at 3 a.m.
during an attack, on a 256 MB machine, and be debuggable from a phone.

```
 public sources                    app/                          browser
 ──────────────                    ────                          ───────
 alerts.in.ua      ─┐
 api.ukrainealarm  ─┤─▶ OfficialAlerts ─┐
 ubilling mirror   ─┘                   │
                                        ├─▶  State  ──▶  Store (SQLite)
 36 Telegram       ──▶ Telegram ────────┤       │              │
 channels (t.me/s)      │               │       │              │
                        └─▶ geo.py      │       │              ▼
                            translate.py┘       │        /api/state
                                                │        /api/markers   ──▶ kyiv.html
                                                ├──────▶ /api/stats          (SVG map,
                                                │        /api/impacts         markers,
                                                │        /api/feed            sheets)
                                                ├──────▶ /api/stream (SSE)
                                                └──────▶ push.py ──▶ Web Push ──▶ phone
```

## The modules

### `app/server.py`

**`Store`** — SQLite (WAL) with five tables: `alerts`, `events`, `feed`, `kv`, `push_subs`.
Everything that arrives is stored before it is interpreted, so a parsing bug is always replayable
against real data. The feed keeps the original Ukrainian text *and* the machine translation.

**Pollers**, each on its own thread:

- `OfficialAlerts` — alerts.in.ua (primary), api.ukrainealarm.com, or the keyless ubilling mirror.
  `ingest()` normalises whatever the source gives into one alert shape; `_apply()` diffs it against
  the current state and emits `start` / `end` / `threat` / `update` events.
- `Telegram` — polls `t.me/s/<channel>` previews with 4 workers in parallel. Interval drops from 30 s
  to 10 s while a missile threat is open. Each post is tagged, filtered by `is_relevant()`, parsed
  by `geo`, translated, and stored.

**`markers()`** is where reports become map objects, and it is the part to be careful with:

1. every recent post is parsed into zero or more candidate targets;
2. candidates from the same post are merged (identical type + position + status collapse into one
   marker carrying a count — this is what stopped seven duplicate markers appearing for one post);
3. official alert announcements can never produce a marker at an oblast centre, and a missile needs
   either a named place or a heading to be drawn at all;
4. `_chain_and_prune()` links consecutive reports of the same target into a track, flags anything
   older than `track_stale_minutes` as `stale` (grey, `?`), and drops it entirely after
   `max(stale × 3, 15)` minutes.

**`stats()`** (cached 5 min) — alert counts and durations from the official feeds; explosions and
shoot-downs from parsed posts tied to a named town; **launch totals** from the Air Force morning
summaries via `parse_af_summary()`, taken as a per-day maximum so a re-posted or corrected summary
cannot double-count; and a separate count of *reports*, which is a volume of posts and is labelled
as such everywhere it appears.

**`tag_feed_text()`** decides what a post is about. `FORECAST_RX` catches nightly assessments
("Загальна оцінка загроз на ніч…", "#обстановка", "може відбутись у будь-який момент") and strips
their threat tags, so a forecast can never light the ballistic banner or place a marker. On startup
the last 6 hours of stored posts are re-tagged with the current rules — a fix to the rules applies
immediately instead of waiting for the bad posts to age out.

**`Pusher`** — sends Web Push for alerts in the watched oblast, all-clears, MiG-31K and ballistic
warnings, and anything heading to the user's chosen place.

### `app/geo.py`

The hard part: Ukrainian is heavily inflected, so a gazetteer of place names needs declension-aware
matching. Entries carry regex stems built per word, with a lookbehind that excludes hyphens — that
is what stops `Коцюбинське` (next to Kyiv) matching inside `Михайло-Коцюбинське` (Chernihiv oblast).

- `parse_post(text)` — the general parser: segments the text, finds places, headings ("курсом на",
  compass words, "→"), counts, and target types; returns candidates with an `evidence` block
  explaining every value it produced. `ALERT_MSG_RX` and `MOVE_RX` guard against reading an alert
  announcement as a sighting.
- `parse_arrows`, `parse_eradar`, `parse_eradar_summary`, `parse_eradar_alert`, `parse_etryvoga`,
  `parse_city_siren` — format-specific parsers for channels that post in a fixed shape.
- `OFFICIAL_PARSERS` — 27 channels mapped to `(kind, uid, fixed raion)`, including 18 city sirens
  that resolve to "<City>ський район".
- `parse_for_channel(channel, text)` picks the right one.

Every parser returns evidence. If a value has no evidence, it is not shown.

### `app/translate.py`

Offline UA→EN: `clean()` strips channel boilerplate and hashtags, then PRE/POST glossaries handle
oblast forms, military vocabulary (KAB, Shahed types) and common phrasings. No network call in the
hot path, so a translation service being down cannot slow the alerts.

### `app/push.py`

Web Push without a library: VAPID ES256 JWT signing, ECDH + HKDF key agreement, AES-128-GCM
(RFC 8291 `aes128gcm`), all on `cryptography` alone. If `cryptography` is missing the module
reports `AVAILABLE = False` and push is simply disabled — the app still runs.

### `static/kyiv.html`

One self-contained page. The map is an SVG in a kilometre projection around Kyiv (`proj` / `unproj`),
with level-of-detail classes (`far`, `veryfar`, `ua`, `close`, `vclose`, `tiled`, `city`) that decide
what is drawn at each zoom. OSM tiles are placed under the vector layers by their own lon/lat corners
below 150 km width, and darkened with a CSS filter.

- `render()` — alerts: an oblast-wide alert always paints the whole oblast; raion alerts can only
  *add* to it, never reduce it (the government app shows the oblast under alert, so this app must
  too).
- `tick()` — markers: clustering of co-located targets with a count badge, loiter orbit for Shaheds,
  heading chevron, staleness classes, label de-collision in screen space, edge indicators for
  off-screen targets.
- `threatStrip()` — the preventive banner, with cancellation and a forecast guard mirroring the
  server's.
- `renderStats()`, `paintDistricts()`, `drawImpacts()`, `updateTiles()`, `layoutTownLabels()`.

`static/i18n.js` holds every UI string in three languages plus place-name tables; see
[I18N.md](I18N.md).

## Data flow timings

| | |
|---|---|
| Alert feed | every 15 s |
| Telegram | every 30 s, 10 s while a missile threat is open |
| Page → `/api/version` | every 15 s, 5 s in missile mode (~60 bytes) |
| Full page reload of state | only when the version ping changes, or every 60 s |
| SSE | pushed immediately on every event |

The version ping exists so that a phone on mobile data during an attack transfers almost nothing
until something actually changes.

## Adding a source

1. Add the channel name to `telegram_channels` in `config.json`.
2. If it posts in a free format, `geo.parse_post` already handles it — check with
   `python app/server.py --demo` and the Feed tab.
3. If it has a fixed format worth exploiting, write a parser in `geo.py`, register it in
   `OFFICIAL_PARSERS`, and add a test in `tests/test_geo.py` with a real post.
4. If it is a mixed news channel, add it to `NEWS_CHANNELS` in `server.py` so only short live-threat
   posts are kept.
