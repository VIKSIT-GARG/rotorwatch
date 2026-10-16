import pytest

from drone_assistant.chat import Chat, parse_intent
from drone_assistant.config import Settings
from drone_assistant.domain import Snapshot, Status
from drone_assistant.llm import SmallLLM
from drone_assistant.monitor import initialize
from drone_assistant.telegram import handle_update


def test_price_stock_store_and_spec_filters():
    intent = parse_intent(
        "find Raspberry Pi 5 in stock under ₹10,000 from thinkrobotics at least 8GB RAM"
    )
    assert intent.query == "Raspberry Pi 5"
    assert intent.max_price_minor == 1_000_000
    assert intent.status == "in_stock" and intent.source == "thinkrobotics"
    assert intent.min_ram_gb == 8


def test_k_price_and_variant_url():
    assert parse_intent("find Pi5 under 10k").max_price_minor == 1_000_000
    url = "https://thinkrobotics.com/products/pi-5?variant=123"
    assert parse_intent("/watch " + url).query == url


async def test_url_watch_add_and_remove_without_restart(store, registry):
    llm = SmallLLM(Settings(), store)
    chat = Chat(store, registry, llm)
    try:
        response = await chat.handle(
            "42", "/watch https://robu.in/product/raspberry-pi-5-model-4gb/"
        )
        assert "Watching P1" in response
        assert "queued" not in response
        assert len(store.watchlist("42")[0]) == 1
        await chat.handle("42", "/unwatch P1")
        assert len(store.watchlist("42")[0]) == 0
        await chat.handle("42", "/watch P1")
        assert len(store.watchlist("42")[0]) == 1
    finally:
        await llm.close()


async def test_chat_uses_recorded_facts_and_filters(store, registry, product):
    llm = SmallLLM(Settings(), store)
    chat = Chat(store, registry, llm)
    store.observe(
        product, Snapshot("Raspberry Pi 5 4GB", Status.IN_STOCK, 650000, attributes={"ram_gb": "4"})
    )
    try:
        assert "₹6,500.00" in await chat.handle("42", "find Raspberry Pi 5 in stock under 7000")
        assert "No matching" in await chat.handle("42", "find Raspberry Pi 5 under 6000")
        assert "No matching" in await chat.handle("42", "find Raspberry Pi 5 at least 8GB RAM")
        assert "LATEST OBSERVATIONS" in await chat.handle("42", "/history P1")
    finally:
        await llm.close()


async def test_private_chat_allowlist_and_update_deduplication(store, registry):
    initialize(store, registry)
    llm = SmallLLM(Settings(), store)
    chat = Chat(store, registry, llm)

    def update(identifier, target, kind="private"):
        return {
            "update_id": identifier,
            "message": {"text": "/start", "chat": {"id": target, "type": kind}},
        }

    try:
        await handle_update(update(1, 999), store, chat, ("42",))
        await handle_update(update(2, 42, "group"), store, chat, ("42",))
        assert store.conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 0
        await handle_update(update(3, 42), store, chat, ("42",))
        await handle_update(update(3, 42), store, chat, ("42",))
        assert store.conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 1
        assert store.watchlist("999") == ([], [])
    finally:
        await llm.close()


@pytest.mark.parametrize(
    "url",
    [
        "http://robu.in/product/x/",
        "https://127.0.0.1/product/x/",
        "https://169.254.169.254/latest/meta-data/",
        "https://robu.in.evil.example/product/x/",
        "https://robu.in:8443/product/x/",
        "https://secret@robu.in/product/x/",
    ],
)
def test_watch_url_boundary(registry, url):
    with pytest.raises(ValueError):
        registry.canonical(url, product=True)


def test_canonical_preserves_variant_drops_tracking(registry):
    url = "https://thinkrobotics.com/products/pi-5?utm_source=ad&variant=123#details"
    assert (
        registry.canonical(url, product=True)
        == "https://thinkrobotics.com/products/pi-5?variant=123"
    )
