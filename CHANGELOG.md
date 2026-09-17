# Changelog

## 1.10.0 — 2026-09-17

### Added
- **A ⚑ button on every marker: "this reading is wrong".** The person who notices a bad marker is holding a
  phone, looking at the map, during a raid. Every capture path that needed a terminal was a capture path that
  never happened, so the corpus was only ever going to be fed by one person with a keyboard. Now it is one tap:
  pick what is wrong (wrong place · wrong threat type · not a threat at all · already gone · something else),
  add a line if you want, send.
  - It changes **nothing** on the map, and says so on the confirmation. A button that silently moved markers
    would be a way to lie to everyone else looking at the same screen.
  - Flags appear on `/admin` under **Flagged readings**, with the original post beside the complaint, and
    become regression cases with `python scripts/corpus.py flags --import`.
  - **Nothing identifying is stored**: the post, the reading, the reason, the optional note. No address, no
    location. The 40-a-day rate limit reuses the usage counter's throwaway daily hash — regenerated every
    midnight, not reversible, never returned by the API.
  - `/api/flags` needs `ACCESS_KEY` and refuses to serve anything when none is set, like the dashboard.
  - EN / UA / FR.

The point of this one is not the feature. It is that the people reading this map know the ground far better
than any parser does, and until now there was no way for them to say so.

## 1.9.1 — 2026-09-17

### Fixed (safety)
Three misreads in one post — an oblast administration's account of a working visit to a forestry enterprise,
drawn as a **fire, 9 km from Kyiv, at high confidence**.

- **A fire needs a fire, not a fire station.** The post was about a *new fire station* and its *fire engines*.
  The root `пожеж` is in the name of every fire service, engine and brigade in the country, so it can never by
  itself mean something is burning. A fire is now only drawn when the sentence says one happened — виникла /
  сталася / спалахнула пожежа, пожежа в …, горить, займання.
- **A post that says there is no fire no longer draws one.** "Пожежі попередньо немає" drew a fire. Worse,
  fixing that naively would have cost the post its damage marker — rejecting the fire reading now falls
  through to the next rule instead of abandoning the post, so the damage it does report still appears.
- **The news guard covers every status, not only the two caught first.** The fire rule ran before the check,
  so a 900-character press release sailed past it.
- **An oblast adjective is not the Kyiv place of the same name.** "Житомирська" is a metro station on Kyiv's
  red line *and* how every post names Zhytomyr oblast — which is why a post about Olevsk, 150 km away, planted
  a marker beside Kyiv. When an oblast noun follows (область, обласна рада, ОВА), the word is the oblast.

All four are in the corpus as verified cases, plus seven hand-written tests. The corpus caught the second one
by itself: it surfaced as drift on a real post nobody had labelled yet.

## 1.9.0 — 2026-09-17

### Added
- **A regression corpus** (`scripts/corpus.py`, `tests/corpus/cases.jsonl`, `docs/CORPUS.md`). Real posts plus
  the parse they must produce, replayed on every test run. The parser is patterns over free text in two
  languages with declensions; it produces a steady trickle of misreads, and until now every one of them was
  found by a person looking at the map — a detection method that does not work at three in the morning during
  a mass attack.
  - `harvest` takes posts the app has already collected (no network), capped per channel.
  - `review` walks them: **verified** is enforced, **known_bad** is enforced in reverse so a fix gets noticed
    and promoted instead of forgotten, **pending** is only reported — capturing a bug must never freeze it as
    the expected answer.
  - `replay` is the regression run, and `tests/test_corpus.py` puts it in the ordinary suite.
  - What is compared is a deliberate projection: status, type, place, oblast, coordinates, count, altitude,
    **position confidence**, quadrant, and the post's feed tags. Not the evidence wording — a corpus that
    fails on rephrasing is one somebody switches off within a week.
  - Seeded with 279 cases: every misread found today (each as the *correct* reading), the doctrine rules
    they came from, and a real sample from 36 channels.

