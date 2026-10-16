# RotorWatch architecture and flows

[Open the editable Miro flowcharts](https://miro.com/app/board/uXjVEZX2kzM=/).
These diagrams describe the reviewed source, not a guarantee that every external
service or retailer is currently reachable. The managed cart browser remains an
unresolved deployment issue as of 16 October 2026.

## 1. Deployment and component boundaries

```mermaid
flowchart TD
    GH["GitHub source and CI"] -->|"Git integration when connected: web/"| UI["Vercel dashboard"]
    GH -->|"Manual backend release"| APP["GCP assistant process: API and workers"]
    UI -->|"HTTPS with workspace API token"| TLS["Caddy reverse proxy"]
    TLS -->|"Private Docker port 8000"| APP
    APP --> DB[("SQLite: assistant-data volume")]
    APP --> MON["Monitor and extraction modules"]
    MON --> RET["Configured retailer pages"]
    APP <-->|"Private tools API"| OC["OpenClaw gateway"]
    OC <-->|"Channel messages"| MSG["Telegram and WhatsApp"]
    OC --> BROW["Managed cart browser: readiness unresolved"]
    BROW --> RET
```

`Service` owns the monitoring, notification and heartbeat tasks inside one Python
process. OpenClaw is a separate container; Caddy terminates HTTPS. The fetcher's
optional Chromium fallback is separate from OpenClaw's managed cart browser.
GitHub CI tests source; no workflow in this repository automatically deploys the
GCP backend. A Git-connected Vercel project can deploy `web/` automatically.

## 2. Monitoring and notification lifecycle

```mermaid
flowchart TD
    W["Product URL or name watch"] --> S["Scheduled scan or queued manual check"]
    S --> F["Bounded discovery and retailer fetch"]
    F --> X["Structured extraction; optional browser and model fallback"]
    X --> Q{"Reliable evidence?"}
    Q -->|"Yes"| O["Save observed stock, price, details and time"]
    Q -->|"No"| U["Save unknown or error; retain saved-price provenance"]
    O --> C{"Meaningful change or new match?"}
    U --> C
    C -->|"No"| END["History updated; no change alert"]
    C -->|"Yes"| E["Record event and grounded summary"]
    E --> P{"Watched and channel preferences allow?"}
    P -->|"No"| END
    P -->|"Yes"| OUT["Durable outbox with delivery deduplication"]
    OUT --> SEND["Send through configured channel transport"]
    SEND --> D{"Delivery confirmed?"}
    D -->|"Yes"| DONE["Record sent state"]
    D -->|"No"| RETRY["Record failure and retry with backoff"]
    RETRY --> OUT
```

The example interval is 900 seconds; the dashboard polling interval is five
seconds. Neither creates a real-time inventory feed. Initial catalog observations
establish a quiet baseline, and unchanged checks do not create change alerts.
Outbox deduplication does not guarantee exactly-once delivery across external
providers. Discovery coverage is bounded and site blocks remain visible.

## 3. Chat, latest prices and stock

```mermaid
flowchart TD
    USER["Question from dashboard or channel"] --> ROUTE["Shared backend chat handler"]
    ROUTE --> KIND{"Supported shopping request?"}
    KIND -->|"No"| DB["Optional model intent or deterministic database command"]
    DB --> FACTS["Answer using stored product records or historical events"]
    KIND -->|"Yes"| MATCH["Resolve exact product, retailer and quantity"]
    MATCH --> CHECK["Bounded retailer recheck; discover if absent"]
    CHECK --> FOUND{"Matching records found?"}
    FOUND -->|"No"| QUEUE["Report limits; queue background discovery when possible"]
    FOUND -->|"Yes"| ANSWER["Return IDs, retailer, price, stock and observation time"]
    QUEUE --> REPLY
    ANSWER --> CART{"Cart request with one exact match?"}
    CART -->|"No"| REPLY["Reply with evidence or ask for an exact product"]
    CART -->|"Yes"| REVIEW["Create purchase review and attempt browser preparation"]
```

Up to three existing matches are rechecked synchronously. A timeout or lock
conflict is reported as unverified saved data; no match is not evidence that a
product is sold out. Prices have their own observation timestamps and stale-price
labels. Human-facing timestamps use IST; storage and API timestamps remain UTC.
Recent activity shows meaningful events from the last 24 hours, not live quotes.
Model calls are optional and budgeted; the model does not supply arbitrary SQL or
invent cart confirmations.

## 4. Reviewed Add-to-cart

```mermaid
flowchart TD
    REQUEST["Exact product and quantity requested"] --> SAVE["Save purchase review C-number"]
    SAVE --> OPEN["Open allowed retailer product in managed browser"]
    OPEN --> PREP{"Browser ready and page verifiable?"}
    PREP -->|"No"| BLOCK["Report blocker; nothing added"]
    PREP -->|"Yes"| SHOW["Show URL, variant, page price and quantity"]
    SHOW --> CONFIRM{"Explicit confirmation within five minutes?"}
    CONFIRM -->|"No"| STOP["No cart mutation; expired reviews need preparation"]
    CONFIRM -->|"Yes"| RECHECK["Recheck exact URL, variant and price fingerprint"]
    RECHECK --> SAME{"Page still matches review?"}
    SAME -->|"No"| BLOCK
    SAME -->|"Yes"| MARK["Persist cart_submitting before mutation"]
    MARK --> CLICK["Attempt one fixed Add-to-cart click"]
    CLICK --> PROOF{"Retailer confirmation verified?"}
    PROOF -->|"Yes"| OK["Record cart_updated; checkout remains manual"]
    PROOF -->|"No or uncertain"| MANUAL["Manual cart check; no automatic replay"]
```

For chat, the review must have been prepared in the same backend chat session.
The dashboard has its own explicit confirmation endpoint. Browser launch, login,
CAPTCHA, unresolved variants and changed page controls can block preparation.
The flow is implemented and fixture-tested, but the current live deployment has
not demonstrated a successful cart mutation. The headless profile is not a
remote desktop; signing into a laptop browser does not sign into the VM profile.

## Where to inspect the implementation

| Concern | Source |
|---|---|
| API auth, CORS and public health route | `src/drone_assistant/api.py` |
| Scheduler, queue and worker ownership | `src/drone_assistant/service.py` |
| Discovery and extraction orchestration | `src/drone_assistant/monitor.py` |
| URL policy, robots checks and browser fallback | `src/drone_assistant/fetching.py`, `sources.py` |
| Products, observations and events | `src/drone_assistant/storage.py`, `app_store.py` |
| Chat and fresh shopping checks | `src/drone_assistant/web_chat.py`, `shopping.py` |
| Notification outbox and provider calls | `src/drone_assistant/notifications.py`, `openclaw.py` |
| Reviewed browser action | `src/drone_assistant/cart.py` |
| OpenClaw channel tool bridge | `integrations/openclaw/` |
| Frontend and request handling | `web/app.js`, `web/core.js` |

See [deployment connections](CONNECTIONS_AND_GITHUB.md) and
[browser diagnostics](../deploy/BROWSER_TROUBLESHOOTING.md) for operations.
