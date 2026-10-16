# Dashboard on Vercel or Render

The `web/` folder is a complete buildless static frontend. The scraper, database,
LLM and messaging workers belong on the GCP VM. Frontend hosting does not run the scraper.

## Vercel

Push this project to a repository you own, import it into Vercel, and select:

| Setting | Value |
|---|---|
| Root Directory | `web` |
| Framework Preset | Other |
| Build Command | Empty override |
| Install Command | Empty override |
| Output Directory | `.` |

`web/vercel.json` supplies response headers. No provider credentials, API token,
database URL or build environment variables are required on Vercel.

If using an authenticated Vercel CLI, deploy `web/` as a new preview project in
your intended team. Do not link it to an unrelated existing project.

## Render

Import the root `render.yaml` Blueprint from your repository, or create a Static
Site with build command `true` and publish directory `web`. No backend service or
database needs to be created on Render for this frontend.

## Connect your real backend

1. Deploy the GCP API with HTTPS using [GCP.md](GCP.md).
2. Add the exact frontend origin to `CORS_ORIGINS` in the GCP `.env` file. For
   example, `https://your-dashboard.vercel.app`. Each preview origin must be
   listed separately if you want to connect from it. Restart the backend after editing.
3. Open the dashboard → **Connect backend**. Enter `https://api.your-domain.com`
   and your generated `API_TOKEN`.
4. The synthetic demo is replaced only after the authenticated state request
   succeeds. Watchlist, history, chat, preferences and purchase reviews then use
   the GCP database. Updates are polled every five seconds; retailer checks have
   their own configured interval.

The workspace token gives access to one private operator workspace. It is held
in the current browser tab's session storage. It is not a multi-user account
system. Use only a frontend origin you control; rotate the token by replacing it
on GCP and restarting the service. Model, Telegram and WhatsApp credentials stay
on the backend and are never returned in state responses.

## Local interface

```bash
python3 scripts/bootstrap.py
.venv/bin/python -m drone_assistant serve --demo
```

Open `http://127.0.0.1:8000`. Demo mode automatically connects to its loopback
API, uses an in-memory database, and sends no retailer or messaging requests.
Windows: use `.venv\Scripts\python.exe`.

For a single-file UI demo without a Python server:

```bash
node scripts/build-preview.mjs
```

Open `dist/rotorwatch-dashboard.html`. This uses clearly labelled synthetic data
until connected to your HTTPS backend. Fonts are optional external Google Fonts;
system fonts work offline. Local file origins may be blocked by browser CORS:
use the hosted frontend or local server to connect to the real API.

## Current deployment and verification

The owner deployed this frontend to Vercel and supplied a successful public HTTPS
backend health check, authenticated API check and exact-origin CORS check. The
curated source keeps the standalone frontend configuration under `web/`; select
that directory when connecting this full repository to Vercel. Keep using the
existing Vercel project if you want to preserve its production address.

Deployment Protection can require Vercel authentication separately from the
workspace API token. The GCP backend no longer needs a Cloud Shell preview tunnel
when accessed through its public HTTPS proxy. Closing Cloud Shell does not stop
services already running on the VM. The VM must remain running.

Source tests do not establish current retailer stock, a logged-in retailer browser
session or successful live Add-to-cart. See [verification](../docs/VERIFICATION.md).
