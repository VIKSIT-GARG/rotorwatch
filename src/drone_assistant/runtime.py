from __future__ import annotations

import asyncio
import logging
import signal
import time
from datetime import UTC, datetime

from .chat import Chat
from .config import Settings
from .domain import Intent, utcnow
from .fetching import Fetcher
from .llm import SmallLLM
from .messages import product_text
from .monitor import Monitor, initialize
from .sources import Registry
from .storage import Store
from .telegram import Telegram, poll_updates, publish_outbox

logger = logging.getLogger(__name__)


async def run(settings: Settings):
    if not settings.telegram_token or not settings.allowed_chat_ids:
        raise ValueError(
            "Set TELEGRAM_BOT_TOKEN and TELEGRAM_ALLOWED_CHAT_IDS in .env before running the bot. The offline demo needs neither."
        )
    registry, store = Registry(settings.sources_file), Store(settings.database)
    initialize(store, registry)
    fetcher, llm = Fetcher(settings, registry), SmallLLM(settings, store)
    monitor = Monitor(settings, store, registry, fetcher, llm)
    telegram, stop = Telegram(settings.telegram_token), asyncio.Event()
    queue: asyncio.Queue[tuple[str, Intent]] = asyncio.Queue(maxsize=20)
    recent_requests = {}

    def queue_search(chat_id: str, intent: Intent):
        if time.monotonic() - recent_requests.get(chat_id, -100) < 10:
            raise ValueError("Please wait 10 seconds between retailer searches.")
        queue.put_nowait((chat_id, intent))
        recent_requests[chat_id] = time.monotonic()

    chat = Chat(store, registry, llm, queue_search, settings.max_products)

    async def monitor_loop():
        while not stop.is_set():
            last = store.get_meta("last_full_scan")
            elapsed = (
                (datetime.now(UTC) - datetime.fromisoformat(last)).total_seconds()
                if last
                else settings.interval_seconds
            )
            if elapsed >= settings.interval_seconds:
                stats = await monitor.run_once()
                logger.info("Scheduled scan completed: %s", stats)
                continue
            try:
                chat_id, intent = await asyncio.wait_for(
                    queue.get(), timeout=min(30, settings.interval_seconds - elapsed)
                )
            except TimeoutError:
                continue
            try:
                if intent.action == "status" and intent.ids:
                    await monitor.run_once(discovery=False, product_id=intent.ids[0])
                    reply = product_text(store.product(intent.ids[0]))
                else:
                    rule_id = store.add_rule(intent.query)
                    stats = await monitor.run_once(rule_id=rule_id)
                    reply = (
                        chat.results(intent)
                        + f"\n\nSearch checked {stats['checked']} products; {stats['listing_errors']} source listings were restricted/unavailable."
                    )
                store.enqueue(chat_id, reply)
            except Exception:
                logger.warning("Queued retailer search failed safely")
                store.enqueue(
                    chat_id,
                    "Retailer search could not finish. Existing watches remain active; try /search again later.",
                )
            finally:
                queue.task_done()

    async def heartbeat():
        while not stop.is_set():
            store.set_meta("heartbeat", utcnow())
            await asyncio.sleep(10)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            pass
    tasks = []
    try:
        await telegram.verify()
        tasks = [
            asyncio.create_task(coro)
            for coro in (
                monitor_loop(),
                heartbeat(),
                poll_updates(telegram, store, chat, settings.allowed_chat_ids, stop),
                publish_outbox(telegram, store, stop, settings.allowed_chat_ids),
            )
        ]
        stop_task = asyncio.create_task(stop.wait())
        done, _ = await asyncio.wait([*tasks, stop_task], return_when=asyncio.FIRST_COMPLETED)
        stop.set()
        for task in done:
            if task is not stop_task and not task.cancelled():
                task.result()
        stop_task.cancel()
        await asyncio.gather(stop_task, return_exceptions=True)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await fetcher.close()
        await llm.close()
        await telegram.close()
        store.close()
