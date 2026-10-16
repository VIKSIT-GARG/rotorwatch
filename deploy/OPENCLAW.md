# OpenClaw: WhatsApp + Telegram + retailer browser

This v0.3 integration routes **both notification channels** through one private
OpenClaw Gateway. It also provides database-query tools for inbound chats and
uses OpenClaw's Playwright-backed browser for reviewed Add-to-cart actions.
The older direct Telegram/Meta Cloud API transport remains optional.

## Implemented and verified

- The custom RotorWatch plugin loaded successfully in OpenClaw **2026.10.3**,
  registering `rotorwatch_query` and `rotorwatch_purchase` without load errors. A local-plugin provenance warning is expected for this
  source bundle.
- The generated channel/agent/browser configuration passed OpenClaw's actual
  configuration validator. The browser tool's argument schema was checked
  against that installed runtime.
- Mock gateway tests verify one event queues/sends through both channels,
  approved destinations, message IDs, failures/retries, and no competing Telegram
  poller. Cart tests exercise review, price/variant changes, expiry, uncertain
  responses and restart handling. DOM fixtures execute the actual cart scripts.
- This does **not** establish live WhatsApp linking, Telegram authentication,
  real message receipt, retailer login or a real retailer cart update. Those
  require your accounts on the deployed GCP instance. No checkout/payment tool
  is implemented.

## 1. Backend environment

Copy `.env.example` to `.env` on your GCP VM. Set:

```dotenv
API_TOKEN=your-random-private-workspace-token
NOTIFICATION_TRANSPORT=openclaw
OPENCLAW_GATEWAY_TOKEN=a-different-random-private-gateway-token
TELEGRAM_BOT_TOKEN=your-botfather-token
TELEGRAM_ALLOWED_CHAT_IDS=your-numeric-private-chat-id
WHATSAPP_TO=your-international-number-with-country-code
LLM_API_KEY=your-openrouter-key
LLM_MODEL=openai/gpt-4.1-nano
OPENCLAW_IMAGE=ghcr.io/openclaw/openclaw:2026.10.3-browser
```

Generate each secret locally with `python3 -c 'import secrets; print(secrets.token_urlsafe(32))'`.
Keep `.env` private. Use the same file for all Compose commands below. `WHATSAPP_TO`
accepts digits with an optional leading `+` in OpenClaw mode. Meta business-token,
phone-ID and approved-template settings are **not used** in this mode.

The combined scraper/OpenClaw/Chromium workload needs more memory than the
original scraper-only build. The GCP script now plans `e2-standard-2` (2 vCPU,
8 GB) as an initial choice; measure actual usage and review current region
pricing. Override with `GCP_MACHINE_TYPE` if needed. This is not a free-tier claim.

## 2. Initialize the dedicated OpenClaw volume

Run from the project root on GCP, after installing Docker/Compose:

```bash
docker compose -f compose.yaml -f compose.openclaw.yaml pull openclaw
docker compose -f compose.yaml -f compose.openclaw.yaml run --rm --no-deps --entrypoint node openclaw /opt/rotorwatch-plugin/setup.mjs
```

The initializer writes the dedicated `openclaw-state` volume. It references
secrets from environment variables, selects private DM allowlists for both
channels, registers the plugin and enables a headless managed browser. Existing
plain-JSON configuration is backed up before merging. It does not touch a
separate existing OpenClaw installation.

Install the official channel plugins into that volume if they are not already
available in your image:

```bash
docker compose -f compose.yaml -f compose.openclaw.yaml run --rm --no-deps --entrypoint node openclaw dist/index.js plugins install @openclaw/whatsapp
docker compose -f compose.yaml -f compose.openclaw.yaml run --rm --no-deps --entrypoint node openclaw dist/index.js channels add --channel telegram --use-env
```

The Telegram command offers official plugin installation if missing. Do not run
the old Python Telegram poller with the same bot: OpenClaw owns inbound messages
in this mode. The Python `serve` service automatically disables its direct poller.

## 3. Link WhatsApp with your phone

```bash
docker compose -f compose.yaml -f compose.openclaw.yaml run --rm --no-deps --entrypoint node openclaw dist/index.js channels login --channel whatsapp
```