## 1.8.1 — 2026-09-17

### Fixed (safety)
- **A government press release removed a live Shahed from the map.** "Уряд розширив програму страхування
  воєнних ризиків … за пошкодження або знищення якого можна отримати компенсацію" — an announcement about
  business insurance — was read as damage on the ground, drawn over Kyiv, and then **closed a live drone track
  with it**. Two rules now stand there:
  - Policy vocabulary (уряд, кабмін, законопроєкт, страхування, компенсація, відшкодування, бюджет, пільговий
    кредит, млн/млрд грн) or a post longer than 700 characters disqualifies an outcome entirely. No marker, no
    status, whatever destruction words the text happens to contain.
  - **Only an outcome that ends a flight may close a track** — shot down, arrived, lost, area clear. Damage and
    fire never close anything: they are what a strike left on the ground, and a burning roof says nothing about
    whether the drone above it is still flying.
  The second rule is the important one. A missed damage marker costs some information; a live target removed
  from the map by a press release is the failure the safety doctrine exists to prevent.
- **The last two "undefined" leaks** — the detail sheet's title and the share text — now use the same labelled
  fallback as the card. Every status, including any added later, has a name.

## 1.8.0 — 2026-09-17

Four misreads spotted on the live map, all the same failure underneath: the app stated more than its source
supported. All four are pinned in `tests/test_misreads.py`.

### Fixed (safety)
- **A destroyed building was drawn as a target shot down.** "Склад гуманітарного фонду … знищено" put a green
  interception tick over a strike site — the opposite meaning — and padded the shoot-down count with somebody's
  ruined warehouse. A destruction word now only reads as a shoot-down when the sentence is about something that
  was flying. Otherwise it is **damage on the ground**: its own muted brick marker, its own column, never added
  to shoot-downs. "Збито 5 БпЛА, уламки пошкодили будинок" is still a shoot-down.
- **Five Shaheds were drawn 700 km from where they were.** Stemming cut *Коломия* to `колом`, which swallowed
  **Коломак** — another town, another oblast. Two guards now: an over-stemmed name must still agree with the
  full name one character past the stem, and **a marker's oblast is the oblast of the place it matched**, never
  the one the sentence mentions. That contradiction — Kharkiv's oblast id with Ivano-Frankivsk's coordinates —
  was visible inside the app before it was visible on the map. No real declension was lost; the whole list is
  tested.
- **"На півночі Київщини" was drawn at the oblast centre**, which sits near Vasylkiv, in the south. A stated
  part of an oblast now places the marker there and the card says *north of the oblast*. Confidence stays low —
  a quadrant is not a position — and course words ("у західному напрямку") still never move anything.
- **A status with no label printed the words "undefined undefined"** on the card. Every status now has a name
  in all three languages, and an unknown one falls back to a plain label instead of leaking a variable.

### Added
- **Damage** as a first-class outcome: `▣` marker, own chip, own column in the report and the statistics,
  labels in EN / UA / FR.

## 1.7.0 — 2026-09-17

### Added
- **A history report behind the counter chips.** The map now carries **the last 24 h only** — beyond that the
  history layer buries the live picture, which is the one thing somebody opening the app during an attack needs
  to see. Tapping the ✸ / ✕ / 🔥 chips opens a report with a 24 h / 72 h / 7 d selector: totals per kind, then
  a list grouped by place — "Kyiv ×12 · last 03:41" says what two hundred dots were trying to say. It fetches
  its own window, so the live layer is never reloaded at seven days, and it repeats under its own figures that
  these count posts, not events.
- **Fires are drawn as fires.** A post that says only *пожежа / горить / загорання* is a fire, not an
  explosion: a small amber flame, drawn quietly, counted in its own column and never added to the explosion
  total. The rule sits below the impact rule on purpose — "внаслідок влучання виникла пожежа" is a strike that
  started a fire and still reads as a strike, and a shoot-down whose debris is burning stays a shoot-down.
