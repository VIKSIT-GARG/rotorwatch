from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

import httpx

from .config import Settings
from .telegram import TelegramError


class OpenClawError(Exception):
    def __init__(self, code: int, message: str):
        self.code, self.message = code, message
        super().__init__(message)


def browser_issue(payload: dict) -> str | None:
    """Map known launch failures to fixed messages without exposing gateway output."""
    text = json.dumps(payload, default=str)[:8000].casefold()
    if any(
        marker in text
        for marker in (
            "no usable sandbox",
            "without --no-sandbox",
            "failed to move to new namespace",
            "zygote_host_impl_linux",
        )
    ):
        return "Chromium could not start its sandbox inside the gateway container. Run the browser diagnostic to verify the container browser configuration. No cart action was performed."
    if any(
        marker in text
        for marker in (
            "no usable browser",
            "no supported browser",
            "executable doesn't exist",
            "executablepath not found",
        )
    ):
        return "OpenClaw cannot find its Chromium executable. Run the browser diagnostic and check the installed browser image/path. No cart action was performed."
    if "failed to start chrome cdp" in text:
        return "OpenClaw could not start the Chromium control connection. Run the browser diagnostic for the underlying launch error. No cart action was performed."
    return None


def tool_data(result: dict) -> dict:
    """OpenClaw tools return their structured result in details or JSON text."""
    if not isinstance(result, dict) or result.get("isError"):
        raise OpenClawError(502, "OpenClaw tool reported an error. Check the private gateway logs.")
    if isinstance(result.get("details"), dict):
        return result["details"]
    if isinstance(result.get("structuredContent"), dict):
        return result["structuredContent"]
    for item in result.get("content", []):
        if item.get("type") == "text":
            try:
                value = json.loads(item["text"])
                if isinstance(value, dict):
                    return value
            except (ValueError, KeyError):
                continue
    return result


def delivery_confirmed(data) -> bool:
    if isinstance(data, dict):
        if data.get("ok") is False or data.get("isError") or data.get("error"):
            return False
        if data.get("messageId") or data.get("message_id"):
            return True
        return any(delivery_confirmed(v) for v in data.values())
    if isinstance(data, list):
        return any(delivery_confirmed(v) for v in data)
    return False


class OpenClaw:
    """Private, authenticated OpenClaw tools-invoke client; no generic public proxy."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self.client = client or httpx.AsyncClient(
            timeout=60, trust_env=False, follow_redirects=False
        )
        self.owns_client = client is None
        u = urlsplit(settings.openclaw_url)
        local = u.hostname in {"localhost", "127.0.0.1", "::1", "openclaw", "host.docker.internal"}
        if (
            u.username
            or u.password
            or u.query
            or u.fragment
            or u.path not in {"", "/"}
            or not u.hostname
        ):
            raise ValueError("OPENCLAW_URL must be a gateway origin without paths or credentials.")
        if u.scheme != "https" and not (u.scheme == "http" and local):
            raise ValueError(
                "Use HTTPS for remote OpenClaw, or HTTP only for loopback/private Docker gateway names."
            )

    @property
    def configured(self) -> bool:
        return bool(self.settings.openclaw_token)

    async def invoke(self, tool: str, args: dict, *, key: str | None = None) -> dict:
        if not self.configured:
            raise OpenClawError(409, "Configure the private OpenClaw gateway first.")
        if tool not in {"message", "browser"}:
            raise OpenClawError(
                403, "This adapter exposes only messaging and the managed retailer browser."
            )
        body = {"tool": tool, "args": args, "agentId": "rotorwatch-bridge"}
        if key:
            body["idempotencyKey"] = key
        try:
            response = await self.client.post(
                self.settings.openclaw_url + "/tools/invoke",
                headers={"Authorization": "Bearer " + self.settings.openclaw_token},
                json=body,
            )
            if response.status_code >= 400:
                known_issue = None
                if tool == "browser":
                    try:
                        known_issue = browser_issue(response.json())
                    except ValueError:
                        pass
                descriptions = {
                    401: "OpenClaw gateway authentication failed.",
                    403: "OpenClaw policy blocked this action.",
                    404: "OpenClaw tool is unavailable or disallowed. Check the message/browser tool policy.",
                    429: "OpenClaw is rate-limited; retry later.",
                }
                raise OpenClawError(
                    response.status_code,
                    known_issue
                    or descriptions.get(
                        response.status_code,
                        "OpenClaw request failed. Check the private gateway logs.",
                    ),
                )
            payload = response.json()
            if payload.get("ok") is not True:
                raise OpenClawError(502, "OpenClaw did not confirm a successful tool invocation.")
            if tool == "browser" and payload.get("result", {}).get("isError"):
                known_issue = browser_issue(payload["result"])
                if known_issue:
                    raise OpenClawError(502, known_issue)
            data = tool_data(payload.get("result"))
            if (
                data.get("ok") is False
                or data.get("error")
                or data.get("status") in {"error", "failed", "suppressed"}
            ):
                raise OpenClawError(
                    502,
                    "OpenClaw tool did not complete successfully. Inspect its channel/browser status.",
                )
            return data
        except OpenClawError:
            raise
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            raise OpenClawError(
                503, "OpenClaw is unreachable or returned an invalid response."
            ) from None

    def channel_configured(self, channel: str) -> bool:
        if not self.configured:
            return False
        if channel == "telegram":
            return bool(self.settings.allowed_chat_ids)
        return channel == "whatsapp" and bool(
            re.fullmatch(r"\+?[1-9]\d{7,14}", self.settings.whatsapp_to)
        )

    async def send(self, channel: str, target: str, text: str, *, key: str | None = None):
        if not self.channel_configured(channel):
            raise TelegramError(403)
        if channel == "telegram" and target not in self.settings.allowed_chat_ids:
            raise TelegramError(403)
        if channel == "whatsapp" and target != self.settings.whatsapp_to:
            raise TelegramError(403)
        account = (
            self.settings.openclaw_telegram_account
            if channel == "telegram"
            else self.settings.openclaw_whatsapp_account
        )
        to = "+" + target.lstrip("+") if channel == "whatsapp" else target
        try:
            result = await self.invoke(
                "message",
                {
                    "action": "send",
                    "channel": channel,
                    "accountId": account,
                    "target": to,
                    "message": text,
                },
                key=key,
            )
            if not delivery_confirmed(result):
                raise OpenClawError(
                    502, "OpenClaw did not return a channel message ID. Delivery is unconfirmed."
                )
            return result
        except OpenClawError as exc:
            raise TelegramError(exc.code) from None

    async def browser(self, action: str, **args) -> dict:
        if action not in {"status", "open", "snapshot", "act", "tabs"}:
            raise OpenClawError(403, "Unsupported browser action.")
        return await self.invoke(
            "browser", {"action": action, "profile": self.settings.openclaw_browser_profile, **args}
        )

    async def close(self):
        if self.owns_client:
            await self.client.aclose()
