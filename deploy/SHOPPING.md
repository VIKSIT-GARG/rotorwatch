# Prices and cart requests in chat

The dashboard, Telegram and WhatsApp can use the same shopping handler through
the private `rotorwatch_query` tool. The gateway's main agent must load the
RotorWatch guidance installed by `integrations/openclaw/setup.mjs` in its actual
workspace. The updater installs that guidance without deleting custom instructions.

Examples:

```text
what is the latest price of Arduino Uno R3 from robu.in
use evelta.com and tell me the price
add to cart P12 quantity 2
confirm cart C7
```

Use the P-number returned by a lookup, and the C-number returned by the cart
preview. Never copy sample IDs without checking the product. A store-only price
follow-up uses the previous shopping query in that backend chat session. Product
names in a new question replace the previous product.

Price requests attempt a fresh check of up to three matches, using bounded
discovery if the product is absent. Each price has its own observation timestamp.
Blocked retailer pages, ambiguous variants and missing prices stay unverified.
A previously recorded price is explicitly labeled as saved if the latest check
did not return a price. HTML extraction supports scoped product prices and sale
prices as well as JSON-LD and Shopify product data; unrelated prices are excluded.

Cart requests with several matches return choices, rather than guessing a
retailer or variant. A unique product is opened in the configured OpenClaw
browser profile. The preview includes page price, variant, quantity and URL.
Nothing is added until an explicit `confirm cart C<number>` arrives in the
same backend chat session within five minutes, or the owner confirms in the
dashboard. The browser page is checked again before one Add to cart click.
If the result is uncertain, check the retailer cart manually; automatic replay
is blocked. Checkout and payment remain manual.

The current managed browser is headless. A retailer may allow a guest cart;
an account cart requires logging into this same browser profile. Logging into
a separate laptop browser does not log the GCP browser in. This update does
not install a remote desktop or claim that any retailer account is logged in.
Login, CAPTCHA and unresolved variant blockers are reported rather than bypassed.

After installation, send `/new` to the bot to start a fresh OpenClaw conversation,
then try an explicit product/store price request. The backend code and scripts
are locally tested; actual retailer availability, channel tool invocation and cart
mutation still require verification on the owner's VM.
