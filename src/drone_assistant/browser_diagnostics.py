"""Bounded browser diagnostics. Opens one product page, never clicks a cart button."""

from __future__ import annotations

import json
import os
import re

import httpx

from .config import Settings
from .openclaw import OpenClaw, browser_issue, tool_data


def safe_message(value: object) -> str:
    text = str(value)
    for key, secret in os.environ.items():
        if re.search(r"TOKEN|KEY|SECRET|PASSWORD", key) and len(secret) >= 5:
            text = text.replace(secret, "[redacted]")
    text = re.sub(r"(?i)(bearer\s+)\S+", r"\1[redacted]", text)
    text = re.sub(r"(?i)([?&](?:token|key|secret|password)=)[^&\s]+", r"\1[redacted]", text)
    return text[:1000]


def main():
    settings = Settings.from_env()
    # Reuse the adapter's origin validation before making any authenticated request.
    gateway = OpenClaw(settings)
    if not gateway.configured:
        raise SystemExit("Private browser gateway is not configured.")
    headers = {"Authorization": "Bearer " + settings.openclaw_token}
    base = settings.openclaw_url
    print("Browser diagnostic: no cart clicks, checkout, or payment.", flush=True)
    with httpx.Client(timeout=45, trust_env=False, follow_redirects=False) as client:

        def invoke(action, **args):
            print("Checking browser action:", action, flush=True)
            response = client.post(
                base + "/tools/invoke",
                headers=headers,
                json={
                    "tool": "browser",
                    "agentId": "rotorwatch-bridge",
                    "args": {
                        "action": action,
                        "profile": settings.openclaw_browser_profile,
                        **args,
                    },
                },
            )
            print("Gateway HTTP:", response.status_code, flush=True)
            payload = response.json()
            if response.status_code >= 400 or payload.get("ok") is not True:
                error = payload.get("error", {})
                if isinstance(error, dict):
                    error = error.get("message", error.get("type", "unspecified gateway error"))
                print("Browser error:", safe_message(error), flush=True)
                if "sandbox" in (browser_issue({"error": error}) or "").casefold():
                    raise SystemExit(10)
                return None
            result = payload.get("result", {})
            if result.get("isError"):
                for block in result.get("content", []):
                    if block.get("type") == "text":
                        print("Browser error:", safe_message(block.get("text")), flush=True)
                        if (
                            "sandbox"
                            in (browser_issue({"error": block.get("text")}) or "").casefold()
                        ):
                            raise SystemExit(10)
                return None
            return tool_data(result)

        try:
            status = invoke("status")
            if status:
                print(
                    "Browser status:",
                    json.dumps(
                        {
                            key: status.get(key)
                            for key in (
                                "running",
                                "cdpReady",
                                "profile",
                                "headless",
                                "noSandbox",
                                "chosenBrowser",
                                "detectError",
                            )
                        }
                    ),
                    flush=True,
                )
            opened = invoke("open", targetUrl="https://thinkrobotics.com/products/argon-neo-rpi5")
            if not opened or not opened.get("targetId"):
                raise SystemExit(1)
            result = invoke(
                "act",
                targetId=opened["targetId"],
                request={
                    "kind": "evaluate",
                    "fn": "() => ({url:location.href,title:document.title,cartControls:document.querySelectorAll('form[action*=\"/cart/add\"] button').length})",
                },
            )
            if result is None:
                raise SystemExit(1)
            print(
                "Retailer page opened and read-only evaluation succeeded. Cart mutation has not been tested.",
                flush=True,
            )
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            print("Browser diagnostic error:", safe_message(exc), flush=True)
            raise SystemExit(1) from None


if __name__ == "__main__":
    main()
