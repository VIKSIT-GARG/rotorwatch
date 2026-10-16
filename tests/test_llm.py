import json
from dataclasses import replace

import httpx
import pytest

from drone_assistant.config import Settings
from drone_assistant.domain import Snapshot, Status
from drone_assistant.llm import SmallLLM


def client_with(payload, requests):
    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(payload)}}]}
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False)


async def test_llm_verified_quote_classifies_and_cache_avoids_calls(store):
    requests = []
    client = client_with(
        {
            "status": "in_stock",
            "confidence": 0.93,
            "evidence": "Dispatching immediately",
            "attributes": {"interface": "PCIe"},
        },
        requests,
    )
    settings = replace(Settings(), llm_api_key="test-key", llm_daily_limit=1)
    llm = SmallLLM(settings, store, client)
    try:
        for _ in range(2):
            snapshot = await llm.classify(Snapshot("Pi 5"), "Pi 5 PCIe. Dispatching immediately.")
            assert snapshot.status == Status.IN_STOCK and snapshot.method == "small_llm"
        assert len(requests) == 1
        other = await llm.classify(Snapshot("Other board"), "Other board Dispatching immediately.")
        assert other.status == Status.UNKNOWN and len(requests) == 1
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "in_stock", "confidence": 0.99, "evidence": "Not actually on page"},
        {"status": "in_stock", "confidence": 0.5, "evidence": "Ships today"},
        {"status": "invented_status", "confidence": 0.99, "evidence": "Ships today"},
        {"status": "in_stock", "confidence": True, "evidence": "Ships today"},
        {"status": "in_stock", "confidence": 1.3, "evidence": "Ships today"},
    ],
)
async def test_llm_hallucination_and_invalid_output_rejected(store, payload):
    client = client_with(payload, [])
    llm = SmallLLM(replace(Settings(), llm_api_key="test-key"), store, client)
    try:
        assert (await llm.classify(Snapshot("Pi 5"), "Pi 5. Ships today.")).status == Status.UNKNOWN
    finally:
        await client.aclose()


async def test_llm_cannot_resolve_conflict_or_variant_ambiguity(store):
    requests = []
    client = client_with(
        {"status": "in_stock", "confidence": 1, "evidence": "Ships today"}, requests
    )
    llm = SmallLLM(replace(Settings(), llm_api_key="test-key"), store, client)
    try:
        for method in ("conflict", "variant_selection_required", "shopify_ajax"):
            assert (
                await llm.classify(Snapshot("Pi 5", method=method), "Ships today")
            ).status == Status.UNKNOWN
        assert requests == []
    finally:
        await client.aclose()


async def test_intent_cannot_invent_url_or_product_id(store):
    client = client_with(
        {"action": "watch", "query": "https://robu.in/product/invented/", "ids": []}, []
    )
    llm = SmallLLM(replace(Settings(), llm_api_key="test-key"), store, client)
    try:
        assert await llm.interpret("Watch a Pi 5") is None
    finally:
        await client.aclose()
