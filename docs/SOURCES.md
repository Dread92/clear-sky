# Sources

Everything Clear Sky shows comes from a public source. Nothing is inferred beyond what a source
said — see [SAFETY.md](SAFETY.md).

## Official alert feeds

| Source | What it gives | Key |
|---|---|---|
| **alerts.in.ua** (primary) | Every active alert at oblast / raion / hromada / city level, the alert type (air raid, artillery, urban fights, chemical, nuclear), red or yellow level, and a `threats[]` detail: drones, ballistic, cruise missiles, MiG-31K take-off, strategic and tactical aviation, KAB, air defence — with the official wording. Plus a month of history for the watched regions. | Free, request at <https://alerts.in.ua/api-request> |
| **api.ukrainealarm.com** | The backend of the official *Повітряна тривога* app. Used as the primary feed when there is no alerts.in.ua token, otherwise as a cross-check. | <https://api.ukrainealarm.com> |
| **ubilling mirror** | Oblast-level on/off only, no start times, no levels. Automatic fallback when no token is configured at all. | none |

An alert from a source that gives no level is treated as a **full red alert**, never as "maybe".

## Telegram channels

Read through the public `t.me/s/<channel>` preview pages — no account, no API key, no scraping of
private content. Posts that are not about the air situation (news, fundraising, culture, ads) are
dropped by `is_relevant()` before they are ever stored.

### Air situation, all Ukraine

| Channel | What it is used for |
|---|---|
| **@kpszsu** | Air Force. Drone and missile reports with headings, ballistic and MiG-31K warnings, all-clears, and the morning summary that gives the only real count of what was launched. |
| **@povitryanatrivogaaa** | The largest monitoring channel. Per-oblast `→A/B (N×)` position lines, explosions, air-defence activity. |
| **@war_monitor**, **@monitor_ukr** | Jet-drone tracking town by town. Also post nightly *assessments*, which are classified as forecasts and never raise a warning. |
| **@UkraineAlarmSignal** (єТривога) | Every raion of the country with its yellow/red level and all-clears, plus per-raion KAB and drone threats. |
| **@eRadarrua** (єРадар) | Per-oblast group counts (`Ударний Бп 3 грп.`) shown as counts on the oblast tags, and live lines (`1 на Велику Димерку`) drawn at the town named. |

### Kyiv

| Channel | What it is used for |
|---|---|
| **@kyivoda** | Kyiv oblast military administration — raion-level alerts with level and threat, turned into real alerts. |
| **@KyivCityOfficial** | Kyiv city administration — drone danger, ballistic, air defence, all-clear. |
| **@kyiv_airdef** | Live play-by-play over Kyiv, one micro-district per post. Consecutive posts are chained into a single track that gives its heading; "Впав / Знижується" closes it at the last position. |
| **@kyiv_times_official** | Mixed news channel — only its short live-threat posts are kept. |

### Surrounding oblasts

**@cherkasy_alerts**, **@poltavskaODA**, **@zhytomyrskaODA** (raion alerts in the @kyivoda format),
**@cherkasy_monitor** and **@sumy_alerts** (live play-by-play, parsed like @kyiv_airdef with the
oblast as context), **@chernihiv_alert**, **@Zhytomyr_alert**, **@sumy_alert** (city sirens).

### City-siren network

One channel per big city, each mapped to that city's raion: **@kharkiv_alert**, **@odesa_alert**,
**@dnipro_alert**, **@lviv_alert**, **@zaporizhzhia_alert**, **@mykolaiv_alert**, **@kherson_alert**,
**@vinnytsia_alert**, **@khmelnytskyi_alert**, **@lutsk_alert**, **@ternopil_alert**,
**@ivanofrankivsk_alert**, **@chernivtsi_alert**, **@uzhhorod_alert**, **@kropyvnytskyi_alert**,
**@kryvyirih_alert**, **@kremenchuk_alert**, **@cherkasy_alert**.

## Adding a channel

Add its name to `telegram_channels` in `config.json` and restart. Free-format posts are handled by
`geo.parse_post`; a fixed format worth exploiting gets a parser in `geo.py` registered in
`OFFICIAL_PARSERS`, plus a test with a real post. See
[ARCHITECTURE.md](ARCHITECTURE.md#adding-a-source).

## Map and geographic data

| Data | Source | License |
|---|---|---|
| Oblast and raion boundaries (`static/kyiv-map.json`) | [geoBoundaries](https://www.geoboundaries.org/) ADM1/ADM2 | CC BY 4.0 |
| Kyiv city districts (`static/kyiv-districts.json`) | OpenStreetMap via Nominatim | ODbL |
| Street and building tiles (zoomed in) | `tile.openstreetmap.org` | © OpenStreetMap contributors |
| Place gazetteer | Built into `geo.py` from public settlement lists | — |

Tiles are fetched by the browser, not by the server, and only below 150 km map width.

## Translation

UA→EN is an offline glossary in `translate.py`: no network call in the hot path, so a translation
service being slow or down can never delay an alert. It is machine translation and is labelled as
such; the original Ukrainian is always one tap away, and is the default in Ukrainian UI mode.
