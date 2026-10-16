import json
from dataclasses import replace

import httpx
import pytest

from drone_assistant.app_store import WEB_OWNER, AppStore
from drone_assistant.config import Settings
from drone_assistant.domain import Snapshot, Status
from drone_assistant.llm import SmallLLM
from drone_assistant.notifications import Notifications, WhatsApp, event_message
from drone_assistant.storage import Store
from drone_assistant.telegram import TelegramError


@pytest.fixture
def app_store():
    store = AppStore(":memory:")
    yield store
    store.close()


async def test_semantic_events_summaries_and_exactly_one_per_channel(app_store):
    settings = replace(
        Settings(),
        allowed_chat_ids=("42",),
        whatsapp_token="fake",
        whatsapp_phone_id="12345",
        whatsapp_to="919900000000",
        whatsapp_template="stock_update",
        whatsapp_graph_version="v25.0",
    )
    pid = app_store.add_product("https://robu.in/product/pi-5/", "robu", "Pi 5")
    app_store.watch(WEB_OWNER, pid)
    app_store.watch("42", pid)
    app_store.set_preferences({"whatsapp": True})
    llm = SmallLLM(settings, app_store)
    sent = []

    class Bot:
        async def send(self, target, text):
            sent.append((target, text))

    def handler(request):
        body = json.loads(request.content)
        assert body["template"]["name"] == "stock_update"
        assert body["template"]["components"][0]["parameters"][0]["text"]
        sent.append((body["to"], body))
        return httpx.Response(200, json={"messages": [{"id": "test-message"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        wa = WhatsApp(settings, client)
        worker = Notifications(settings, app_store, llm, Bot(), wa)
        app_store.observe(pid, Snapshot("Pi 5", Status.OUT_OF_STOCK, 700000))
        assert await worker.summarize_one()  # initial baseline is recorded but quiet
        assert not await worker.deliver_one()
        app_store.observe(pid, Snapshot("Pi 5", Status.OUT_OF_STOCK, 700000))
        assert not app_store.pending_events()  # timestamp-only update
        app_store.observe(pid, Snapshot("Pi 5", Status.IN_STOCK, 650000))
        assert not await worker.deliver_one()  # legacy raw alert waits for summary
        event = app_store.pending_events()[0]
        assert json.loads(event["facts"])["changes"] == ["stock", "price"]
        assert await worker.summarize_one()
        app_store.complete_event(
            event,
            "duplicate completion",
            "template",
            [("telegram", "42"), ("whatsapp", settings.whatsapp_to)],
        )
        assert await worker.deliver_one()
        assert await worker.deliver_one()
        assert not await worker.deliver_one()
        assert len(sent) == 2 and {s[0] for s in sent} == {"42", settings.whatsapp_to}
        assert "₹6,500" in sent[0][1] or isinstance(sent[0][1], dict)
        assert (
            app_store.conn.execute("SELECT COUNT(*) FROM outbox WHERE state='sent'").fetchone()[0]
            == 2
        )
    await llm.close()


async def test_invalid_model_wording_replaced_by_factual_template(app_store):
    pid = app_store.add_product("https://robu.in/product/pi-5/", "robu", "Pi 5")
    app_store.observe(pid, Snapshot("Pi 5", Status.IN_STOCK, 123400))

    class BadModel:
        async def _json(self, *args):
            return {"message": "Order placed for zero rupees!"}

    text, method = await event_message(BadModel(), app_store.pending_events()[0])
    assert method == "template" and "Order placed" not in text
    assert "₹1,234" in text and "in stock" in text


async def test_whatsapp_failures_retry_and_secrets_stay_private(app_store):
    settings = replace(
        Settings(),
        whatsapp_token="hidden-token",
        whatsapp_phone_id="123",
        whatsapp_to="919900000000",
        whatsapp_template="stock",
        whatsapp_graph_version="v25.0",
    )
    app_store.enqueue(settings.whatsapp_to, "Price changed", "test")
    app_store.conn.execute("UPDATE outbox SET channel='whatsapp'")
    app_store.conn.commit()
    llm = SmallLLM(settings, app_store)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(503, json={}))
    ) as client:
        wa = WhatsApp(settings, client)
        worker = Notifications(settings, app_store, llm, None, wa)
        assert await worker.deliver_one()
        row = dict(app_store.conn.execute("SELECT * FROM outbox").fetchone())
        assert row["state"] == "pending" and row["attempts"] == 1
        with pytest.raises(TelegramError):
            await wa.send("unapproved-recipient", "hello")
    state = json.dumps(app_store.dashboard())
    assert "hidden-token" not in state and settings.whatsapp_to not in state
    await llm.close()


def test_schema_upgrade_keeps_history_and_does_not_downgrade(tmp_path):
    path = str(tmp_path / "old.db")
    old = Store(path)
    pid = old.add_product("https://robu.in/product/pi-5/", "robu", "Pi 5")
    old.watch("42", pid)
    old.observe(pid, Snapshot("Pi 5", Status.OUT_OF_STOCK))
    old.close()
    upgraded = AppStore(path)
    assert len(upgraded.history(pid)) == 1 and upgraded.subscribers(pid) == ["42"]
    assert upgraded.conn.execute("PRAGMA user_version").fetchone()[0] == 3
    upgraded.close()
    reopened = Store(path)
    assert reopened.conn.execute("PRAGMA user_version").fetchone()[0] == 3
    reopened.close()
