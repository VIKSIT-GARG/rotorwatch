# Diagnose browser preparation

The API, channel connectivity and retailer browser are separate checks. A healthy
API or working Telegram/WhatsApp reply does not establish that Chromium can open
a retailer page. Browser preparation was reported failing in the owner deployment;
the exact cause has not yet been captured in the supplied output.

Run this from Cloud Shell (not the browser developer console):

```bash
gcloud compute ssh drone-parts-assistant \
  --project project-1d4cc719-9ffd-484f-b2e \
  --zone asia-south1-a \
  --command='sudo docker exec drone-parts-assistant-assistant-1 python -m drone_assistant.browser_diagnostics'
```

Or from the VM:

```bash
sudo docker exec drone-parts-assistant-assistant-1 \
  python -m drone_assistant.browser_diagnostics
```

The diagnostic uses the running service's private gateway configuration. It
reports browser status, opens one public retailer product page and performs a
read-only evaluation; it does not click Add-to-cart or checkout. Known environment
secrets are redacted in its error output. Share the `Gateway HTTP`, `Browser
status` and `Browser error` lines; never share .env or full gateway configuration.

An exit status of 10 identifies a known Chromium container sandbox launch error.
Only for that confirmed error is `integrations/openclaw/fix-container-browser.mjs`
applicable. It backs up configuration and refuses external/attach-only profiles;
it changes the managed Docker browser settings. Do not run it blindly for a
401, policy failure, missing executable, timeout, login or retailer block.
Gateway authentication and account data should be preserved during any repair.

Success opening the page still does not establish a logged-in retailer account
or successful cart mutation. Use the exact product and review/confirm flow once
browser preparation works; do not repeatedly confirm an uncertain cart result.

## If the HTTP diagnostic reports only "tool execution failed"

The tools HTTP endpoint can hide the detailed launch error. Try the direct
browser CLI inside the running gateway (from Cloud Shell):

```bash
gcloud compute ssh drone-parts-assistant \
  --project project-1d4cc719-9ffd-484f-b2e \
  --zone asia-south1-a \
  --command='sudo docker exec drone-parts-assistant-openclaw-1 node dist/index.js browser --browser-profile openclaw --timeout 60000 start'
```

This launches the managed browser without adding items. It can take about a
minute. Share the error lines, without credentials. If the installed CLI differs,
inspect `node dist/index.js browser --help` inside that container. A stopped
browser reporting noSandbox=false is not sufficient evidence to apply the
sandbox repair, and chosenBrowser=null does not by itself prove a missing binary.
