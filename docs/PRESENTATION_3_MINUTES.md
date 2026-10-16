# RotorWatch: three-minute presentation script

Target: approximately three minutes at a natural speaking pace, including brief
screen transitions. Rehearse once against the timestamps. Speak only the quoted
paragraphs; screen directions are not narration. Use the real product IDs shown
on screen rather than copying example IDs. Keep tokens and private messages out
of the recording.

## 0:00–0:20 | The problem

**Screen:** Dashboard overview, with Demo or Live mode visible.

> Building a drone often starts with a frustrating task: finding the right parts across different websites. Prices change, products go out of stock, and repeatedly checking tabs takes attention away from the build. RotorWatch brings those checks, observations and alerts into one workspace.

## 0:20–0:45 | What the user gets

**Screen:** Product explorer, filters, a watched product and its observation history.

> It is designed for drone builders, robotics students and small project teams sourcing electronics in India. You can track an exact product URL or search by name, filter the catalog, and maintain a watchlist. Each product keeps its retailer link, recorded price, stock status and check history, so the result has evidence you can inspect.

## 0:45–1:10 | Fresh checks and honest answers

**Screen:** Ask for the latest price of an exact listed product; show its timestamp.
For a recorded demo, use prepared footage if a retailer response takes too long.

> When I ask for a product's latest price or availability, the backend attempts a fresh retailer check. It returns the price with its own observation time, displayed in Indian Standard Time. If a site is blocked or a check times out, RotorWatch says the information is unverified. A missing search result is not presented as proof that the product is sold out.

## 1:10–1:35 | Monitoring and messaging

**Screen:** Watchlist, activity, then a prepared Telegram or WhatsApp example.

> Monitoring also runs in the background. The example configuration checks every fifteen minutes and records meaningful changes, such as a restock or price update. A durable notification queue can deliver those changes through Telegram and WhatsApp using OpenClaw. The same channel tools can query the backend, while supported requests also work without a language model.

## 1:35–2:00 | How the components connect

**Screen:** Miro architecture frame. Follow the dashboard, API and database path.

> The frontend is a static dashboard hosted on Vercel. It connects over HTTPS to a Python API on a Google Cloud virtual machine. That process runs the monitor and notification worker, and SQLite stores the observations. Caddy provides the HTTPS proxy. OpenClaw runs separately for messaging and retailer browser actions. GitHub holds the source and automated checks.

## 2:00–2:30 | Purchase review and its current status

**Screen:** Miro cart flow, followed by a saved purchase review or the current
browser blocker. Do not claim a successful real cart update from fixture footage.

> The purchase flow is deliberately reviewed. A request selects the exact product and quantity, then attempts to inspect the retailer page. Before any cart action, the user must review the variant and page price and explicitly confirm. The code checks the page again and avoids blindly repeating an uncertain action. This flow is implemented and tested with fixtures, but the deployed browser still has a startup issue. Checkout and payment remain manual.

## 2:30–3:00 | Value and next step

**Screen:** Return to the dashboard, then repository and project name.

> RotorWatch's value is a shared record of what was checked, what changed and when, available through a dashboard and messaging channels. It is a single-operator prototype, with bounded retailer coverage rather than a complete shopping engine. The next priorities are resolving browser reliability and verifying retailer sessions. The goal is straightforward: spend less time checking parts, and more time building.

## Recording checklist

- Show Demo mode honestly if using synthetic data; never present demo prices as live quotes.
- Use prepared footage of external checks rather than filling the video with loading screens.
- Keep the cart segment as a flow explanation until browser preparation succeeds.
- Label prior channel footage as recorded if its timestamp is from an earlier check.
- Do not expose API keys, workspace tokens, QR pairing codes, account details or private chats.
- Miro architecture and flowcharts: https://miro.com/app/board/uXjVEZX2kzM=/
