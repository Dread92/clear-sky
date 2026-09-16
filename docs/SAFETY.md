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

## Nothing is drawn where the source said it is clear

Posts mix an all-clear and a warning in one breath: "Чисте небо Київська область та Київ. Васильків увага ‼️".
Clear sentences are removed before parsing, so only Vasylkiv gets a marker. A false alarm over a city that was
just declared clear is the fastest way to make people stop trusting the map — and then stop reading it at all.

## Every number is traceable

Tap any marker and you see the sentence it came from, the channel, the time, and how position,
heading and count were read, each with a confidence. If a value has no evidence, it is not shown.
"Machine-read — always check the sentence" is on the sheet, because the source can be wrong too.

In the statistics, **launches** (from Air Force summaries) and **reports** (a count of posts) are
never mixed and never added together. A number that counts posts is labelled as counting posts.

## Operational security

The app does not encourage publishing air-defence positions or explosion locations during an
attack — the disclaimer and the ticker say so explicitly. The history layer only shows what was
already public, only at the level of a named town, and only after the fact.

## When in doubt

Show less. A missing marker costs a user some information. A confidently wrong marker, a false
all-clear, or an invented trajectory can cost something else.
