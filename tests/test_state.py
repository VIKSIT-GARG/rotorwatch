import pytest

from drone_assistant.domain import Snapshot, Status
from drone_assistant.storage import Store
from drone_assistant.telegram import TelegramError, deliver_one


def count_alerts(store):
    return store.conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]


@pytest.mark.parametrize("initial", list(Status))
def test_initial_observation_is_baseline(store, product, initial):
    store.observe(product, Snapshot("Pi 5", initial))
    assert count_alerts(store) == 0


@pytest.mark.parametrize("target", [Status.IN_STOCK, Status.PRE_ORDER])
def test_unknown_between_known_states_does_not_hide_restock(store, product, target):
    store.observe(product, Snapshot("Pi 5", Status.OUT_OF_STOCK))
    store.observe(product, Snapshot("Pi 5", error="http_403"))
    assert store.product(product)["confirmed_status"] == Status.OUT_OF_STOCK
    assert store.product(product)["status"] == Status.UNKNOWN
    store.observe(product, Snapshot("Pi 5", target))
    assert count_alerts(store) == 1
    store.observe(product, Snapshot("Pi 5", target))
    assert count_alerts(store) == 1


def test_unknown_after_in_stock_does_not_create_false_restock(store, product):
    store.observe(product, Snapshot("Pi 5", Status.IN_STOCK))
    store.observe(product, Snapshot("Pi 5", error="timeout"))
    store.observe(product, Snapshot("Pi 5", Status.IN_STOCK))
    assert count_alerts(store) == 0


def test_preorder_to_in_stock_alerts_once(store, product):
    store.observe(product, Snapshot("Pi 5", Status.PRE_ORDER))
    store.observe(product, Snapshot("Pi 5", Status.IN_STOCK))
    store.observe(product, Snapshot("Pi 5", Status.PRE_ORDER))
    assert count_alerts(store) == 1


def test_price_change_alone_is_not_restock(store, product):
    store.observe(product, Snapshot("Pi 5", Status.IN_STOCK, price_minor=700000))
    store.observe(product, Snapshot("Pi 5", Status.IN_STOCK, price_minor=600000))
    assert count_alerts(store) == 0


def test_new_listing_alert_only_after_baseline(store, product):
    rid = store.add_rule("Pi 5")
    store.subscribe_rule("42", rid)
    store.link_discoveries(rid, "robu", [product], complete=True)
    store.observe(product, Snapshot("Pi 5", Status.IN_STOCK))
    assert count_alerts(store) == 0
    new = store.add_product("https://robu.in/product/pi-5-new/", "robu", "Pi 5 new")
    store.link_discoveries(rid, "robu", [new], complete=True)
    store.observe(new, Snapshot("Pi 5 new", Status.PRE_ORDER))
    assert count_alerts(store) == 1
    assert "NEW MATCH AVAILABLE" in store.conn.execute("SELECT text FROM outbox").fetchone()[0]


def test_incomplete_listing_does_not_establish_baseline(store, product):
    rid = store.add_rule("Pi 5")
    store.subscribe_rule("42", rid)
    store.link_discoveries(rid, "robu", [], complete=False)
    store.link_discoveries(rid, "robu", [product], complete=True)
    store.observe(product, Snapshot("Pi 5", Status.IN_STOCK))
    assert count_alerts(store) == 0


def test_unwatch_excludes_rule_alerts_and_rewatch_restores(store, product):
    rid = store.add_rule("Pi 5")
    store.subscribe_rule("42", rid)
    store.link_discoveries(rid, "robu", [product], complete=True)
    store.unwatch("42", "P", product)
    assert store.subscribers(product) == []
    store.watch("42", product)
    assert store.subscribers(product) == ["42"]


def test_history_and_outbox_survive_restart(tmp_path):
    path = str(tmp_path / "state.sqlite3")
    first = Store(path)
    pid = first.add_product("https://robu.in/product/pi-5/", "robu", "Pi 5")
    first.watch("42", pid)
    first.observe(pid, Snapshot("Pi 5", Status.OUT_OF_STOCK))
    first.observe(pid, Snapshot("Pi 5", Status.IN_STOCK))
    first.close()
    second = Store(path)
    assert second.product(pid)["confirmed_status"] == Status.IN_STOCK
    assert len(second.history(pid)) == 2
    assert second.claim_message()["chat_id"] == "42"
    assert second.claim_message() is None
    second.close()


def test_long_replies_split_without_content_loss(store):
    text = ("A product detail line\n" * 800) + "END"
    store.enqueue("42", text, "reply:1")
    store.enqueue("42", text, "reply:1")
    parts = [r[0] for r in store.conn.execute("SELECT text FROM outbox ORDER BY id")]
    assert len(parts) > 1 and all(len(p) <= 3800 for p in parts)
    assert "".join(parts) == text


async def test_transient_delivery_retried_and_then_sent(store):
    class Transport:
        fail = True

        async def send(self, chat_id, text):
            if self.fail:
                raise TelegramError(429, retry_after=0)

    transport = Transport()
    store.enqueue("42", "hello")
    await deliver_one(transport, store)
    assert store.conn.execute("SELECT state FROM outbox").fetchone()[0] == "pending"
    transport.fail = False
    await deliver_one(transport, store)
    assert store.conn.execute("SELECT state FROM outbox").fetchone()[0] == "sent"


async def test_blocked_bot_deadletters_without_retry_loop(store):
    class Transport:
        async def send(self, chat_id, text):
            raise TelegramError(403)

    store.enqueue("42", "hello")
    await deliver_one(Transport(), store)
    assert store.conn.execute("SELECT state FROM outbox").fetchone()[0] == "failed"
    assert store.claim_message() is None


async def test_removed_allowlist_member_does_not_receive_queued_message(store):
    class Transport:
        async def send(self, chat_id, text):
            raise AssertionError("Delivery to a revoked chat must not be attempted")

    store.enqueue("42", "old alert")
    await deliver_one(Transport(), store, ("123",))
    assert store.conn.execute("SELECT state FROM outbox").fetchone()[0] == "failed"