If you link your personal number and will chat with yourself, set
`OPENCLAW_WHATSAPP_SELF_CHAT=true` before initialization. Leave it false for a
dedicated assistant number.

Scan the QR from WhatsApp → Linked devices. This is an account action you perform
on your own phone. The QR and linked-session files are credentials; do not put
them in public screenshots or repositories. Pairing cannot be completed by code
without access to your account. A retailer login is a separate session.

## 4. Start both services

```bash
docker compose -f compose.yaml -f compose.openclaw.yaml up -d --build
docker compose -f compose.yaml -f compose.openclaw.yaml logs --tail 100 openclaw assistant
```

For the HTTPS API proxy add `-f compose.https.yaml` to these commands. Keep the
same overlays for future upgrades and restarts. Gateway port 18789 is bound to
VM loopback only; it is not exposed through Caddy. The frontend receives only the
RotorWatch API URL/workspace token, never the OpenClaw operator token.

Check the real channel and plugin state:

```bash
docker compose -f compose.yaml -f compose.openclaw.yaml exec openclaw node dist/index.js channels status --probe
docker compose -f compose.yaml -f compose.openclaw.yaml exec openclaw node dist/index.js plugins inspect rotorwatch --runtime --json
```

Open RotorWatch → Alerts → **Send test to both**. This explicitly queues a test
for Telegram and WhatsApp. Check each delivery state, then confirm receipt in
both apps. A channel message ID means accepted by the gateway/provider, not a
read receipt. Disconnected/unpaired channels remain failed/pending and are not
labelled successfully sent. Enable both notification toggles if an older saved
preference disabled either channel.

## 5. Inbound chat and browser cart

The default OpenClaw chat agent has only the two RotorWatch tools. Product
questions use the GCP database API, and watch requests use its validated command
handler. OpenClaw sends the reply back on the originating channel. Its own
orchestration model calls are separate from the Python LLM daily budget.

The internal `rotorwatch-bridge` agent exposes message/browser tools to the
private backend adapter. Incoming chat agents cannot call the browser directly
or invoke arbitrary shell commands through this integration.

For a cart action:

1. Save a product/quantity in **Purchase review**, or ask OpenClaw to prepare it.
2. Select **Prepare browser**. The backend opens the exact allowed retailer URL
   in the managed OpenClaw browser and inspects its product form.
3. Resolve any login, CAPTCHA or variant selection manually in that browser
   session. No login/CAPTCHA bypass is implemented. Use OpenClaw's private Control
   UI via an SSH tunnel when needed; account cookies remain in `openclaw-state`.
4. Review the current page price, exact variant and quantity. Select **Review &
   add to cart**, tick the review box, then **Confirm Add to cart**.
5. The backend rechecks the preview (five-minute expiry) and clicks one exact
   Add-to-cart button. Changed/ambiguous forms pause. A timeout never blindly
   repeats a cart mutation. Where retailer evidence is insufficient, state is
   `cart_check_required`; inspect the retailer cart yourself.

WooCommerce, Shopify and BigCommerce product forms have scoped selectors. This
is not a guarantee of compatibility with every retailer theme. Read-only page
inspection and mutation scripts are fixed application code, not model-generated
JavaScript. Browser evaluation must be enabled for this adapter.

**Ordering/payment remains manual.** There is no Place order, Pay, Buy now or
checkout action. OpenClaw's general browser abilities outside this dedicated
integration do not establish a tested purchase workflow for these retailers.

Back up both `assistant-data` and `openclaw-state`. The latter contains linked
WhatsApp credentials and retailer browser sessions. Do not use `down -v` unless
you intend to erase those volumes.

Primary references: [tools invoke](https://docs.openclaw.ai/gateway/tools-invoke-http-api),
[WhatsApp](https://docs.openclaw.ai/channels/whatsapp),
[Telegram](https://docs.openclaw.ai/channels/telegram),
[browser](https://docs.openclaw.ai/tools/browser/agent-tools),
[Docker](https://docs.openclaw.ai/install/docker),
[plugins](https://docs.openclaw.ai/plugins/building-plugins).
