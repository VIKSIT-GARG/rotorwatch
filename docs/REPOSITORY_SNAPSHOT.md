# GitHub source snapshot — 16 October 2026

## Source of truth

This package was curated from `rotorwatch-backend-current.tar.gz` and
`rotorwatch-frontend-current.tar.gz` supplied by the owner from GCP/Cloud Shell.
Backend application source was preserved. All six frontend application assets
matched in both uploads; one shared copy is retained under `web/`. The standalone
frontend's buildless Vercel configuration replaces the older header-only config.
No older local application code was merged into this snapshot.

Packaging changes: README and deployment notes updated, ignore rules expanded,
standalone preview regenerated from current `web/` assets, this provenance record
and a SHA-256 file manifest added. No credentials or account state are bundled.

## Layout

- `src/drone_assistant/`: Python API, monitoring, prices, history and cart reviews.
- `web/`: Vercel frontend and assets also served by the Python backend.
- `integrations/openclaw/`: channel tools, setup and scoped browser repair helper.
- `deploy/`, `compose*.yaml`, `Dockerfile`: VM and proxy configuration.
- `tests/`, `.github/workflows/ci.yml`: tests and CI.
- `.env.example`: blank credential template; use a private `.env` on the VM.

Vercel Root Directory: `web`. Framework: Other. Build and install commands:
empty. Output directory: `.`. Upload the extracted repository contents so that
README.md, pyproject.toml and .github are directly at the GitHub repository root.

## Checks run on this package

Python 3.12: 90 tests passed, 1 optional browser integration test skipped because
its Chromium runtime was unavailable. JavaScript/DOM: all 9 tests passed.
Ruff lint and formatting passed; JavaScript and Bash syntax checks passed.
The standalone preview was regenerated. These are the tests present in the
uploaded VM backup, not test counts from other development copies.

## Live deployment status supplied by the owner

The owner reported working Telegram and WhatsApp replies and a Vercel frontend.
The supplied setup output showed successful authenticated API/CORS checks and a
public HTTPS health response. These historical observations do not verify uptime
at the time this source package is used. Existing services were not changed by
creating this archive.

Browser preparation is currently reported as failing. The archive does not
claim that retailer login or Add-to-cart works. Diagnose the running instance
with the command in `deploy/BROWSER_TROUBLESHOOTING.md`. Checkout/payment stays
manual. Historical records elsewhere in the documentation describe earlier
validation sessions and are superseded by this section for current status.

## Private runtime state

Keep `.env`, database volumes, OpenClaw sessions, credentials and private backups
outside GitHub. This archive cannot restore those items. Do not overwrite the
existing VM `.env` with the blank example when deploying a code update.
