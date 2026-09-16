# Deploying

## Fly.io (what this repo is set up for)

One `shared-cpu-1x` / 256 MB machine in Amsterdam with a 1 GB volume for the history. Inside the
free allowance, but Fly requires a payment method on the account before it will create apps.

### First time

Windows: double-click **`deploy-fly.bat`** — it installs `flyctl`, logs you in, creates the app and
the volume, asks for an access key and deploys.

Manually, anywhere:

```bash
fly auth login
fly apps create clear-sky --org personal
fly volumes create data --size 1 --region ams --yes -a clear-sky
fly secrets set ACCESS_KEY=a-long-random-string -a clear-sky
fly secrets set ALERTS_IN_UA_TOKEN=your-token -a clear-sky
fly deploy -a clear-sky --ha=false
```

Change `app = "…"` in `fly.toml` to your app name.

### Updating

```bash
fly deploy
```

**If a deploy does not seem to change anything**, check the build tag at the bottom of the menu in
the app (`build YYYY-MM-DD HH:MM UTC`) against the one in `static/kyiv.html`. If they differ, the
files on the machine you deployed from were not the ones you edited — `git status` and `git log -1`
tell you which version you actually shipped. This is much more often the cause than a Fly caching
problem.

Useful:

```bash
fly logs -a clear-sky          # live logs
fly status -a clear-sky        # machine state
fly ssh console -a clear-sky   # shell inside the machine
fly ssh console -a clear-sky -C "ls -la /data"   # the database on the volume
```

### Access key

With `ACCESS_KEY` set, every request needs `?key=…` once per device; the key is then remembered in
the browser. Open `https://<app>.fly.dev/m?key=YOUR_KEY` once on each phone. Without it the
deployment is public.

## GitHub Actions (optional automatic deploy)

`.github/workflows/fly-deploy.yml` deploys on every push to `main` **if** the repository secret
`FLY_API_TOKEN` exists. Create one with:

```bash
fly tokens create deploy -a clear-sky
```

then add it under *Settings → Secrets and variables → Actions → New repository secret*. Without the
secret the job is skipped, so the workflow is harmless until you want it.

## Render.com

`render.yaml` is a Blueprint: *New → Blueprint → point at this repo*. The free instance sleeps after
15 minutes idle, which is not what you want for an alert app — use a paid instance, or ping
`/healthz` on a schedule.

## Docker anywhere

```bash
docker build -t clear-sky .
docker run -d --name clear-sky -p 8080:8080 \
  -v clear-sky-data:/data \
  -e ACCESS_KEY=a-long-random-string \
  -e ALERTS_IN_UA_TOKEN=your-token \
  clear-sky
```

The image contains `app/` and `static/` only; the database lives on the volume at `/data`.

## On your own PC

`start.bat` (Windows) or `scripts/start.sh`. To reach it from your phone on the same Wi-Fi, use the
LAN address the service prints at startup. To reach it from outside without opening a port,
`scripts/tunnel.bat` runs a Cloudflare quick tunnel.

Running at home means the app stops when the PC sleeps — which is exactly when you need it. Fly is
the better home for it; keep the local copy for development.

## Web Push

Push needs `cryptography` (installed by `start.bat`, in `requirements.txt`, and in the Docker image)
and HTTPS — so it works on a Fly deployment, not on plain `http://localhost` except in Chrome, which
treats localhost as secure.

VAPID keys are generated on first use and stored in the `kv` table, so they survive restarts as long
as the volume does. Losing them means every device has to subscribe again.

On iPhone the page must be added to the home screen before notifications can be granted.
