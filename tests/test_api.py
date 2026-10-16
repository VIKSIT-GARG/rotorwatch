from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from drone_assistant.api import create_app
from drone_assistant.config import Settings

TOKEN = "test-workspace-token-" + "x" * 32


@pytest.fixture
def demo():
    with TestClient(create_app(Settings(), demo=True, start_workers=False)) as client:
        yield client


def test_live_requires_auth_and_exact_cors(tmp_path):
    settings = replace(
        Settings(),
        database=str(tmp_path / "app.db"),
        api_token=TOKEN,
        cors_origins=("https://dashboard.example",),
    )
    with TestClient(create_app(settings, start_workers=False)) as client:
        assert client.get("/health").json() == {"ok": True, "mode": "live"}
        assert client.get("/api/state").status_code == 401
        assert (
            client.get("/api/state", headers={"Authorization": "Bearer wrong"}).status_code == 401
        )
        headers = {"Authorization": f"Bearer {TOKEN}", "Origin": "https://dashboard.example"}
        response = client.get("/api/state", headers=headers)
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "https://dashboard.example"
        assert response.headers["cache-control"] == "no-store"
        assert TOKEN not in response.text
        state = response.json()
        assert len(state["products"]) == 5 and state["mode"] == "live"
        assert (
            client.get("/api/state?version=" + state["version"], headers=headers).status_code == 204
        )
        bad = client.options(
            "/api/state",
            headers={"Origin": "https://other.example", "Access-Control-Request-Method": "POST"},
        )
        assert bad.status_code == 400 and "access-control-allow-origin" not in bad.headers


def test_live_rejects_short_secret():
    with pytest.raises(ValueError, match="API_TOKEN"):
        create_app(Settings())


def test_web_assets_and_synthetic_changes(demo):
    assert "RotorWatch" in demo.get("/").text
    assert "renderChrome" in demo.get("/app.js").text
    before = demo.get("/api/state").json()
    assert len(before["products"]) == 8 and before["mode"] == "demo"
    assert demo.post("/api/scans", json={}).status_code == 202
    after = demo.get("/api/state").json()
    assert before["products"][0]["status"] != after["products"][0]["status"]
    assert after["products"][1]["price_minor"] == before["products"][1]["price_minor"] - 10000
    assert after["version"] != before["version"]
    assert any(e["kind"] == "stock" for e in after["events"])
    assert any(e["kind"] == "price" for e in after["events"])
    assert after["alerts"] == []
    assert len(demo.get("/api/products/1/history").json()["observations"]) == 2


def test_watch_mutations_and_bad_inputs(demo):
    assert demo.delete("/api/watchlist/1").status_code == 200
    assert not demo.get("/api/state").json()["products"][0]["watched"]
    assert demo.post("/api/watchlist", json={"product_id": 1}).status_code == 200
    assert demo.get("/api/state").json()["products"][0]["watched"]
    assert demo.post("/api/watchlist", json={"product_id": 9999}).status_code == 404
    assert (
        demo.post("/api/watchlist", json={"query": "https://evil.example/product/x"}).status_code
        == 422
    )
    assert demo.post("/api/watchlist", json={"query": "http://127.0.0.1/x"}).status_code == 422
    assert demo.post("/api/watchlist", json={"query": " "}).status_code == 422
    assert demo.post("/api/watchlist", json={"query": "Pi 5", "product_id": 1}).status_code == 422
    response = demo.post(
        "/api/watchlist", json={"query": "https://robu.in/product/test-specific-hat/"}
    )
    assert response.status_code == 200
    assert demo.get("/api/state").json()["products"][-1]["status"] == "unknown"
    assert demo.post("/api/watchlist", json={"query": "Custom new carrier"}).status_code == 200
    rules = demo.get("/api/state").json()["rules"]
    rule = next(r for r in rules if r["query"] == "Custom new carrier")
    assert demo.delete(f"/api/rules/{rule['id']}").status_code == 200
    assert all(r["id"] != rule["id"] for r in demo.get("/api/state").json()["rules"])


def test_chat_uses_database_and_persists_history(demo):
    reply = demo.post(
        "/api/chat",
        json={"message": "find Raspberry Pi 5 in stock under 10000", "session_id": "test"},
    ).json()
    assert reply["products"] and not reply["model_used"]
    assert all(p["status"] == "in_stock" and p["price_minor"] <= 1000000 for p in reply["products"])
    assert (
        demo.post("/api/chat", json={"message": "Show my watchlist"}).json()["action"]
        == "watchlist"
    )
    assert (
        demo.post("/api/chat", json={"message": "What changed recently?"}).json()["action"]
        == "activity"
    )
    history = demo.get("/api/chat?session_id=test").json()["messages"]
    assert [m["role"] for m in history] == ["user", "assistant"]
    assert history[1]["product_ids"]
    assert demo.post("/api/chat", json={"message": "DROP TABLE products;"}).status_code == 200
    assert len(demo.get("/api/state").json()["products"]) == 8
    assert demo.post("/api/chat", json={"message": ""}).status_code == 422


def test_preferences_and_honest_purchase_review(demo):
    assert demo.patch("/api/preferences", json={"whatsapp": True}).status_code == 409
    assert demo.patch("/api/preferences", json={"telegram": True}).status_code == 409
    assert (
        demo.patch("/api/preferences", json={"price": False}).json()["preferences"]["price"]
        is False
    )
    result = demo.post("/api/purchases", json={"product_id": 1, "quantity": 2})
    assert result.status_code == 201 and result.json()["state"] == "browser_session_required"
    assert demo.get("/api/state").json()["purchases"][0]["quantity"] == 2
    assert demo.post("/api/purchases", json={"product_id": 999}).status_code == 404
    assert demo.post("/api/purchases", json={"product_id": 1, "quantity": 0}).status_code == 422


def test_restart_preserves_removed_seed_rule(tmp_path):
    settings = replace(Settings(), database=str(tmp_path / "app.db"), api_token=TOKEN)
    headers = {"Authorization": f"Bearer {TOKEN}"}
    for attempt in range(2):
        with TestClient(create_app(settings, start_workers=False), headers=headers) as client:
            if attempt == 0:
                client.delete("/api/rules/1")
            assert all(r["id"] != 1 for r in client.get("/api/state").json()["rules"])
