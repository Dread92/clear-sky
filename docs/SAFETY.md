# Safety rules the code follows

People may look at this map while deciding whether to go to a shelter. That single fact decides most
of the design. These rules are not style preferences — breaking one is a bug of the worst kind, and
a pull request that breaks one will not be merged.

## The app is never the primary warning

The official *Повітряна тривога* app and the sirens come first, always. Clear Sky adds detail to a
warning that has already been given; it never replaces it. The disclaimer is shown on **every**
open, not once, and the scrolling ticker repeats the essentials while the app is in use.

## No liberties with trajectories

A target is drawn **where a post said it was**. Nothing else.

- Shaheds are drawn *loitering* around the reported point, because their real path is erratic and a
  straight arrow would imply a precision nobody has. The reported heading is shown as a chevron —
  a direction someone reported, not a course line.
- Dead reckoning along a heading exists only behind the explicit **EST** switch, off by default,
  labelled as an estimate. A drone that turned after the report would otherwise be drawn somewhere
  it is not.
- **No ETA is ever displayed.** "Arrives in 4 minutes" is a number the data cannot support, and
  someone would wait for it.
- The course ray is capped and drawn with an uncertainty cone, never as a line to a destination.

## Stale is never shown as active

Silence is not safety, and it is not danger either — it is silence, and it must look like it.

