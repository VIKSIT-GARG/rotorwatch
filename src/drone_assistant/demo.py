from __future__ import annotations

from dataclasses import replace
from importlib.resources import files

from .chat import Chat
from .config import Settings
from .domain import Status
from .fetching import FixtureFetcher
from .llm import SmallLLM
from .monitor import Monitor
from .sources import Registry
from .storage import Store


async def run_demo(telegram=None, telegram_chat_id: str | None = None) -> dict:
    if telegram:
        print(
            "TELEGRAM DEMO — synthetic inventory changes; labelled demo messages are sent to the allowlisted chat. No retailer requests.\n"
        )
    else:
        print(
            "OFFLINE DEMO — synthetic prices/stock, no live retailer requests, no Telegram messages sent.\n"
        )
    settings = replace(Settings(), source_delay=0, max_discovery_pages=1)
    store, registry = Store(":memory:"), Registry()
    registry.sources = {"robu": registry.sources["robu"]}
    pi_url = "https://robu.in/product/demo-raspberry-pi-5-4gb/"
    pix_url = "https://robu.in/product/demo-pixhawk-6x/"
    old_hat = "https://robu.in/product/demo-pi-5-gpio-hat/"
    new_hat = "https://robu.in/product/demo-pi-5-pcie-hat/"
    pi_id = store.add_product(pi_url, "robu", "Raspberry Pi 5 4GB", seed=True)
    pix_id = store.add_product(pix_url, "robu", "Holybro Pixhawk 6X", seed=True)
    store.add_rule("Pi 5 HAT", seed=True)
    source = registry.sources["robu"]
    listing = source.search_url("Pi 5 HAT")

    def fixture(name):
        return files("drone_assistant").joinpath(f"data/demo/{name}.html").read_text()

    fetcher = FixtureFetcher(
        {
            pi_url: fixture("pi_out"),
            pix_url: fixture("pix_out"),
            old_hat: fixture("hat_old"),
            new_hat: fixture("hat_new"),
            listing: f'<a href="{old_hat}">Pi 5 GPIO HAT</a>',
        }
    )
    llm = SmallLLM(settings, store)
    chat = Chat(store, registry, llm)
    monitor = Monitor(settings, store, registry, fetcher, llm)
    await chat.handle("42", "/start")
    try:
        print("1. Baseline monitoring run:", await monitor.run_once())
        assert store.conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 0
        fetcher.responses[pi_url] = fixture("pi_in")
        fetcher.responses[pix_url] = fixture("pix_pre")
        fetcher.responses[listing] += f'<a href="{new_hat}">Pi 5 PCIe HAT</a>'
        print(
            "2. Forced run: restock, preorder opened, new matching HAT:", await monitor.run_once()
        )
        count = store.conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]
        assert count == 3, f"Expected 3 alerts, got {count}"
        print("\nSIMULATED TELEGRAM ALERTS")
        for row in store.conn.execute("SELECT * FROM outbox ORDER BY id"):
            print("\n" + row["text"])
            if telegram:
                await telegram.send(
                    telegram_chat_id, "DEMO — SYNTHETIC INVENTORY CHANGE\n" + row["text"]
                )
                store.message_sent(row["id"])
        await monitor.run_once()
        assert store.conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == count
        print("\n3. Repeated unchanged run: no duplicate alerts.")
        for text in [
            "find Raspberry Pi 5 in stock under 7000",
            f"/compare P{pi_id} P{pix_id}",
            f"/history P{pi_id}",
            "/watch https://robu.in/product/demo-raspberry-pi-5-8gb/",
            "/watch flight controller",
            "/watchlist",
            "/unwatch P1",
        ]:
            print(f"\nUSER: {text}\nBOT: {await chat.handle('42', text)}")
        assert store.product(pi_id)["status"] == Status.IN_STOCK
        print(
            "\nDEMO PASSED: monitoring, three alert types, deduplication, search/filter, comparison, history and watchlist changes."
        )
        return {"alerts": count, "products": store.product_count(), "passed": True}
    finally:
        await llm.close()
        store.close()
