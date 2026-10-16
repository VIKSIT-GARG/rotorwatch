# Demo recording checklist

## Web dashboard demonstration (v0.2)

```bash
python3 scripts/bootstrap.py
.venv/bin/python -m drone_assistant serve --demo
```

Open `http://127.0.0.1:8000`. Record the overview, filterable watchlist,
**Simulate update** action, AI assistant prompt chips, alert activity, add/remove
watch actions, observation history and purchase review. This local API uses
synthetic records and sends no external messages. The single-file HTML demo is
also included in the release archive under `preview/`.

For OpenClaw setup and testing BOTH WhatsApp and Telegram, follow
[deploy/OPENCLAW.md](../deploy/OPENCLAW.md). Use Alerts → Send test to both, then
show the received test in both apps. Show Prepare browser → exact price/variant
review → explicit Add to cart separately from checkout, which stays manual.

For a real deployment, connect the hosted frontend to the GCP HTTPS API, show
recorded timestamps, and use a provider-backed chat request. Keep API tokens out
of recordings. Real Telegram/WhatsApp delivery requires configured credentials;
Direct Meta mode needs its approved template; OpenClaw mode uses QR linking. Do not record demo values as
live retailer prices or claim a purchase review is an executed cart/order.

## Reproducible offline demonstration

```bash
python3 scripts/bootstrap.py --demo
```

Record the terminal showing the baseline, the forced monitoring run and its three simulated notifications, followed by search/filtering, comparison, history, URL/name watch addition and removal. The demo uses the actual monitor, extraction, SQLite, alert creation and chat code with an explicit fixture transport. It makes no retailer requests and sends no Telegram messages. All prices, product paths and stock transitions in this mode are synthetic.

This establishes the monitoring and conversation behavior, but it is not a replacement for the brief's requested video showing a real notification arriving in Telegram.

## Complete the real Telegram recording

1. Set up a private Telegram bot and allowlist as described in README.md.
2. Start the bot and record `/start`, `/search Raspberry Pi 5`, `/watchlist`, `/compare P1 P2`, and a real supported product URL added with `/watch`.
3. For a reproducible stock/preorder alert arriving in the real Telegram app, run the explicit opt-in command below. It uses a separate in-memory fixture database and labels each message `DEMO — SYNTHETIC INVENTORY CHANGE`. The supplied chat ID must be in your allowlist.
4. Show the notification in the Telegram app, along with the originating monitoring run and check timestamp.
5. Show that repeating the same observation creates no additional state-change notification.

```bash
.venv/bin/python -m drone_assistant demo --telegram-chat YOUR_PRIVATE_CHAT_ID
```

Or, after Docker setup:

```bash
docker compose run --rm assistant python -m drone_assistant demo --telegram-chat YOUR_PRIVATE_CHAT_ID
```

This command intentionally sends three labelled demonstration messages. Normal `demo` without the flag sends nothing. It does not start a second Telegram poller and can be used while the normal bot is running. Alternatively record a real favorable transition on a product with an actual out-of-stock baseline.

Never edit a production product to pretend a real retailer has restocked. Both demo modes are deliberately separate from the live monitoring database.

For a deterministic notification-transport check, unit tests use mocked Telegram responses to exercise send success, rate limits and permanent failures. A real token is necessary to verify actual delivery.

## Files to submit

- Source in a GitHub repository, including README.md, architecture diagram, tests and deployment instructions.
- A short recording that distinguishes real retailer observations from controlled/simulated changes.
- The limitations and verification record rather than unmeasured uptime/accuracy/cost claims.
