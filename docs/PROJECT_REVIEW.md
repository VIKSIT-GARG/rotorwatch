# Source review and documentation update

Reviewed repository: `VIKSIT-GARG/rotorwatch`, main at
`e5b6dba23f2af77574beecca0be46cd9af9acdef`.
Review date: 16 October 2026. This is a focused implementation/documentation
review, not a penetration test or an exhaustive defect audit.

## What the code implements

- One FastAPI service owns SQLite access, monitoring, queued checks and outbox work.
- Four configured retailer adapters support bounded discovery and timestamped
  product observations; extraction failures are retained as unknown.
- Supported price, stock and cart messages use the shared shopping handler;
  model calls are optional for other intent/wording/classification tasks.
- Price provenance, IST human-readable timestamps and meaningful recent events
  are represented in the backend and the current frontend source.
- Workspace API authentication and exact-origin CORS protect API operations.
- The reviewed cart flow verifies URL, variant and page price, requires explicit
  confirmation, persists an attempt before mutation and blocks automatic replay
  of uncertain outcomes. It never performs checkout or payment.

## Findings addressed by this pull request

| Finding | Documentation change |
|---|---|
| README led with infrastructure and packaging history | Explain the user problem, audience, capabilities and normal workflow first |
| Architecture obscured worker ownership and browser separation | Diagram one Python process, its database, separate gateway and two browser paths |
| Vercel root instructions could be misapplied to a standalone upload | Show `web` for this repository and `./` for an already extracted frontend folder |
| Source implementation could be read as proof that live cart actions work | Mark the deployed browser failure and distinguish fixture tests from live success |
| No concise presentation narrative | Add a timed three-minute script with screen cues and honest demo instructions |

## Remaining limitations and follow-up work

1. **Managed cart browser startup is unresolved on the owner's VM.** The owner
   supplied HTTP 500 tool failures and a Chromium CDP startup crash; changing
   browser.noSandbox alone did not establish a working browser. Do not advertise
   live Add-to-cart success until launch, page inspection and mutation are verified.
2. **Coverage and freshness are bounded.** Synchronous shopping checks cover at
   most three matches, scans have pacing/time limits, and the manual job queue is
   in memory. Missing results are not a complete cross-retailer stock survey.
3. **This is a single-operator workspace.** One shared API token is not a multi-user
   authentication system. The OpenClaw query tool currently uses the shared
   `openclaw` backend session; add per-channel/user session scoping before making
   conversation isolation claims for multiple operators.
4. **Hosted code and running code can diverge.** Vercel must receive the intended
   web assets, and GCP backend deployment is manual. A README PR does not update
   runtime credentials, browser configuration, service containers or VM source.
5. **External integrations need live verification.** Test fixtures are not proof
   of real retailer login, source coverage, delivery guarantees or current stock.

No application behavior, cloud resource, token, account session or deployment
setting was changed by this pull request. The existing operational guides remain
available for detailed setup and troubleshooting.

## Checks run during this review

- Python 3.12 suite: **90 passed, 1 skipped**. The optional real Chromium test was
  skipped because its matching browser runtime was unavailable.
- JavaScript/DOM suite: **9 passed**.
- Offline CLI demo passed monitoring, restock/preorder/new-match alerts,
  unchanged-run deduplication, search, comparison, history and watch changes.
- README/document links, source references, Mermaid structure, whitespace and
  script word count were checked locally.

Tests used the reviewed checkout with existing local test dependencies. Python
reported an upstream Starlette/HTTPX deprecation warning. No live retailer/cart
mutation or external message was performed as part of these checks.
