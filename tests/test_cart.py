from datetime import UTC, datetime, timedelta

import pytest

from drone_assistant.app_store import AppStore
from drone_assistant.cart import INSPECT, Cart
from drone_assistant.openclaw import OpenClawError
from drone_assistant.sources import Registry


class Browser:
    def __init__(self):
        self.calls = []
        self.clicked = False
        self.fail_click = False
        self.probe = {
            "url": "https://robu.in/product/pi5/",
            "title": "Pi 5",
            "price": "₹6,000",
            "variant": "",
            "quantityEditable": True,
            "quantity": "1",
            "button": "Add to cart",
            "blocked": "",
        }

    async def browser(self, action, **args):
        self.calls.append((action, args))
        if action == "open":
            return {"targetId": "tab-1"}
        if args["request"]["fn"] == INSPECT:
            return {
                "result": self.probe
                | {"notice": "Pi 5 has been added to your cart" if self.clicked else ""}
            }
        self.clicked = True
        if self.fail_click:
            raise OpenClawError(503, "timeout after request")
        return {"result": {"clicked": True}}


@pytest.fixture
def cart():
    store = AppStore(":memory:")
    pid = store.add_product("https://robu.in/product/pi5/", "robu", "Pi 5")
    identifier = store.purchase_request(pid, 2)
    browser = Browser()
    yield Cart(store, browser, Registry()), browser, identifier
    store.close()


async def test_inspect_then_explicit_cart_confirmation_without_checkout(cart):
    worker, browser, identifier = cart
    preview = await worker.prepare(identifier)
    assert preview["state"] == "cart_ready" and not browser.clicked
    result = await worker.confirm(identifier)
    assert result["state"] == "cart_updated" and browser.clicked
    assert "No order was placed" in result["message"]
    mutations = [
        args["request"]["fn"]
        for action, args in browser.calls
        if action == "act" and args["request"]["fn"] != INSPECT
    ]
    assert len(mutations) == 1 and "String(2)" in mutations[0]
    assert "checkout" not in mutations[0] and "b.click()" in mutations[0]
    with pytest.raises(OpenClawError):
        await worker.confirm(identifier)
    with pytest.raises(OpenClawError):
        await worker.prepare(identifier)


async def test_changed_price_variant_or_url_stops_before_click(cart):
    worker, browser, identifier = cart
    await worker.prepare(identifier)
    browser.probe["price"] = "₹9,000"
    with pytest.raises(OpenClawError, match="changed since review"):
        await worker.confirm(identifier)
    assert not browser.clicked
    browser.probe["url"] = "http://169.254.169.254/metadata"
    with pytest.raises(OpenClawError):
        await worker.prepare(identifier)


async def test_ambiguous_mutation_is_not_retried(cart):
    worker, browser, identifier = cart
    await worker.prepare(identifier)
    browser.fail_click = True
    assert (await worker.confirm(identifier))["state"] == "cart_check_required"
    with pytest.raises(OpenClawError):
        await worker.confirm(identifier)
    assert (
        sum(action == "act" and args["request"]["fn"] != INSPECT for action, args in browser.calls)
        == 1
    )


async def test_unselected_variant_and_expired_preview(cart):
    worker, browser, identifier = cart
    browser.probe["blocked"] = "Choose an exact variant first."
    assert (await worker.prepare(identifier))["state"] == "browser_action_required"
    assert not browser.clicked
    browser.probe["blocked"] = ""
    await worker.prepare(identifier)
    worker.store.conn.execute(
        "UPDATE purchase_requests SET prepared_at=? WHERE id=?",
        ((datetime.now(UTC) - timedelta(minutes=6)).isoformat(), identifier),
    )
    worker.store.conn.commit()
    with pytest.raises(OpenClawError, match="expired"):
        await worker.confirm(identifier)
    assert not browser.clicked


def test_crash_recovery_never_replays_cart_mutation(tmp_path):
    path = str(tmp_path / "app.db")
    store = AppStore(path)
    pid = store.add_product("https://robu.in/product/pi5/", "robu", "Pi 5")
    identifier = store.purchase_request(pid, 1)
    store.conn.execute(
        "UPDATE purchase_requests SET state='cart_submitting' WHERE id=?", (identifier,)
    )
    store.conn.commit()
    store.close()
    recovered = AppStore(path)
    assert (
        recovered.conn.execute(
            "SELECT state FROM purchase_requests WHERE id=?", (identifier,)
        ).fetchone()[0]
        == "cart_check_required"
    )
    recovered.close()
