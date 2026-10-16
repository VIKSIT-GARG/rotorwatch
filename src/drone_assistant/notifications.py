from __future__ import annotations

import asyncio
import json
import logging
import re

import httpx

from .app_store import WEB_OWNER, AppStore
from .config import Settings
from .domain import format_money
from .llm import SmallLLM
from .openclaw import OpenClaw
from .telegram import Telegram, TelegramError


class WhatsApp:
    """Official Cloud API template sender. Requires an approved one-body-parameter template."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self.client = client or httpx.AsyncClient(timeout=30)
        self.owns_client = client is None

    @property
    def configured(self):
        s = self.settings
        return bool(
            s.whatsapp_token
            and s.whatsapp_phone_id.isdigit()
            and s.whatsapp_to.isdigit()
            and s.whatsapp_template
            and re.fullmatch(r"v\d+\.\d+", s.whatsapp_graph_version)
        )

    async def send(self, target: str, text: str):
        if not self.configured or target != self.settings.whatsapp_to:
            raise TelegramError(403)
        s = self.settings
        try:
            response = await self.client.post(
                f"https://graph.facebook.com/{s.whatsapp_graph_version}/{s.whatsapp_phone_id}/messages",
                headers={"Authorization": f"Bearer {s.whatsapp_token}"},
                json={
                    "messaging_product": "whatsapp",
                    "to": target,
                    "type": "template",
                    "template": {
                        "name": s.whatsapp_template,
                        "language": {"code": s.whatsapp_language},
                        "components": [
                            {"type": "body", "parameters": [{"type": "text", "text": text[:1000]}]}
                        ],
                    },
                },
            )
            data = response.json()
            if response.status_code >= 400 or not data.get("messages"):
                raise TelegramError(response.status_code if response.status_code >= 400 else 502)
        except TelegramError:
            raise
        except (httpx.HTTPError, ValueError, TypeError):
            raise TelegramError(503) from None

    async def close(self):
        if self.owns_client:
            await self.client.aclose()


async def event_message(llm: SmallLLM, event: dict) -> tuple[str, str]:
    facts = json.loads(event["facts"]) if isinstance(event["facts"], str) else event["facts"]
    kind = event["kind"]
    introductions = {
        "stock": "A watched product's availability changed.",
        "price": "A watched product's price changed.",
        "new_match": "A new product matches your watchlist.",
        "baseline": "Initial product check recorded.",
        "specifications": "A watched product's details changed.",
        "verification": "A product's availability could not be verified.",
        "verified": "Product availability has been verified again.",
    }
    intro, method = introductions.get(kind, "A watched product changed."), "template"
    # Ask the LLM to select a short, grounded wording. The allowed choices are derived
    # from the event, so malformed or invented model text cannot rewrite inventory facts.
    choices = [
        intro,
        intro.replace("A watched product", "Your tracked product"),
        intro.replace("changed.", "has an update."),
    ]
    result = await llm._json(
        "change_message",
        'Choose one short notification introduction from allowed_messages for the supplied database event. Return {"message":"an exact allowed_messages entry"}. Do not add facts, names, numbers or links.',
        json.dumps({"event": facts, "kind": kind, "allowed_messages": choices}),
    )
    if result and result.get("message") in choices:
        intro, method = result["message"], "small_llm"
    status = facts["status"].replace("_", " ")
    price = format_money(facts["price_minor"], facts["currency"])
    previous = facts.get("previous_price_minor")
    if "price" in facts["changes"] and previous is not None:
        price = format_money(previous, facts["currency"]) + " → " + price
    from .display import ist_time

    message = f"{intro}\n{facts['title']}\n{facts['source']} · {status}\n{price}\nChecked: {ist_time(facts['checked_at'])}\n{facts['url']}"
    return message, method


class Notifications:
    def __init__(
        self,
        settings: Settings,
        store: AppStore,
        llm: SmallLLM,
        telegram: Telegram | None,
        whatsapp: WhatsApp,
        openclaw: OpenClaw | None = None,
    ):
        self.settings, self.store, self.llm = settings, store, llm
        self.telegram, self.whatsapp = telegram, whatsapp
        self.openclaw = openclaw

    def channel_ready(self, channel: str) -> bool:
        if self.settings.notification_transport == "openclaw":
            return bool(self.openclaw and self.openclaw.channel_configured(channel))
        return bool(self.telegram) if channel == "telegram" else self.whatsapp.configured

    async def summarize_one(self) -> bool:
        pending = self.store.pending_events(1)
        if not pending:
            return False
        event = pending[0]
        facts = json.loads(event["facts"])
        text, method = await event_message(self.llm, event)
        preferences = self.store.preferences()
        subscribers = set(self.store.subscribers(event["product_id"]))
        should_notify = event["kind"] not in {"baseline", "verified"} and preferences.get(
            event["kind"], False
        )
        # A combined price+stock update follows either enabled event preference.
        if event["kind"] == "stock" and "price" in facts["changes"]:
            should_notify |= preferences.get("price", False)
        recipients = []
        if should_notify:
            for chat_id in self.settings.allowed_chat_ids:
                if (
                    (chat_id in subscribers or WEB_OWNER in subscribers)
                    and preferences["telegram"]
                    and self.channel_ready("telegram")
                ):
                    recipients.append(("telegram", chat_id))
            if (
                WEB_OWNER in subscribers
                and preferences["whatsapp"]
                and self.channel_ready("whatsapp")
            ):
                recipients.append(("whatsapp", self.settings.whatsapp_to))
        self.store.complete_event(event, text, method, recipients)
        return True

    async def deliver_one(self) -> bool:
        message = self.store.claim_message()
        if not message:
            return False
        target, channel = message["chat_id"], message.get("channel", "telegram")
        try:
            if self.settings.notification_transport == "openclaw" and self.openclaw:
                await self.openclaw.send(
                    channel, target, message["text"], key=f"rotorwatch-outbox-{message['id']}"
                )
                from .domain import utcnow

                self.store.set_meta(f"openclaw_accepted_{channel}", utcnow())
            elif (
                channel == "telegram" and self.telegram and target in self.settings.allowed_chat_ids
            ):
                await self.telegram.send(target, message["text"])
            elif (
                channel == "whatsapp"
                and self.whatsapp.configured
                and target == self.settings.whatsapp_to
            ):
                await self.whatsapp.send(target, message["text"])
            else:
                raise TelegramError(403)
            self.store.message_sent(message["id"])
        except TelegramError as exc:
            self.store.message_failed(
                message["id"],
                retry_after=exc.retry_after
                if exc.code == 429
                else min(3600, 5 * 2 ** min(message["attempts"], 9)),
                permanent=exc.code in (400, 401, 403, 404) or message["attempts"] >= 11,
            )
        return True

    async def loop(self, stop: asyncio.Event):
        while not stop.is_set():
            try:
                await self.summarize_one()
                sent = await self.deliver_one()
                await asyncio.sleep(0.15 if sent else 1)
            except Exception:
                logging.getLogger(__name__).warning(
                    "Notification cycle failed safely; retained events will retry"
                )
                await asyncio.sleep(5)
