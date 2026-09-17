# The regression corpus

Everything this app draws comes from patterns matched against free text written by people in a hurry, in two
languages, with declensions. That produces a steady trickle of misreads. In one evening: `знищено` on a
warehouse read as a target shot down, `колом` swallowing a town 700 km away, a government press release
closing a live Shahed track, a stated *north of the oblast* dropped on the oblast centre.

Every one of those was found by a person looking at the map. That is a detection method that does not work at
three in the morning during a mass attack, and it does not scale past one pair of eyes.

A **case** is a real post plus the parse it must produce. `replay` re-parses all of them and reports what
changed, so a fix that quietly breaks another reading fails the build instead of appearing on somebody's
phone during a raid.

## The three states

| state | meaning | enforced |
|---|---|---|
| `pending` | captured; nobody has said whether the recorded parse is right | no — reported as *drift* only |
| `verified` | a human confirmed this is the correct reading | yes — any difference fails |
| `known_bad` | a human confirmed this reading is **wrong**, and it is not fixed yet | in reverse — when it stops reproducing, the suite says so |

`pending` exists so that capturing a bug cannot freeze it as the expected answer. `known_bad` exists so a fix
gets noticed and promoted instead of forgotten.

Even a pending case earns its keep: the press release that closed a live track showed up as drift in two
channels before anyone had labelled it.

## Where cases actually come from

Not a terminal. The person who notices a wrong marker is holding a phone, looking at the map, during a raid —
and a capture step that needs a command line is a capture step that never happens.

So the primary path is the **⚑ button on every marker**. Tap it, pick what is wrong (wrong place, wrong threat
type, not a threat at all, already gone, something else), optionally add a line, send. It changes **nothing** on
the map and says so — a button that silently moved markers would be a way to lie to everyone else looking at
the same screen. It files the post for review.

Flags land on `/admin` under **Flagged readings**, and become corpus cases with:

```bash
python scripts/corpus.py flags            # read them
python scripts/corpus.py flags --import   # turn them into pending cases, mark them handled
```

Nothing identifying is stored: the post, the reading, the reason, the optional note. No address, no location.
The rate limit (40 a day) uses the same throwaway daily hash as the usage counter, which is regenerated every
midnight and cannot be reversed.

That also means the people using this map — the ones who know the ground far better than any parser does — are
the ones finding the misreads, instead of one person checking a map alone.

## Reviewing: on /admin, not in a terminal

The 260-odd pending cases are reviewed from the browser, under **Review readings** on `/admin`. One post, what
this build makes of it, and three answers: *correct*, *wrong* (with a note), *skip*. It works on a phone.

The corpus file ships inside the deployed image and cannot be written from a machine in Amsterdam, so the
verdicts are stored on the volume and come home with:

```bash
python scripts/corpus.py pull      # merge the verdicts into data/corpus.jsonl
python scripts/corpus.py replay
```

`review` in the terminal still exists and does the same thing; use whichever is in front of you.

## Day to day

```bash
python scripts/corpus.py harvest            # add real posts the app has already collected, as pending
python scripts/corpus.py review             # walk the pending ones: y / n / s / q
python scripts/corpus.py replay             # the regression run  (also runs inside pytest)
python scripts/corpus.py stats
```

When something looks wrong on the live map, capture it while you are looking at it:

```bash
python scripts/corpus.py add --channel kyiv_airdef --text "Склад … знищено" \
    --note "this is damage on the ground, not a shoot-down"
python scripts/corpus.py review             # mark it known_bad, then fix it, then re-review
```

That turns "I saw something odd on the map" into a case that can never come back silently.

## What is compared

`project()` in `scripts/corpus.py` is the contract. It keeps, per marker: status, type, place, oblast,
coordinates (2 dp), heading, count, jet, likely, altitude, **position confidence** and quadrant — plus the
post's feed tags and whether it was considered relevant.

It deliberately leaves out the human-readable evidence sentences. Those get reworded for clarity often, and a
corpus that fails on wording is a corpus somebody switches off within a week. Confidence is **in**: `high`
on a guess is a safety bug, not a cosmetic one. Tags are **in**: the MiG-31K banner and the one-second
ballistic cadence are driven by tags, not by markers, and both have had bugs.

## Reviewing well

The question is never "did the app do something reasonable". It is **"is this what the post actually says"**:

- a place named, a type *not* named → unspecified threat, never a drone glyph;
- a destruction word about something on the ground → damage, never a shoot-down;
- policy, money or programme vocabulary → no marker at all;
- a confidence of `high` only when the post named a town the gazetteer knows.

When in doubt, mark it `known_bad` with a note rather than `verified`. A wrong expectation is worse than no
expectation: it makes the suite defend the bug.
