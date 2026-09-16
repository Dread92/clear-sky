# Security

## Reporting

This is a personal project — open an issue, or contact the repository owner directly for anything
sensitive. No bounty, but reports are taken seriously and fixed quickly.

## What is sensitive here

- **`config.json`** holds API tokens. It is git-ignored; `config.example.json` is the template.
  Never commit the real one, and never paste it into an issue.
- **`alerts.sqlite`** holds the full history and the push subscriptions. Git-ignored.
- **VAPID keys** are generated on first use and stored in the database. Losing them forces every
  device to re-subscribe; leaking them lets someone else send notifications to those devices.
- **`ACCESS_KEY`** is the only thing between a public deployment and the open internet. Set it on
  anything reachable from outside, and make it long.

## Deployment notes

- `bind` defaults to `0.0.0.0` so a phone on the same Wi-Fi can reach it. Set `127.0.0.1` to keep it
  on the machine.
- The service makes outbound requests only to the alert APIs, `t.me` preview pages and push
  endpoints. It accepts no user-supplied URL.
- Stored post text is escaped on output; the front end never uses `innerHTML` with source text.
- Do not expose a deployment publicly without an access key, and do not republish air-defence or
  explosion positions during an attack — see [docs/SAFETY.md](docs/SAFETY.md#operational-security).