| Age without a new report | What the map shows |
|---|---|
| < 5 min (`track_stale_minutes`) | Live: coloured, animated, labelled |
| ≥ 5 min | Grey, a `?` above the marker, "not updated for N min", animation stopped |
| ≥ 8 min, ≥ 11 min | Fades further, labels drop away |
| ≥ 15 min (or the oblast's alert ends) | Removed |

A grey marker is a record of the last thing known, not a target's current position, and the sheet
says so in those words.

## A missile is not aged like a drone

The table above is written for a Shahed: about 3 km a minute, so a five-minute-old dot is still worth
something, and going grey is an honest way to say *this is the last thing anyone reported*. Applying the
same rule to a missile would be a lie with a precise pin in it. A Kalibr or a Kh-101 covers roughly 13 km
a minute and turns as it goes; a ballistic covers around 35 and is on its terminal leg before a second
report could exist. Two minutes of silence already puts the marker tens of kilometres from the truth.

So missiles are handled apart:

- **They never get the grey `stale` flag.** There is no age at which "last seen here, not updated" is a
  useful thing to say about something moving that fast.
- **The pin becomes a circle.** For a short window after the report — 2.5 min for cruise, 1.5 for
  ballistic — the position is still worth a point. Past that, the marker keeps the last reported position
  as its origin and grows a ring at the speed of that missile family, labelled *could be anywhere within
  N km · last seen <place> at HH:MM*. The ring is the claim; the point underneath it is only where the
  post put it. The ring is capped at 260 km, because past that it covers half the country and stops
  meaning anything.
- **They are dropped at 12 minutes** (`MISSILE_TTL_MIN`), not 15. By then the last position tells you
  nothing at all, even as an uncertainty circle.
- **No course ray on a ballistic.** A cruise missile flying a reported heading can justify a capped ray
  with a cone; a ballistic one on a lofted trajectory cannot, and the ray is hidden for that type.

Ballistic and cruise are also drawn differently on purpose — a white-hot fast-blinking spike against the
red cruise arrow — because the time you have is different and the marker should say so at a glance,
without being read.

Everything on the screen still comes from a post. Nothing here estimates where a missile *is*; the circle
is an explicit statement of how little the last report now constrains it. It also keeps growing between
server updates — at ballistic speed a circle that only moved when new data arrived would sit perfectly
still exactly when it matters, and a still circle reads as a known position.

### How often the app looks

Refresh rate is a level, not a setting:

| Level | When | Client | Telegram and the alert APIs |
|---|---|---|---|
| 0 | nothing missile-shaped | 15 s | 30 s / 15 s |
| 1 | cruise missiles or a MiG-31K up | 5 s | 10 s |
| 2 | a ballistic threat is open | **1 s** | 5 s |

Both ends move together on purpose. A client polling every second in front of a server that last read its
sources fifteen seconds ago is theatre: the app can only ever be as fresh as the slowest link, and that
link is the source poll. Level 2 is rare and lasts minutes, which is the only reason a one-second cadence
is affordable at all — and the ping counters are batched in memory rather than committed one by one, so
counting usage can never be what stalls the alert path during an attack.

At that cadence the countdown is replaced by **LIVE**, because a number flickering between 1 and 0 reads
as a fault rather than as speed. It turns red the moment a round actually fails, which is the only thing
there worth noticing.

## A warning is only raised when something was actually reported

The preventive banner (MiG-31K airborne, ballistic threat) is the loudest thing in the app, so what
raises it is deliberately narrow:

- **Raises it:** an official alert carrying that threat type; a post that says a launch was recorded
  (`пуск`, `запуск Іскандер/Кинджал`), a fast target (`швидкісна ціль`), ballistics named against a
  city, or a MiG-31K take-off.
- **Never raises it:** a nightly *assessment* — "Загальна оцінка загроз на ніч…", "#обстановка",
  "Запуск може відбутись у будь-який момент", "ймовірні додаткові запуски". These are forecasts.
  They are tagged `forecast`, stripped of their threat tags on the server, and filtered again on the
  client. Tested in `tests/test_tagging.py`.
- The banner **cancels itself** as soon as a later post lifts it (`відбій`, `посадка`, "загроза
  минула") and never outlives 20 minutes without a new report.

The same logic applies to markers: an *announcement* of an alert never becomes a target on the map,
and a missile is only drawn if a place or a heading was actually reported.

## Alert colours must match the official app

If the government app shows an oblast under alert, this map shows the whole oblast under alert. An
oblast-wide alert always paints the oblast; raion-level alerts can only **add** to it (a red raion
inside a yellow oblast), never reduce or replace it. Getting this wrong — showing "clear" where the
state says "alert" — is the single most dangerous bug this app can have.

## Altitude is read, never guessed

No public source publishes target altitude. So the app shows it **only when a post states it in words**, and
shows nothing at all otherwise — never a default, never an estimate, never "level flight".

What the channels do say is read and shown:

| The post says | The map shows |
|---|---|
| `знижується`, `зниження`, `заходить на ціль` | ↓ **DESCENDING** — the marker turns crimson and keeps its label at every zoom |
| `набирає висоту` | climbing |
| `низько`, `на малій висоті` | low |
| `на висоті 2000м` | ~2000 m |

A descending drone is the most dangerous state there is: it is diving at something. Until version 1.1 the word
`зниження` was matched by the *shoot-down* keywords, so a drone in its attack dive was drawn green as "confirmed
shot down". It is now a live target, and a post has to say `збито` / `знищено` before anything is called a
shoot-down. `tests/test_geo.py` pins both halves of that.

## A threat type is never invented

If the post does not say what is flying, the map does not say it either: the marker is an amber warning triangle
labelled *type not stated*, counted apart from drones and missiles. "Васильків увага ‼️" tells you where to worry,
not what to worry about, and a Shahed icon there would be a claim nobody made.

Its sign stands upright and blinks red, and it never turns with the reported heading — a warning sign that is
rotated 200° reads as a decoration, not a warning; the heading is shown by the chevron beside it. It lives five
minutes and then disappears outright, with none of the grey fading a drone track gets: there is no target type
to keep half-remembered.

The channels' shorthand does count as naming it — 🛸 🛵 🏍 🅿 for a strike drone, 🚀 for a missile, 💣 for a KAB —
because that is how those channels write, every night.

## A fire is not an explosion, and it does not last all night

A burning roof reported an hour after a raid is a consequence somebody saw, not a strike. Drawing it as the
red starburst inflates the explosion count and puts a strike marker where no strike was reported. So a post
that says only fire — *пожежа*, *горить*, *загорання* — gets its own small amber flame, drawn quietly, counted
in its own column and never added to explosions. The rule sits **below** the impact rule on purpose: "внаслідок
влучання виникла пожежа" is a strike that started a fire and reads as a strike, and a shoot-down whose debris
is burning stays a shoot-down. The more serious reading always wins.

A fire also **leaves the live map after an hour** (`FIRE_TTL_MIN`). After that it is either out or it has been
burning all night, and neither belongs on a live map as though it had just happened. A new post about the same
place brings it back, because that post carries a fresh timestamp.

## The history layer never buries the live picture

The map carries the last 24 hours and nothing more. Beyond that the dots are denser than the thing somebody
opened the app to see, and a person deciding whether to go to a shelter should not have to read three days of
history to find what is flying now. Longer windows — 24 h, 72 h, 7 days — live in a report behind the counter
chips, grouped by place: "Kyiv ×12, last at 03:41" says what two hundred dots were trying to say. The report
repeats, under its own figures, that they count posts and not events.

## What was destroyed decides what it means

"Склад гуманітарного фонду … знищено" is a warehouse on the ground. Read as a shoot-down it became a green
tick over a strike site — the opposite of what happened — and it padded the interception count with somebody's
ruined building. A destruction word only reads as a shoot-down when the sentence is about something that was
flying (`ціль`, `БпЛА`, `шахед`, `ракета`). Otherwise it is **damage on the ground**: its own muted marker, its
own column, never added to shoot-downs. "Збито 5 БпЛА, уламки пошкодили будинок" is still a shoot-down; debris
damaging a roof, with nothing said about what was intercepted, is damage.

## A press release is not an observation of the sky

"Уряд розширив програму страхування воєнних ризиків … за **пошкодження** або **знищення** якого можна отримати
компенсацію" is a government announcement about insurance. It put a damage marker over Kyiv and — worse —
**closed a live Shahed track with it**, because anything carrying an outcome was allowed to end a flight.

Three rules now:

- **An article produces no outcome of any kind.** Policy vocabulary — уряд, кабмін, законопроєкт, страхування,
  компенсація, відшкодування, бюджет, пільговий кредит, млн/млрд грн — never appears in somebody reporting what
  is overhead, and neither does a post of more than 700 characters. Such a post produces no marker and no
  status, whatever destruction words it contains. The guard covers **every** status: a visit to a new fire
  station drew a fire marker beside Kyiv because the fire rule ran before the news check.
- **A fire needs a fire, not a fire station.** The root `пожеж` is in the name of every fire service, engine
  and brigade in the country, so it cannot by itself mean something is burning. A fire is only drawn when the
  sentence says one happened — *виникла / сталася / спалахнула пожежа*, *пожежа в …*, *горить*, *займання*. A
  post that says **"Пожежі попередньо немає"** draws no fire — and still keeps the damage marker its text
  earns, because rejecting the fire reading falls through to the next rule rather than abandoning the post.
- **Only an outcome that ends a flight may close a track**: shot down, arrived, lost from tracking, area
  declared clear. **Damage and fire never close anything** — they are what a strike left on the ground, and a
  burning roof says nothing about whether the drone above it is still flying. A live target must never vanish
  from the map because of something that happened underneath it.

The second rule matters more than the first. A missed damage marker costs a user some information; a live
target removed from the map by a press release is the failure this whole document exists to prevent.

## A name that merely starts the same is not a match

Stemming strips trailing vowels, so *Коломия* was indexed as `колом` — which quietly swallowed **Коломак**, a
different town 700 km east. Five Shaheds were drawn over Ivano-Frankivshchyna from a post about Kharkiv oblast.
Two guards now stand there:

- When the stem gave up two or more letters, the matched word must still agree with the full name one
  character past the stem. *коломиї* does; *коломак* does not. Alternation spellings (*фастів→фастов*,
  *Київ→києв*) are explicit entries and are trusted as written, so no real declension was lost.
- **An oblast adjective is not the Kyiv place of the same name.** "Житомирська" is a metro station on Kyiv's
  red line *and* how every post names Zhytomyr oblast. Read as the station, a forestry post about Olevsk —
  150 km away — planted a marker 9 km from Kyiv. When an oblast noun follows the word (область, обласна рада,
  ОВА), the word is the oblast and nothing matches.
- **A marker's oblast is the oblast of the place it actually matched**, never the one the sentence mentions.
  The Kolomyia marker carried Kharkiv's oblast id and Ivano-Frankivsk's coordinates at once; that contradiction
  was visible inside the app before it was visible on the map.

## A stated part of an oblast is not the oblast centre

"Реактивні БпЛА **на півночі** … Київщини" was dropped on the oblast centre, which for Kyiv oblast sits near
Vasylkiv — in the south, on the wrong side of the city from the reported drones. When a post names a part of an
oblast, the marker goes there and the card says *north of the oblast* instead of *oblast centre*. The
confidence stays **low**, because a quadrant is still not a position.

Only locative phrasing counts. "У західному напрямку" is a course, not a place, and never moves a marker —
discarding a stated quadrant and inventing one are the same failure in opposite directions.

## Nothing is drawn where the source said it is clear

Posts mix an all-clear and a warning in one breath: "Чисте небо Київська область та Київ. Васильків увага ‼️".
Clear sentences are removed before parsing, so only Vasylkiv gets a marker. A false alarm over a city that was
just declared clear is the fastest way to make people stop trusting the map — and then stop reading it at all.

## Overlapping explosions are grouped, never stacked

At a 300 km view two explosions 3 km apart are the same eleven pixels. Drawing both put two glyphs on top of
each other and two labels in the same space; pushing them onto a ring, the earlier fix, moved each dot off the
place it was actually reported. Both are worse than one dot saying **×8**.

So at draw time anything closer than one glyph-width merges into a single marker carrying the total:

- The group is **anchored and labelled by its most recent member**, never by a centroid. A centroid puts a dot
  on a spot where nobody reported anything, which is exactly the kind of invented precision this map avoids.
- **Explosions and confirmed shoot-downs are never merged into each other.** They mean opposite things, and are
  grouped separately even when they sit on the same street.
- It is purely a function of zoom. Zooming in pulls the group apart into its separate reports, and a tap lists
  every one of them with its own place, time, channel and post — no single report ever stands in for the others.
- The counts in the ticker and the statistics are computed before grouping, so what is on the screen never
  changes a number.

## Every number is traceable

Tap any marker and you see the sentence it came from, the channel, the time, and how position,
heading and count were read, each with a confidence. If a value has no evidence, it is not shown.
"Machine-read — always check the sentence" is on the sheet, because the source can be wrong too.

In the statistics, **launches** (from Air Force summaries) and **reports** (a count of posts) are
never mixed and never added together. A number that counts posts is labelled as counting posts.

That labelling is not cosmetic. "Confirmed shot down: 310" reads as *310 things were shot down*, which the
app has no way to know: it counts the monitoring posts it could parse and tie to a named town. Every
shoot-down that was never posted, posted without a place, or written in a form the parser missed is absent
from it. So these figures are named **explosion reports** and **shoot-down reports**, and the caveat sits
directly under the numbers rather than in a footnote at the bottom of the panel: what the app read, not
official totals, and the real numbers are higher — never lower. A floor presented as a total is a false
precision in the same family as an invented trajectory.

## Operational security

The app does not encourage publishing air-defence positions or explosion locations during an
attack — the disclaimer and the ticker say so explicitly. The history layer only shows what was
already public, only at the level of a named town, and only after the fact.

## When in doubt

Show less. A missing marker costs a user some information. A confidently wrong marker, a false
all-clear, or an invented trajectory can cost something else.
