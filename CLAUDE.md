# Working on Clear Sky (notes for Claude sessions)

Clear Sky is used live by people deciding whether to take cover. Read `docs/SAFETY.md` and
`docs/TECHNICAL.md` before changing `app/` or `static/`.

## Non-negotiable

- Never invent a trajectory, ETA, threat type, altitude or position a post did not state.
- Only the official data sets an alert's colour, raion by raion. Telegram cannot start or end an alert.
- Banderol is a jet drone, never a cruise missile.
- The four watched places (Home, Work, Kids, Pin) never leave the phone; push stores a ~10 km cell only.
- `config.json`, `vapid.json`, `.env`, databases: never committed, never copied anywhere, never printed.
- Never type or store the owner's passwords or tokens; secrets are set by the owner (Fly secrets, config.json).

## Commits

No Claude attribution in commit messages or on GitHub — no `Co-Authored-By` and no `Claude-Session` lines.
The repository is the owner's; he asked for his name only.

## Every patch

Follow `docs/TECHNICAL.md` §18: tests + ruff clean, version bumped (server.py and kyiv.html),
a `CHANGELOG.md` entry, and `docs/TECHNICAL.md` updated in the same commit — `tests/test_docs.py`
fails otherwise. Page changes get a render check (Playwright) in the three languages.
