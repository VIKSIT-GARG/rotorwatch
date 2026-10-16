import json
from dataclasses import replace

import httpx

from drone_assistant.app_store import AppStore
from drone_assistant.chat import Chat
from drone_assistant.config import Settings
from drone_assistant.domain import Snapshot, Status
from drone_assistant.llm import SmallLLM
from drone_assistant.sources import Registry
from drone_assistant.web_chat import WebChat


async def test_model_intent_fetches_database_then_model_receives_only_matching_rows():
    store = AppStore(":memory:")
    pid = store.add_product("https://robu.in/product/pi5/", "robu", "Raspberry Pi 5 4GB")
    store.observe(pid, Snapshot("Raspberry Pi 5 4GB", Status.IN_STOCK, 699900))
    other = store.add_product("https://robu.in/product/expensive/", "robu", "Raspberry Pi 5 8GB")
    store.observe(other, Snapshot("Raspberry Pi 5 8GB", Status.IN_STOCK, 1299900))
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        if len(calls) == 1:
            result = {
                "action": "search",
                "query": "Pi 5",
                "status": "in_stock",
                "max_price_minor": 1000000,
            }
        else:
            payload = json.loads(body["messages"][-1]["content"])
            assert [p["id"] for p in payload["records"]] == [pid]
            assert payload["records"][0]["price_minor"] == 699900
            result = {
                "answer": "P1 was recorded in stock for ₹6,999 at its latest check.",
                "product_ids": [pid],
            }
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(result)}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        llm = SmallLLM(replace(Settings(), llm_api_key="fake-key"), store, client)
        chat = WebChat(store, llm, Chat(store, Registry(), llm))
        result = await chat.ask("Find Pi 5 in stock under 10000", "test")
        assert result["model_used"] and len(calls) == 2
        assert [p["id"] for p in result["products"]] == [pid]
        assert len(store.messages("test")) == 2
    store.close()
