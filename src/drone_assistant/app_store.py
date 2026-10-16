from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

from .domain import utcnow
from .storage import Store

WEB_OWNER = "web"
APP_SCHEMA = """
CREATE TABLE IF NOT EXISTS change_events (
 id INTEGER PRIMARY KEY, observation_id INTEGER NOT NULL UNIQUE,
 product_id INTEGER NOT NULL REFERENCES products(id), kind TEXT NOT NULL,
 facts TEXT NOT NULL, created_at TEXT NOT NULL,
 summary TEXT, summary_method TEXT, processed INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS web_messages (
 id INTEGER PRIMARY KEY, session_id TEXT NOT NULL, role TEXT NOT NULL,
 content TEXT NOT NULL, product_ids TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS purchase_requests (
 id INTEGER PRIMARY KEY, product_id INTEGER NOT NULL REFERENCES products(id),
 quantity INTEGER NOT NULL, state TEXT NOT NULL DEFAULT 'browser_session_required',
 created_at TEXT NOT NULL, note TEXT NOT NULL
);
"""


class AppStore(Store):
    def __init__(self, path: str):
        super().__init__(path)
        self.conn.executescript(APP_SCHEMA)
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(outbox)")}
        if "channel" not in columns:
            self.conn.execute(
                "ALTER TABLE outbox ADD COLUMN channel TEXT NOT NULL DEFAULT 'telegram'"
            )
        purchase_columns = {
            row[1] for row in self.conn.execute("PRAGMA table_info(purchase_requests)")
        }
        for column in ("browser_target", "browser_probe", "browser_fingerprint", "prepared_at"):
            if column not in purchase_columns:
                self.conn.execute(f"ALTER TABLE purchase_requests ADD COLUMN {column} TEXT")
        self.conn.execute(
            "UPDATE purchase_requests SET state='cart_check_required',note='The service restarted during a cart request. Check the retailer cart before making another request.' WHERE state='cart_submitting'"
        )
        self.conn.execute("PRAGMA user_version=3")
        self.conn.execute(
            "UPDATE outbox SET state='superseded' WHERE state='pending' AND dedupe_key LIKE 'availability:%'"
        )
        self.conn.commit()

    def _after_observation(self, before: dict, after: dict, observation_id: int, new_match: bool):
        changes = []
        if before["confirmed_status"] and after["confirmed_status"] != before["confirmed_status"]:
            changes.append("stock")
        if before["price_minor"] is not None and after["price_minor"] != before["price_minor"]:
            changes.append("price")
        if before["last_checked"] and (
            before["attributes"] != after["attributes"] or before["title"] != after["title"]
        ):
            changes.append("specifications")
        if after["status"] == "unknown" and (
            before["status"] != "unknown" or before["error"] != after["error"]
        ):
            changes.append("verification")
        if (
            not changes
            and before["status"] == "unknown"
            and after["status"] != "unknown"
            and before["confirmed_status"]
        ):
            changes.append("verified")
        if new_match:
            kind = "new_match"
        elif before["confirmed_status"] is None and after["confirmed_status"] is not None:
            kind = "baseline"
        elif changes:
            kind = changes[0]
        else:
            return  # last_checked and identical rechecks are not business-data changes.
        facts = {
            "product_id": after["id"],
            "title": after["title"],
            "url": after["url"],
            "source": after["source"],
            "status": after["status"],
            "previous_status": before["confirmed_status"],
            "price_minor": after["price_minor"],
            "previous_price_minor": before["price_minor"],
            "currency": after["currency"],
            "changes": changes,
            "checked_at": after["last_checked"],
            "error": after["error"],
        }
        self.conn.execute(
            "INSERT OR IGNORE INTO change_events(observation_id,product_id,kind,facts,created_at) VALUES (?,?,?,?,?)",
            (observation_id, after["id"], kind, json.dumps(facts), utcnow()),
        )
        self.conn.execute(
            "UPDATE outbox SET state='awaiting_summary' WHERE state='pending' AND dedupe_key LIKE ?",
            (f"availability:{after['id']}:{observation_id}:%",),
        )

    def pending_events(self, limit: int = 20) -> list[dict]:
        return [
            dict(r)
            for r in self.conn.execute(
                "SELECT * FROM change_events WHERE processed=0 ORDER BY id LIMIT ?", (limit,)
            )
        ]

    def recent_changes(self, *, hours: int = 24, now: datetime | None = None) -> list[dict]:
        now = now or datetime.now(UTC)
        cutoff = now - timedelta(hours=hours)
        events = []
        for row in self.conn.execute(
            "SELECT * FROM change_events WHERE kind NOT IN ('baseline','verified') "
            "AND julianday(created_at)>=julianday(?) AND julianday(created_at)<=julianday(?) "
            "ORDER BY julianday(created_at) DESC,id DESC LIMIT 8",
            (cutoff.isoformat(), now.isoformat()),
        ):
            event = dict(row)
            event["facts"] = json.loads(event["facts"])
            events.append(event)
        return events

    def preferences(self) -> dict:
        defaults = {
            "telegram": True,
            "whatsapp": False,
            "stock": True,
            "price": True,
            "new_match": True,
            "specifications": True,
            "verification": False,
        }
        saved = json.loads(self.get_meta("web_preferences", "{}"))
        return defaults | saved

    def set_preferences(self, values: dict):
        self.set_meta("web_preferences", json.dumps(self.preferences() | values))

    def complete_event(
        self, event: dict, summary: str, method: str, recipients: list[tuple[str, str]]
    ):
        with self.conn:
            # Supersede the CLI-compatible raw alert for this exact observation. The v2
            # service sends its summarized event instead, without duplicate raw deliveries.
            prefix = f"availability:{event['product_id']}:{event['observation_id']}:%"
            self.conn.execute(
                "UPDATE outbox SET state='superseded' WHERE dedupe_key LIKE ? AND state IN ('pending','awaiting_summary')",
                (prefix,),
            )
            for channel, target in sorted(set(recipients)):
                key = f"event:{event['id']}:{channel}:{target}"
                self._enqueue(target, summary, key)
                self.conn.execute(
                    "UPDATE outbox SET channel=? WHERE dedupe_key=? OR dedupe_key LIKE ?",
                    (channel, key, key + ":part:%"),
                )
            self.conn.execute(
                "UPDATE change_events SET summary=?,summary_method=?,processed=1 WHERE id=?",
                (summary, method, event["id"]),
            )

    def add_message(self, session_id: str, role: str, content: str, ids: list[int] | None = None):
        with self.conn:
            self.conn.execute(
                "INSERT INTO web_messages(session_id,role,content,product_ids,created_at) VALUES (?,?,?,?,?)",
                (session_id, role, content[:12000], json.dumps(ids or []), utcnow()),
            )

    def messages(self, session_id: str, limit: int = 40) -> list[dict]:
        return [
            dict(r) | {"product_ids": json.loads(r["product_ids"])}
            for r in reversed(
                self.conn.execute(
                    "SELECT * FROM web_messages WHERE session_id=? ORDER BY id DESC LIMIT ?",
                    (session_id, limit),
                ).fetchall()
            )
        ]

    def purchase_request(self, product_id: int, quantity: int) -> int:
        if self.product(product_id) is None:
            raise ValueError("Unknown product.")
        with self.conn:
            return self.conn.execute(
                "INSERT INTO purchase_requests(product_id,quantity,created_at,note) VALUES (?,?,?,?)",
                (
                    product_id,
                    quantity,
                    utcnow(),
                    "Saved for review. A retailer login and configured OpenClaw/Playwright worker are required to automate the cart. No order has been placed.",
                ),
            ).lastrowid

    def public_product(self, product: dict, watched: set[int] | None = None) -> dict:
        attrs = json.loads(product["attributes"])
        price_observation = self.conn.execute(
            "SELECT observed_at FROM observations WHERE product_id=? AND price_minor IS NOT NULL ORDER BY id DESC LIMIT 1",
            (product["id"],),
        ).fetchone()
        last_observation = self.conn.execute(
            "SELECT price_minor FROM observations WHERE product_id=? ORDER BY id DESC LIMIT 1",
            (product["id"],),
        ).fetchone()
        latest = self.conn.execute(
            "SELECT facts FROM change_events WHERE product_id=? AND kind='price' ORDER BY id DESC LIMIT 1",
            (product["id"],),
        ).fetchone()
        facts = json.loads(latest[0]) if latest else {}
        return {
            key: product[key]
            for key in (
                "id",
                "title",
                "url",
                "source",
                "category",
                "status",
                "confirmed_status",
                "confirmed_at",
                "last_checked",
                "price_minor",
                "currency",
                "error",
            )
        } | {
            "attributes": attrs,
            "price_checked_at": price_observation[0] if price_observation else None,
            "price_stale": bool(
                product["price_minor"] is not None
                and (not last_observation or last_observation[0] is None)
            ),
            "watched": product["id"] in (watched or set()),
            "previous_price_minor": facts.get("previous_price_minor"),
        }

    def dashboard(self) -> dict:
        watched_rows, rules = self.watchlist(WEB_OWNER)
        watched = {p["id"] for p in watched_rows}
        products = [
            self.public_product(dict(r), watched)
            for r in self.conn.execute("SELECT * FROM products ORDER BY id")
        ]
        events = []
        for row in self.conn.execute("SELECT * FROM change_events ORDER BY id DESC LIMIT 30"):
            event = dict(row)
            event["facts"] = json.loads(event["facts"])
            events.append(event)
        alerts = []
        for row in self.conn.execute(
            "SELECT id,chat_id,text,state,channel,error,created_at,sent_at FROM outbox WHERE state!='superseded' AND chat_id!='web' ORDER BY id DESC LIMIT 30"
        ):
            alert = dict(row)
            # Display channel and delivery state, not private phone/chat destinations.
            alert["recipient"] = "…" + alert.pop("chat_id")[-4:]
            alerts.append(alert)
        counts = {
            status: sum(p["status"] == status and p["watched"] for p in products)
            for status in ("in_stock", "out_of_stock", "pre_order", "unknown")
        }
        runs = [dict(r) for r in self.conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 10")]
        requests = [
            dict(r)
            for r in self.conn.execute(
                "SELECT r.*,p.title,p.url,p.price_minor,p.currency FROM purchase_requests r JOIN products p ON p.id=r.product_id ORDER BY r.id DESC LIMIT 50"
            )
        ]
        since = (datetime.now(UTC) - timedelta(days=6)).date().isoformat()
        timeline = []
        for row in self.conn.execute(
            "SELECT substr(observed_at,1,10) day,COUNT(*) checks,SUM(status='in_stock') in_stock FROM observations WHERE observed_at>=? GROUP BY day ORDER BY day",
            (since,),
        ):
            timeline.append(dict(row))
        state = {
            "products": products,
            "rules": rules,
            "events": events,
            "alerts": alerts,
            "runs": runs,
            "purchases": requests,
            "preferences": self.preferences(),
            "timeline": timeline,
            "stats": {
                "watched": len(watched),
                "catalog": len(products),
                "counts": counts,
                "delivered": self.conn.execute(
                    "SELECT COUNT(*) FROM outbox WHERE state='sent'"
                ).fetchone()[0],
            },
            "last_scan": self.get_meta("last_full_scan"),
            "heartbeat": self.get_meta("heartbeat"),
        }
        state["version"] = hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()[
            :20
        ]
        return state
