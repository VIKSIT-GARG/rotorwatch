from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from .app_store import WEB_OWNER, AppStore
from .cart import Cart
from .chat import Chat
from .config import Settings
from .domain import Intent, Snapshot, Status, utcnow
from .fetching import Fetcher
from .llm import SmallLLM
from .monitor import Monitor, initialize
from .notifications import Notifications, WhatsApp
from .openclaw import OpenClaw
from .shopping import Shopping
from .sources import Registry
from .telegram import Telegram, poll_updates
from .web_chat import WebChat

logger = logging.getLogger(__name__)


class Service:
    def __init__(self, settings: Settings, *, demo: bool = False):
        self.demo = demo
        if demo:
            settings = replace(
                settings,
                database=":memory:",
                telegram_token="",
                allowed_chat_ids=(),
                llm_api_key="",
                whatsapp_token="",
                whatsapp_to="",
                openclaw_token="",
            )
        self.settings = settings
        self.store = AppStore(settings.database)
        self.registry = Registry(settings.sources_file)
        initialize(self.store, self.registry)
        if not self.store.get_meta("web_initialized"):
            self.store.subscribe_defaults(WEB_OWNER)
            self.store.set_meta("web_initialized", "1")
        self.llm, self.fetcher = SmallLLM(settings, self.store), Fetcher(settings, self.registry)
        self.monitor = Monitor(settings, self.store, self.registry, self.fetcher, self.llm)
        self.telegram = (
            Telegram(settings.telegram_token)
            if settings.telegram_token
            and settings.allowed_chat_ids
            and settings.notification_transport == "direct"
            else None
        )
        self.whatsapp = WhatsApp(settings)
        self.openclaw = OpenClaw(settings)
        self.cart = Cart(self.store, self.openclaw, self.registry)
        self.notifications = Notifications(
            settings, self.store, self.llm, self.telegram, self.whatsapp, self.openclaw
        )
        if settings.notification_transport == "openclaw" and not self.store.get_meta(
            "web_preferences"
        ):
            self.store.set_preferences({"telegram": True, "whatsapp": True})
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=20)
        self.commands = Chat(
            self.store, self.registry, self.llm, self.queue_chat_search, settings.max_products
        )
        self.web_chat = WebChat(
            self.store,
            self.llm,
            self.commands,
            Shopping(
                self.store, self.monitor, self.cart, demo=demo, queue_search=self.queue_chat_search
            ),
        )
        self.tasks = []
        self.stop = asyncio.Event()
        self.errors: dict[str, str] = {}
        self.last_manual_request = -100.0
        if demo:
            self.seed_demo()

    def seed_demo(self):
        demo_items = [
            (1, "Raspberry Pi 5 · 4GB", Status.OUT_OF_STOCK, 649900),
            (2, "Raspberry Pi 5 · 8GB", Status.IN_STOCK, 869900),
            (3, "Raspberry Pi 5 · 2/4/8GB", Status.UNKNOWN, 536900),
            (4, "Holybro Pixhawk 6X Standard Set", Status.PRE_ORDER, 4720000),
            (5, "Pi 5 PoE+ HAT · Waveshare", Status.IN_STOCK, 204900),
        ]
        for pid, title, status, price in demo_items:
            self.store.observe(
                pid,
                Snapshot(
                    title,
                    status,
                    price,
                    evidence="Synthetic demo fixture; not a live retailer observation.",
                    method="demo_fixture",
                ),
            )
        additions = [
            (
                "https://robu.in/product/demo-m2-hat/",
                "robu",
                "Raspberry Pi 5 M.2 HAT+",
                Status.IN_STOCK,
                129900,
            ),
            (
                "https://zbotic.in/product/demo-flight-controller/",
                "zbotic",
                "Matek H743 Flight Controller",
                Status.OUT_OF_STOCK,
                1149900,
            ),
            (
                "https://evelta.com/demo-companion-computer/",
                "evelta",
                "Raspberry Pi Compute Module 4 · 4GB",
                Status.IN_STOCK,
                739900,
            ),
        ]
        for url, source, title, status, price in additions:
            pid = self.store.add_product(url, source, title)
            self.store.watch(WEB_OWNER, pid)
            self.store.observe(
                pid,
                Snapshot(
                    title, status, price, method="demo_fixture", evidence="Synthetic demo fixture."
                ),
            )
        self.store.set_meta("heartbeat", utcnow())

    def queue_chat_search(self, owner: str, intent: Intent):
        if intent.action == "status" and intent.ids:
            self.enqueue("product", owner=owner, product_id=intent.ids[0])
        else:
            rid = self.store.add_rule(intent.query)
            self.enqueue("rule", owner=owner, rule_id=rid)

    def enqueue(self, kind: str, **values):
        if self.queue.full():
            raise ValueError("The check queue is full. Please try again shortly.")
        self.queue.put_nowait({"kind": kind, **values})

    def queue_manual(self, product_id: int | None):
        if time.monotonic() - self.last_manual_request < 10:
            raise ValueError("Wait 10 seconds between manual checks.")
        if product_id and self.store.product(product_id) is None:
            raise ValueError("Unknown product.")
        self.enqueue("product" if product_id else "full", product_id=product_id)
        self.last_manual_request = time.monotonic()

    def capabilities(self) -> dict:
        return {
            "telegram": self.notifications.channel_ready("telegram"),
            "whatsapp": self.notifications.channel_ready("whatsapp"),
            "llm": self.llm.enabled,
            "browser": self.settings.browser_enabled,
            "purchase_automation": self.openclaw.configured and not self.demo,
            "notification_transport": self.settings.notification_transport,
            "openclaw_configured": self.openclaw.configured,
            "openclaw_live_verified": all(
                self.store.get_meta(f"openclaw_accepted_{c}") for c in ("telegram", "whatsapp")
            ),
            "channel_last_accepted": {
                c: self.store.get_meta(f"openclaw_accepted_{c}") for c in ("telegram", "whatsapp")
            },
            "errors": self.errors,
        }

    def state(self) -> dict:
        state = self.store.dashboard()
        state["mode"] = "demo" if self.demo else "live"
        state["capabilities"] = self.capabilities()
        state["scan_running"] = self.monitor.lock.locked()
        state["queued_checks"] = self.queue.qsize()
        state["interval_seconds"] = self.settings.interval_seconds
        state["sources"] = []
        for source in self.registry.sources:
            products = [p for p in state["products"] if p["source"] == source]
            last = max((p["last_checked"] for p in products if p["last_checked"]), default=None)
            state["sources"].append(
                {
                    "key": source,
                    "last_checked": last,
                    "state": "restricted"
                    if any(p["error"] for p in products)
                    else "checked"
                    if last
                    else "not_checked",
                }
            )
        if state["last_scan"]:
            state["next_scan"] = (
                datetime.fromisoformat(state["last_scan"])
                + timedelta(seconds=self.settings.interval_seconds)
            ).isoformat()
        else:
            state["next_scan"] = None
        # Include running/capability changes in conditional-poll responses.
        import hashlib
        import json

        state["version"] = hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()[
            :20
        ]
        return state

    async def advance_demo(self):
        if not self.demo:
            raise ValueError("Synthetic changes are disabled in live mode.")
        while await self.notifications.summarize_one():
            pass
        p = self.store.product(1)
        status = Status.IN_STOCK if p["status"] != "in_stock" else Status.OUT_OF_STOCK
        self.store.observe(
            1,
            Snapshot(
                p["title"],
                status,
                p["price_minor"],
                method="demo_fixture",
                evidence="Explicit synthetic demo update.",
            ),
        )
        p = self.store.product(2)
        self.store.observe(
            2,
            Snapshot(
                p["title"],
                Status(p["status"]),
                max(10000, p["price_minor"] - 10000),
                method="demo_fixture",
                evidence="Explicit synthetic price update.",
            ),
        )
        while await self.notifications.summarize_one():
            pass

    async def _monitor_loop(self):
        while not self.stop.is_set():
            last = self.store.get_meta("last_full_scan")
            due = (
                not last
                or (datetime.now(UTC) - datetime.fromisoformat(last)).total_seconds()
                >= self.settings.interval_seconds
            )
            try:
                if due:
                    await self.monitor.run_once()
                    self.errors.pop("monitor", None)
                    continue
                try:
                    job = await asyncio.wait_for(self.queue.get(), 2)
                except TimeoutError:
                    continue
                try:
                    await self.monitor.run_once(
                        discovery=job["kind"] != "product",
                        product_id=job.get("product_id"),
                        rule_id=job.get("rule_id"),
                    )
                    if job.get("owner") and job["owner"] != WEB_OWNER:
                        self.store.enqueue(
                            job["owner"],
                            "Your requested database/retailer check has completed. Use /search or /watchlist for updated results.",
                        )
                finally:
                    self.queue.task_done()
            except Exception:
                logger.warning("Monitoring cycle failed safely; will retry")
                self.errors["monitor"] = (
                    "Check failed; retrying. Last confirmed observations are retained."
                )
                await asyncio.sleep(30)

    async def _heartbeat(self):
        while not self.stop.is_set():
            self.store.set_meta("heartbeat", utcnow())
            await asyncio.sleep(10)

    async def start(self):
        self.tasks.append(asyncio.create_task(self._heartbeat()))
        if self.demo:
            while await self.notifications.summarize_one():
                pass
            return
        self.tasks.extend(
            [
                asyncio.create_task(self._monitor_loop()),
                asyncio.create_task(self.notifications.loop(self.stop)),
            ]
        )
        if self.telegram:
            try:
                await self.telegram.verify()
                self.tasks.append(
                    asyncio.create_task(
                        poll_updates(
                            self.telegram,
                            self.store,
                            self.commands,
                            self.settings.allowed_chat_ids,
                            self.stop,
                        )
                    )
                )
            except Exception:
                self.errors["telegram"] = (
                    "Bot authentication/webhook check failed. Verify its configuration."
                )

    async def close(self):
        self.stop.set()
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.fetcher.close()
        await self.llm.close()
        await self.whatsapp.close()
        await self.openclaw.close()
        if self.telegram:
            await self.telegram.close()
        self.store.close()