- **A fire leaves the live map after an hour** (`FIRE_TTL_MIN`). After that it is either out or it has been
  burning all night; a new post about the same place brings it back, because that post carries a fresh
  timestamp.

### Fixed
- **The livebar chips were not tappable at all.** The bar carries `pointer-events:none` so the map can be
  dragged through it — which silently disabled every button placed inside. Only the chips take taps now; the
  gaps between them still pass through to the map.

### Changed
- The Ukrainian and English chips say *reports* rather than a bare count of explosions, matching the stats
  panel.
- `/api/impacts` accepts up to 7 days (was 96 h) for the report; the map never asks for more than 24 h.

## 1.6.1 — 2026-09-17

### Changed
- **"Confirmed shot down: 310" was claiming more than the app can know.** It counts the monitoring posts the
  parser could read and tie to a named town — every shoot-down never posted, posted without a place, or
  written in a form the parser missed is missing from it. The two figures are now **Explosion reports** and
  **Shoot-down reports**, with the caveat directly under the numbers instead of in a footnote at the bottom
  of the panel: what this app read, not official totals, and the real numbers are higher, never lower. The
  Air Force summary table below them is unchanged — those *are* counts of targets. The map chips say
  "reported" too. A floor presented as a total is false precision in the same family as an invented
  trajectory, and `docs/SAFETY.md` now says so.

## 1.6.0 — 2026-09-17

### Added
- **Odesa live tracking — @xydessa_live.** A local channel that posts the way people watching the sky actually
  write: a bare district name, a count, *реактивний* for a jet Shahed, half of it in Russian, and `-1` when one
  comes down. It is registered as a live-position channel for Odesa oblast, with the safety rules that implies:
  - a **bare district name is a position, never a threat type** — it draws the amber *type not stated* triangle,
    with "most likely a drone" carried separately as a guess;
  - **Russian spellings resolve to the same place** (черноморск → Чорноморськ, Аркадия → Аркадія, поскот →
    Селище Котовського), because half the posts are in Russian;
  - **`-1` is never read as anything.** It means one was brought down, but with no place attached — and a
    shoot-down drawn at a guessed position is worse than no shoot-down at all;
  - the channel's promo tail (one of the two lines is profane) is stripped before anything reaches a warning.
- **Nine Odesa places in the gazetteer**, coordinates from GeoNames: Пересип, Лузанівка, Селище Котовського,
  Аркадія, Хаджибейський лиман, Санжійка, Татарбунари, Тузли, Маяки.
- `tests/test_odesa.py` — 14 tests pinning all of the above, including what must *not* be concluded.

### Changed
- **MiG-31K is recognised however the channel declines it** — "Мігну31к в небе" is the same warning as
  "МіГ-31К". The pattern stays tight (міг/миг/mig, then 31 within three letters) because it raises the loudest
  banner in the app; ordinary words like *мігрант* and *мигдаль* are tested not to.
- Kalibrs are recognised in the Russian spelling as well (*калибы*).

### Known gaps in Odesa coverage
- **Південне is deliberately not matched.** It is both a town near Odesa and the adjective *southern*, and the
  channel uses both. Until a post can be told apart from "курс південний", neither draws a marker: a wrong pin
  30 km up the coast is worse than a missing one.
- **Слобідка, Іллічанка, Латівка and Паланка are not in the gazetteer** — no authoritative coordinate was
  found for them. Their posts stay in the feed with no marker until someone supplies one.

## 1.5.1 — 2026-09-17

### Fixed
- **The MiG-31K banner stopped shouting in the present tense long after the last report.** It is the loudest
  thing in the app, so it now only speaks in the present while the report behind it is fresh: for the first
  8 minutes it reads *MiG-31K airborne — ballistic risk · 02:33 · 4 min ago*, and after that it switches to the
  past tense, loses the alarm colours and the pulse, and says *not confirmed for 16 min*. It still disappears
  at 20 minutes. It is demoted rather than deleted, because a sortie can still end in a launch and silence is
  not an all-clear. **The age is now always on its face** — a banner with only a start time reads as *now*,
  whatever the clock says. Freshness also outranks type in the sort, so an unconfirmed MiG from a quarter of an
  hour ago can no longer sit on top of a ballistic warning that came in a minute ago.
