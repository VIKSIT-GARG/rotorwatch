from __future__ import annotations

import asyncio
import logging

import httpx

from .chat import Chat
from .storage import Store

logger = logging.getLogger(__name__)


class TelegramError(Exception):
    def __init__(self, code: int, retry_after: int = 30):
        super().__init__(f"Telegram API error {code}")
        self.code, self.retry_after = code, retry_after


class Telegram:
    def __init__(self, token: str, client: httpx.AsyncClient | None = None):
        self._base = f"https://api.telegram.org/bot{token}/"
        self.client = client or httpx.AsyncClient(timeout=45)
        self.owns_client = client is None

    async def call(self, method: str, payload: dict) -> object:
        try:
            response = await self.client.post(self._base + method, json=payload)
            data = response.json()
            if not data.get("ok"):
                raise TelegramError(
                    int(data.get("error_code", response.status_code)),
                    int(data.get("parameters", {}).get("retry_after", 30)),
                )
            return data["result"]
        except TelegramError:
            raise
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            # Never log httpx's full URL: Telegram includes the token in its path.
            raise TelegramError(503) from exc

    async def verify(self):
        await self.call("getMe", {})
        info = await self.call("getWebhookInfo", {})
        if info.get("url"):
            raise ValueError(
                "This bot has a webhook configured. Remove it before starting a long-polling deployment; only one consumer may run."
            )

    async def updates(self, offset: int) -> list[dict]:
        return await self.call(
            "getUpdates", {"offset": offset, "timeout": 30, "allowed_updates": ["message"]}
        )

    async def send(self, chat_id: str, text: str):
        await self.call(
            "sendMessage",
            {"chat_id": chat_id, "text": text, "link_preview_options": {"is_disabled": True}},
        )

    async def close(self):
        if self.owns_client:
            await self.client.aclose()


async def handle_update(update: dict, store: Store, chat: Chat, allowed_ids: tuple[str, ...]):
    message = update.get("message", {})
    target = message.get("chat", {})
    chat_id = str(target.get("id", ""))
    # Private allowlisted chats only: a shared group ID is not individual authorization.
    if target.get("type") != "private" or chat_id not in allowed_ids or not message.get("text"):
        return
    update_id = update["update_id"]
    dedupe_key = f"reply:{update_id}"
    if store.conn.execute("SELECT 1 FROM outbox WHERE dedupe_key=?", (dedupe_key,)).fetchone():
        return
    try:
        result = await chat.handle(chat_id, message["text"])
    except Exception:
        logger.warning("Chat request failed safely for update %s", update_id)
        result = "That request could not be completed. Try /help or a shorter product query."
    store.enqueue(chat_id, result, dedupe_key)


async def poll_updates(
    telegram: Telegram, store: Store, chat: Chat, allowed_ids: tuple[str, ...], stop: asyncio.Event
):
    while not stop.is_set():
        offset = int(store.get_meta("telegram_offset", "0"))
        try:
            updates = await telegram.updates(offset)
            for update in updates:
                await handle_update(update, store, chat, allowed_ids)
                store.set_meta("telegram_offset", str(update["update_id"] + 1))
        except TelegramError as exc:
            if exc.code in (401, 409):
                raise ValueError(
                    "Telegram authentication or duplicate-consumer conflict. Verify the token and run only one bot instance."
                ) from None
            logger.warning("Telegram polling temporarily unavailable (code %s)", exc.code)
            await asyncio.sleep(min(max(exc.retry_after, 3), 60))


async def deliver_one(
    telegram: Telegram, store: Store, allowed_ids: tuple[str, ...] | None = None
) -> bool:
    message = store.claim_message()
    if not message:
        return False
    if allowed_ids is not None and message["chat_id"] not in allowed_ids:
        store.message_failed(message["id"], permanent=True)
        return True
    try:
        await telegram.send(message["chat_id"], message["text"])
        store.message_sent(message["id"])
    except TelegramError as exc:
        delay = (
            exc.retry_after if exc.code == 429 else min(3600, 5 * 2 ** min(message["attempts"], 9))
        )
        store.message_failed(
            message["id"],
            retry_after=delay,
            permanent=exc.code in (400, 401, 403) or message["attempts"] >= 11,
        )
    return True


async def publish_outbox(
    telegram: Telegram, store: Store, stop: asyncio.Event, allowed_ids: tuple[str, ...]
):
    while not stop.is_set():
        if await deliver_one(telegram, store, allowed_ids):
            await asyncio.sleep(0.1)
        else:
            await asyncio.sleep(1)
