# Contributing

This is a small personal project with an unusual constraint: people may use it while deciding
whether to take cover. Read [docs/SAFETY.md](docs/SAFETY.md) first — those rules override
convenience, cleverness and features.

## Before changing anything in `app/`

- [docs/TECHNICAL.md](docs/TECHNICAL.md) — the complete technical reference: how the pieces fit, every
  route, setting, table and source.
- `python -m pytest` must pass. Parsing changes need a test built from a **real post**.
- A new warning path (anything that can raise the red banner or place a marker) needs a test for the
  false-positive case too: the post that looks similar but must *not* trigger it.

## Local loop

```bash
pip install -r requirements-dev.txt
python app/server.py --demo --port 8099    # fake alerts, no token
python -m pytest
python -m ruff check app tests
```

The demo mode generates plausible alerts and posts, so the whole UI can be exercised without keys
and without waiting for an attack.

## Style

- Python: standard library only in `app/` (the one exception is `cryptography`, and the app must
  still run without it). Lines up to 160 characters. Comments explain *why*, especially where a rule
  exists for a safety reason.
- Front end: no build step, no framework, no CDN dependency at runtime. `static/kyiv.html` stays one
  self-contained file.
- Every user-visible string goes through `t()` in all three languages — CI enforces it.
- Every patch follows the checklist in [docs/TECHNICAL.md §18](docs/TECHNICAL.md#18-the-patch-checklist):
  version bump, a `CHANGELOG.md` entry, and the technical documentation updated in the same commit.
  `tests/test_docs.py` fails when they drift apart.

## Commits

Say what changed and why it matters, in one line:

```
fix: nightly threat assessments no longer raise the ballistic banner
```

## Reporting a parsing miss

Open an issue with the original Ukrainian text, the channel, what the app did and what it should
have done. A post that was read wrongly is the most useful bug report this project can get.