- **The 24-hour history no longer looks like fireworks at the all-Ukraine view.** The merge distance and the
  glyph size now both follow the zoom (26→42 px, full size→50 %), so a heavy night's hundred reports become
  about thirty dots carrying their counts instead of a hundred full-size starbursts. History markers are also
  no longer flung onto a decluttering ring: at that zoom 26 px is 90 km, and a dot that far from the town it
  happened in is worse than a slight overlap. They nudge at most 13 px now, and otherwise stay where they were
  reported.
- **The safety ticker was clipped and unreadable.** The 55-second marquee is gone. It is now one short sentence
  at a time, cross-fading every 7 seconds, in a fixed two-line box that cannot clip its own descenders or make
  the header jump. Tapping it opens the full disclaimer. The five lines were rewritten in all three languages
  (the Ukrainian ones want a native review).

### Changed
- **The menu is grouped instead of a flat list of seventeen rows**: Alerts · Map · Display · About, with
  Sound/Vibration on one row and the three zoom levels on another. It scrolls if it does not fit the screen,
  and the version and build are on one line.
- **Community on Telegram** in the menu and the footers, pointing at t.me/blackflamestudio.

## 1.5.0 — 2026-09-17

### Changed
- **Missiles are no longer aged like drones.** Going grey after five minutes of silence is honest for a Shahed
  (~3 km/min); for a cruise missile (~13 km/min) or a ballistic one (~35 km/min) a two-minute-old pin is already
  tens of kilometres from the truth, and leaving it there is a lie with a precise point in it. So a missile never
  gets the grey *stale* flag at all. For a short window after the report — 2.5 min cruise, 1.5 min ballistic — the
  position still counts as a point. After that the marker grows a ring at that family's speed and reads
  **"could be anywhere within N km · last seen \<place\> at HH:MM"**. The ring is capped at 260 km, and missiles
  are dropped from the map at 12 minutes instead of 15, because past that even a circle says nothing.
- **Ballistic now looks different from cruise at a glance.** A white-hot downward spike, blinking twice as fast,
  with no course ray — a lofted trajectory cannot justify one — against the red cruise arrow with its capped ray
  and uncertainty cone. The time you have is different for the two; the marker should say so without being read.
- The threat sheet explains both, in all three languages: what the circle means, why a missile has no "last
  updated" state, and that nothing on the screen estimates where a missile *is*.
- **One-second refresh while a ballistic threat is open.** The cadence is now a level the server decides
  (`/api/version.msl`): 15 s normally, 5 s for cruise missiles or a MiG-31K, **1 s for ballistic**. The server
  speeds its own sources up with it — Telegram and the alert APIs go to 5 s at that level — because a
  one-second client in front of a fifteen-second poller only refreshes a stale answer faster. At 1 s the
  countdown is replaced by a pulsing **LIVE**, since a number flickering between 1 and 0 reads as a fault.
- **The uncertainty ring now grows every second**, not only when new data arrives. A ballistic missile adds
  about half a kilometre of doubt per second; a circle that sat still between updates would read as a known
  position. Only missile markers are repainted on that tick — a phone on 2G does not re-lay out forty markers
  a second.
- **Overlapping explosions are grouped instead of stacked.** Anything closer than one glyph-width on screen
  becomes a single dot with the total (**×8**), anchored and labelled by its most recent member — never a
  centroid, which would put a dot where nobody reported anything. Explosions and confirmed shoot-downs are
  grouped apart from each other; they mean opposite things. It is a function of zoom, so zooming in pulls the
  group back into its separate reports, and tapping one opens the full list — place, time, channel and post for
  every report behind it, with a Zoom in button. The ticker and statistics are counted before grouping, so what
  is drawn never changes a number. This replaces the old behaviour of fanning them onto a ring, which moved
  each dot off the place it was reported.

