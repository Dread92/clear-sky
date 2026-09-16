# Changelog

## 1.2.0 — 2026-09-17

### Fixed (safety)
- **A threat is no longer invented when the post does not name one.** A post that says only where —
  "Васильків увага ‼️", "Лісники" — used to be drawn as a Shahed. It is now an amber warning triangle labelled
  *type not stated*, counted separately from drones. The channels' own shorthand still counts as naming it:
  🛸 / 🛵 / 🏍 / 🅿 is a strike drone, 🚀 a missile, 💣 a KAB. The sign stays upright and blinks red, lives five
  minutes and then disappears outright — no grey fade, because there is no target type to keep half-remembered.
  Where the channel's own habits make one type likely it says so as a guess — "⚠ likely a drone", with the reason —
  rather than either hiding the context or stating it as fact.
- **No marker where the source said the sky is clear.** "Чисте небо Київська область та Київ. Васильків увага"
  produced threat markers over Kyiv — a false alarm on a city that had just been declared clear. Clear sentences
  are now removed before anything is parsed, and "чисте небо" is recognised as an all-clear.
- **The stylus no longer moves the map on its own.** A hovering pen sends pointer moves with no contact; the map
  panned on them. Nothing moves now unless something is actually pressed.
- **The details card stayed inside the map.** It flipped left past the middle of a phone screen and was cut off.
  Below 560 px it docks full width at the top or bottom, away from the target; above that it follows the cursor,
  clamped on both axes.

### Changed
- **Cruise and ballistic missiles are the loudest thing on the map**: brighter red with a white edge, larger
  glyph, a double pulse, a faster course ray, and a label that never hides. They fly further, arrive faster and
  carry far more than a Shahed.
- **Channel advertising is stripped from every post** ("Купуємо контент | ❤️", "➡️Оперативно про…", subscribe
  and donation lines). The warning is kept, the ad is not — it was taking the space a warning needs.
- **The feed is condensed**: identical warnings repeated district by district collapse into one card listing the
  places, and a long post shows two lines with *more* to expand.
- **Statistics reduced to what is real**: explosions recorded and confirmed shoot-downs (24 h / 72 h — the window
  the parsing actually covers), what the Air Force reported as launched and shot down (24 h / 7 d / 30 d), and
  explosions per day. The "alerts / 24 h", "under alert" and per-oblast figures are gone; they were double
  counting and nobody used them.
- The drawer opens and closes with a proper tab on its top edge instead of a 3-px pill.
- Ukrainian wording corrected throughout, using the terms the people reading it actually use: *Збито / Приземлено*,
  *Залишив область / Вийшов із зони*, *Локаційно втрачено*, *Позначка не фіксується*. Several strings were plainly
  ungrammatical ("Позиції — де сказав допис"); a glossary of the status words is now in the help page.
- Credit: built by **Dread92**.

## 1.1.0 — 2026-09-17

### Fixed (safety)
- **A descending drone was shown as "confirmed shot down".** `зниження / знижується` — the word the monitoring
  channels use for a drone diving on its target — was matched by the shoot-down keywords, so the most dangerous
  moment of a target's flight was painted green and its track was closed. It is now a live target carrying the
  altitude state `descending`, drawn crimson with a ↓ and a label that survives every zoom level. Only an explicit
  `збито` / `знищено` / `мінус` makes a shoot-down.

### Added
- **Altitude, when the post states it** — descending, climbing, low, high, or a value in metres, with the words it
  came from. Never inferred: no public source publishes altitude.
- **"You are on an old build" notice.** `/api/version` carries a build id; a page that has been open for days
  offers a one-tap reload when the server has moved on.
- **Install as an app (PWA)** — a discreet prompt on Android, the Share → Add to Home Screen hint on iPhone,
  snoozed for 14 days if dismissed, never shown over the welcome screen.
- **Share a target in one tap** — the system share sheet, or the clipboard: type, count, place, time, descent,
  heading, distance from your place, the source channel, the "not radar" caveat and the link.
- **Proximity notifications** — allow your location, choose a radius (5–50 km), and get a push when something is
  reported inside it. One push per target per device, at most one every two minutes, live targets only.
- **Blackout mode** for 2G and weak signal: pure black and white, no map tiles, no images, a much smaller feed
  payload. One accent colour survives — what is heading at you.
- **"How this app works"** — twelve sections explaining the map, the markers, the grey targets, the warning
  banner, the statistics and the notifications, in all three languages.
- **Support the project** — the donation address, a polite line on the welcome screen, and a reminder at most
  once a fortnight that never appears while an alert is running.
- Credits: Black Flame Studio & NGO 07300.

## 1.0.0 — 2026-09-16

First tagged version: the app as it runs today.

### Coverage
- All of Ukraine: 27 oblasts, 136 raions, 36 Telegram channels including the city-siren network,
  єТривога and єРадар.
- Kyiv has its own simplified 10-district map from OSM boundaries.
- Crimea drawn hatched as temporarily occupied, with a pixel-art Cossack and a waving flag.

### Map
- Oblast-wide alerts always paint the whole oblast; raion alerts can only add to them.
- Shaheds drawn loitering with a heading chevron; no trajectory extrapolation unless EST is on;
  no ETA anywhere.
- Co-located targets cluster into one glyph with a count badge; labels de-collide in screen space;
  off-screen targets get edge indicators.
- Stale targets go grey with a `?`, fade in steps and disappear after 15 minutes.
- Street and building tiles from OSM below 150 km width.
- History layer: explosions ✸ and confirmed shoot-downs ✕ over 24 / 48 / 72 h.

### Warnings
- Preventive banner for MiG-31K take-off and ballistic threats, with cancellation and a 20-minute
  cap.
- Nightly threat *assessments* are classified as forecasts: they never raise the banner and never
  place a marker. Stored posts from the last 6 hours are re-tagged at startup.
- Missile mode: 5-second refresh while a ballistic or cruise-missile threat is open.
- Web Push (VAPID / RFC 8291) for the watched oblast and the chosen place.

### Interface
- Full UI in English, Ukrainian and French, including place, raion, district and channel names.
- The entry screen asks for the language, the oblast and, optionally, a precise place to watch.
- Compact status band; scrolling ticker with the basic rules; disclaimer on every open.
- Stats: alerts per day and by time of day, longest alert, explosions and shoot-downs by oblast, and
  what the Air Force reported as launched over Ukraine (24 h / 7 d / 30 d) — kept separate from the
  count of monitoring reports.

### Project
- Restructured into `app/` + `static/` + `tests/` + `docs/`, with VS Code configuration, pytest
  suite, ruff, GitHub Actions CI and an optional Fly.io deploy workflow.
