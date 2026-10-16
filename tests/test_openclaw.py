import json
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

from drone_assistant.api import create_app
from drone_assistant.app_store import WEB_OWNER, AppStore
from drone_assistant.config import Settings
from drone_assistant.domain import Snapshot, Status
from drone_assistant.llm import SmallLLM
from drone_assistant.notifications import Notifications, WhatsApp
from drone_assistant.openclaw import OpenClaw, OpenClawError
from drone_assistant.telegram import TelegramError


def openclaw_settings(**kwargs):
    return replace(
        Settings(),
        notification_transport="openclaw",
        openclaw_token="gateway-secret",
        allowed_chat_ids=("42",),
        whatsapp_to="919900000000",
        **kwargs,
    )


async def test_one_change_goes_to_both_openclaw_channels_without_direct_tokens():
    store = AppStore(":memory:")
    settings = openclaw_settings()
    calls = []

    def handler(request):
        assert request.url.path == "/tools/invoke"
        assert request.headers["authorization"] == "Bearer gateway-secret"
        body = json.loads(request.content)
        calls.append(body)
        return httpx.Response(
            200, json={"ok": True, "result": {"details": {"messageId": "channel-message"}}}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gateway = OpenClaw(settings, client)
        llm, wa = SmallLLM(settings, store), WhatsApp(settings, client)
        notifications = Notifications(settings, store, llm, None, wa, gateway)
        store.set_preferences({"telegram": True, "whatsapp": True})
        pid = store.add_product("https://robu.in/product/pi5/", "robu", "Pi 5")
        store.watch(WEB_OWNER, pid)
        store.observe(pid, Snapshot("Pi 5", Status.OUT_OF_STOCK, 600000))
        await notifications.summarize_one()
        store.observe(pid, Snapshot("Pi 5", Status.IN_STOCK, 590000))
        await notifications.summarize_one()
        assert await notifications.deliver_one()
        assert await notifications.deliver_one()
        assert not await notifications.deliver_one()
        assert {c["args"]["channel"] for c in calls} == {"telegram", "whatsapp"}
        assert {c["args"]["target"] for c in calls} == {"42", "+919900000000"}
        assert all(
            c["args"]["action"] == "send" and c["idempotencyKey"].startswith("rotorwatch-outbox-")
            for c in calls
        )
        assert len({c["args"]["message"] for c in calls}) == 1
        assert (
            store.conn.execute("SELECT COUNT(*) FROM outbox WHERE state='sent'").fetchone()[0] == 2
        )
        await llm.close()
    store.close()


@pytest.mark.parametrize(
    "payload",
    [
        {"ok": False},
        {"ok": True, "result": {"isError": True}},
        {"ok": True, "result": {"details": {"ok": True}}},
    ],
)
async def test_unconfirmed_or_unpaired_gateway_never_marks_delivery_success(payload):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    ) as client:
        gateway = OpenClaw(openclaw_settings(), client)
        with pytest.raises(TelegramError):
            await gateway.send("whatsapp", "919900000000", "test")


async def test_gateway_cannot_be_used_as_arbitrary_tool_or_recipient_proxy():
    calls = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: calls.append(r))
    ) as client:
        gateway = OpenClaw(openclaw_settings(), client)
        with pytest.raises(OpenClawError):
            await gateway.invoke("exec", {"command": "anything"})
        with pytest.raises(TelegramError):
            await gateway.send("telegram", "9999", "hello")
        assert calls == []


def test_both_channels_test_queue_and_demo_isolation(tmp_path):
    settings = openclaw_settings(
        database=str(tmp_path / "app.db"), api_token="x" * 32, telegram_token="bot-token"
    )
    with TestClient(
        create_app(settings, start_workers=False),
        headers={"Authorization": "Bearer " + settings.api_token},
    ) as client:
        state = client.get("/api/state").json()
        assert state["capabilities"]["telegram"] and state["capabilities"]["whatsapp"]
        assert state["capabilities"]["notification_transport"] == "openclaw"
        assert client.app.state.engine.telegram is None  # OpenClaw owns the Telegram poller
        assert "gateway-secret" not in json.dumps(state) and "bot-token" not in json.dumps(state)
        assert client.post("/api/integrations/test-notifications").status_code == 202
        assert {a["channel"] for a in client.get("/api/state").json()["alerts"]} == {
            "telegram",
            "whatsapp",
        }
        assert client.post("/api/integrations/test-notifications").status_code == 429
    with TestClient(create_app(settings, demo=True, start_workers=False)) as client:
        assert not client.get("/api/state").json()["capabilities"]["openclaw_configured"]
        assert client.post("/api/integrations/test-notifications").status_code == 409
        request = client.post("/api/purchases", json={"product_id": 1}).json()
        assert client.post(f"/api/purchases/{request['id']}/prepare-browser").status_code == 409