### Added
- A **Missiles are treated differently** section in *How Clear Sky works*, in all three languages.
- `docs/SAFETY.md`: why missiles are aged apart from drones, what the uncertainty ring claims, and the
  refresh-level table.
- `poll_telegram_ballistic_seconds` (default 5) in `config.example.json`.
- Regression tests: a missile never receives `stale`, always carries `fast`, and is gone at 12 minutes
  (`tests/test_prune.py`); the missile level, the source-poll shortening and its failure behaviour, and that
  pings are batched rather than committed one by one (`tests/test_cadence.py`).

### Fixed
- **Usage counting could have become the bottleneck it was measuring.** Every ping committed its own SQLite
  transaction; at one ping a second per device that is thousands of writes a second through a single
  connection, during a ballistic alert, competing with the alert path. Pings are now counted in memory and
  written in batches (at 200, or every 20 s), with the unwritten tail included in the dashboard's numbers.

## 1.3.0 — 2026-09-17

### Added
- **A private usage dashboard at `/admin`** — devices per day, app opens, push subscribers, language split,
  how many run it as an installed app. It needs `ACCESS_KEY` and is not served at all when no key is set.
  **Nothing identifies anybody**: a device is counted once a day through a one-way hash of address and browser,
  salted with a value thrown away and regenerated every midnight, so the same person cannot be followed from one
  day to the next and no hash can be reversed. No address is stored, no location, no path through the app, no
  per-person history — only the daily totals. `tests/test_usage.py` pins those properties.
- The footer now sits under every tab of the panel, not only the Map tab: the free-to-use line, the two support
  destinations, the community link once configured, and © 2026 Black Flame Studio & NGO 07300 · Developed by
  Dread92 · v1.2.

### Changed
- **The welcome screen now uses proper native wording in all three languages**, replacing my own translation:
  the unofficial-volunteer framing, "these are NOT radar data", the main rule about city sirens and the official
  app, and the line about never sharing air-defence footage or impact locations during an attack.

## 1.2.1 — 2026-09-17

### Fixed
- **Updates could stop altogether.** Every request now has a deadline, so a fetch stalled on a weak cell can no
  longer wedge the poller with `pulseBusy` stuck true — the failure nobody would notice until it mattered. A
  watchdog checks every second that a round actually completed, and restarts one if not; coming back to the app,
  regaining network or refocusing the tab all trigger an immediate refresh. The countdown turns red when the last
  round failed. Cadence measured: 15 s normally, 5 s while missiles are up.
- **Markers stacked on top of each other.** Anything landing within 26 px of something already drawn steps onto a
  ring around it — 6 slots, then 12, then 18 — with a thin leader line back to a dot at the true position, so the
  glyph moves but the reported place stays honest. Live targets keep their spot first; history gives way.
- **The same village hit nine times is one marker with ×9**, not nine glyphs buried on top of each other.
- The drawer's open/close tab is centred and in the app's yellow; it was tucked in a corner over the map icons.

### Changed
- **Explosions are brighter**: a white-edged glyph with a red glow, and a shockwave on the freshest ones.
- **Altitude is read the way the channels write it** — "2200 висота" as well as "висота 2200", and a follow-up
  post that is only a number and a place ("1600, Вороньків"). A stated height is treated as a strong hint of a
  drone, shown as *likely a drone*, never as the type itself. A road number or a small count is not a height.
- **Footer**: hosting & development and NGO 07300 as separate destinations, a community link that only appears
  once configured, and a version tag — © 2026 Black Flame Studio & NGO 07300 · Developed by Dread92 · v1.2.
  Contacts live in one `CONTACT` block at the top of the page script.

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
