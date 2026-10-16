from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .domain import FAVORABLE, Intent, Snapshot, Status, category_for, query_matches, utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
 id INTEGER PRIMARY KEY, url TEXT NOT NULL UNIQUE, source TEXT NOT NULL,
 title TEXT NOT NULL, category TEXT NOT NULL, seed INTEGER NOT NULL DEFAULT 0,
 status TEXT NOT NULL DEFAULT 'unknown', confirmed_status TEXT, confirmed_at TEXT,
 last_checked TEXT, price_minor INTEGER, currency TEXT NOT NULL DEFAULT 'INR',
 attributes TEXT NOT NULL DEFAULT '{}', error TEXT, error_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS observations (
 id INTEGER PRIMARY KEY, product_id INTEGER NOT NULL REFERENCES products(id),
 observed_at TEXT NOT NULL, status TEXT NOT NULL, price_minor INTEGER, currency TEXT NOT NULL,
 evidence TEXT NOT NULL, confidence REAL NOT NULL, method TEXT NOT NULL, error TEXT
);
CREATE INDEX IF NOT EXISTS observation_product ON observations(product_id, id DESC);
CREATE TABLE IF NOT EXISTS watches (
 chat_id TEXT NOT NULL, product_id INTEGER NOT NULL REFERENCES products(id),
 PRIMARY KEY(chat_id, product_id)
);
CREATE TABLE IF NOT EXISTS rules (
 id INTEGER PRIMARY KEY, query TEXT NOT NULL UNIQUE, seed INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS rule_subscribers (
 chat_id TEXT NOT NULL, rule_id INTEGER NOT NULL REFERENCES rules(id),
 PRIMARY KEY(chat_id, rule_id)
);
CREATE TABLE IF NOT EXISTS rule_products (
 rule_id INTEGER NOT NULL REFERENCES rules(id), product_id INTEGER NOT NULL REFERENCES products(id),
 notify_new INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(rule_id, product_id)
);
CREATE TABLE IF NOT EXISTS listing_state (
 rule_id INTEGER NOT NULL REFERENCES rules(id), source TEXT NOT NULL,
 baseline_complete INTEGER NOT NULL DEFAULT 0, checked_at TEXT NOT NULL,
 PRIMARY KEY(rule_id, source)
);
CREATE TABLE IF NOT EXISTS exclusions (
 chat_id TEXT NOT NULL, product_id INTEGER NOT NULL REFERENCES products(id),
 PRIMARY KEY(chat_id, product_id)
);
CREATE TABLE IF NOT EXISTS outbox (
 id INTEGER PRIMARY KEY, chat_id TEXT NOT NULL, text TEXT NOT NULL, dedupe_key TEXT UNIQUE,
 state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
 available_at TEXT NOT NULL, lease_until TEXT, error TEXT, created_at TEXT NOT NULL,
 sent_at TEXT
);
CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS llm_cache (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS llm_usage (day TEXT PRIMARY KEY, calls INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS runs (
 id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
 checked INTEGER NOT NULL DEFAULT 0, failed INTEGER NOT NULL DEFAULT 0,
 discovered INTEGER NOT NULL DEFAULT 0, listing_errors INTEGER NOT NULL DEFAULT 0
);
"""


class Store:
    """Single-process SQLite store. State changes and alert creation share one transaction."""

    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        if self.conn.execute("PRAGMA user_version").fetchone()[0] == 0:
            self.conn.execute("PRAGMA user_version=1")

    def close(self):
        self.conn.close()

    def add_product(self, url: str, source: str, title: str, *, seed: bool = False) -> int:
        with self.conn:
            self.conn.execute(
                "INSERT INTO products(url,source,title,category,seed) VALUES (?,?,?,?,?) "
                "ON CONFLICT(url) DO UPDATE SET seed=MAX(seed, excluded.seed)",
                (url, source, title[:300], category_for(title), int(seed)),
            )
        return self.conn.execute("SELECT id FROM products WHERE url=?", (url,)).fetchone()[0]

    def add_rule(self, query: str, *, seed: bool = False) -> int:
        query = " ".join(query.split())[:160]
        if not query:
            raise ValueError("A search query is required.")
        with self.conn:
            self.conn.execute(
                "INSERT INTO rules(query,seed) VALUES (?,?) ON CONFLICT(query) "
                "DO UPDATE SET seed=MAX(seed,excluded.seed)",
                (query, int(seed)),
            )
        return self.conn.execute("SELECT id FROM rules WHERE query=?", (query,)).fetchone()[0]

    def subscribe_defaults(self, chat_id: str):
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO watches SELECT ?,id FROM products WHERE seed=1", (chat_id,)
            )
            self.conn.execute(
                "INSERT OR IGNORE INTO rule_subscribers SELECT ?,id FROM rules WHERE seed=1",
                (chat_id,),
            )

    def watch(self, chat_id: str, product_id: int):
        if not self.product(product_id):
            raise ValueError("Unknown product ID.")
        with self.conn:
            self.conn.execute("INSERT OR IGNORE INTO watches VALUES (?,?)", (chat_id, product_id))
            self.conn.execute(
                "DELETE FROM exclusions WHERE chat_id=? AND product_id=?", (chat_id, product_id)
            )

    def subscribe_rule(self, chat_id: str, rule_id: int):
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO rule_subscribers VALUES (?,?)", (chat_id, rule_id)
            )

    def unwatch(self, chat_id: str, kind: str, item_id: int):
        with self.conn:
            if kind == "R":
                self.conn.execute(
                    "DELETE FROM rule_subscribers WHERE chat_id=? AND rule_id=?", (chat_id, item_id)
                )
            else:
                self.conn.execute(
                    "DELETE FROM watches WHERE chat_id=? AND product_id=?", (chat_id, item_id)
                )
                self.conn.execute(
                    "INSERT OR IGNORE INTO exclusions VALUES (?,?)", (chat_id, item_id)
                )

    def product(self, product_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
        return dict(row) if row else None

    def active_products(self) -> list[dict]:
        return [
            dict(r)
            for r in self.conn.execute("""
          SELECT p.* FROM products p WHERE p.seed=1
          OR EXISTS (SELECT 1 FROM watches w WHERE w.product_id=p.id)
          OR EXISTS (SELECT 1 FROM rule_products rp JOIN rules r ON r.id=rp.rule_id
            WHERE rp.product_id=p.id AND (r.seed=1 OR EXISTS
              (SELECT 1 FROM rule_subscribers rs WHERE rs.rule_id=r.id))) ORDER BY p.id
        """)
        ]

    def rules(self) -> list[dict]:
        return [
            dict(r)
            for r in self.conn.execute("""
          SELECT r.* FROM rules r WHERE r.seed=1 OR EXISTS
          (SELECT 1 FROM rule_subscribers s WHERE s.rule_id=r.id) ORDER BY r.id
        """)
        ]

    def rule(self, rule_id: int) -> dict:
        return dict(self.conn.execute("SELECT * FROM rules WHERE id=?", (rule_id,)).fetchone())

    def product_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]

    def link_discoveries(
        self, rule_id: int, source: str, product_ids: list[int], *, complete: bool
    ):
        prior = self.conn.execute(
            "SELECT baseline_complete FROM listing_state WHERE rule_id=? AND source=?",
            (rule_id, source),
        ).fetchone()
        alert_new = bool(prior and prior[0])
        with self.conn:
            for product_id in product_ids:
                self.conn.execute(
                    "INSERT OR IGNORE INTO rule_products VALUES (?,?,?)",
                    (rule_id, product_id, int(alert_new)),
                )
            # An incomplete/blocked listing must never establish a baseline.
            if complete:
                self.conn.execute(
                    "INSERT INTO listing_state VALUES (?,?,1,?) ON CONFLICT(rule_id,source) "
                    "DO UPDATE SET baseline_complete=1,checked_at=excluded.checked_at",
                    (rule_id, source, utcnow()),
                )

    def subscribers(self, product_id: int) -> list[str]:
        return [
            r[0]
            for r in self.conn.execute(
                """
          SELECT chat_id FROM (
            SELECT chat_id FROM watches WHERE product_id=? UNION
            SELECT rs.chat_id FROM rule_subscribers rs JOIN rule_products rp
              ON rp.rule_id=rs.rule_id WHERE rp.product_id=?
          ) WHERE chat_id NOT IN (SELECT chat_id FROM exclusions WHERE product_id=?)
        """,
                (product_id, product_id, product_id),
            )
        ]

    def observe(self, product_id: int, snapshot: Snapshot) -> int:
        with self.conn:
            product = self.product(product_id)
            if product is None:
                raise ValueError("Unknown product.")
            cursor = self.conn.execute(
                "INSERT INTO observations(product_id,observed_at,status,price_minor,currency,evidence,confidence,method,error) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    product_id,
                    snapshot.observed_at,
                    snapshot.status.value,
                    snapshot.price_minor,
                    snapshot.currency,
                    snapshot.evidence[:700],
                    snapshot.confidence,
                    snapshot.method,
                    snapshot.error,
                ),
            )
            observation_id = cursor.lastrowid
            known = snapshot.status != Status.UNKNOWN
            self.conn.execute(
                """
              UPDATE products SET status=?,last_checked=?,error=?,error_count=?,
              title=?,category=?,price_minor=COALESCE(?,price_minor),currency=?,attributes=?,
              confirmed_status=?,confirmed_at=? WHERE id=?
            """,
                (
                    snapshot.status.value,
                    snapshot.observed_at,
                    snapshot.error,
                    product["error_count"] + 1 if snapshot.error else 0,
                    snapshot.title[:300] if not snapshot.error else product["title"],
                    category_for(snapshot.title) if not snapshot.error else product["category"],
                    snapshot.price_minor,
                    snapshot.currency if snapshot.price_minor is not None else product["currency"],
                    json.dumps(snapshot.attributes)
                    if snapshot.attributes
                    else product["attributes"],
                    snapshot.status.value if known else product["confirmed_status"],
                    snapshot.observed_at if known else product["confirmed_at"],
                    product_id,
                ),
            )
            previous = product["confirmed_status"]
            transition = (
                snapshot.status in FAVORABLE
                and previous is not None
                and (
                    previous == Status.OUT_OF_STOCK
                    or (previous == Status.PRE_ORDER and snapshot.status == Status.IN_STOCK)
                )
            )
            new_rules = [
                r[0]
                for r in self.conn.execute(
                    "SELECT rule_id FROM rule_products WHERE product_id=? AND notify_new=1",
                    (product_id,),
                )
            ]
            discovery_chats = set()
            if snapshot.status in FAVORABLE and new_rules:
                for rid in new_rules:
                    discovery_chats.update(
                        r[0]
                        for r in self.conn.execute(
                            "SELECT chat_id FROM rule_subscribers WHERE rule_id=?", (rid,)
                        )
                    )
            from .messages import alert_text

            for chat_id in self.subscribers(product_id):
                if transition or chat_id in discovery_chats:
                    text = alert_text(self.product(product_id), previous, new=not transition)
                    self._enqueue(
                        chat_id, text, f"availability:{product_id}:{observation_id}:{chat_id}"
                    )
            if known:
                self.conn.execute(
                    "UPDATE rule_products SET notify_new=0 WHERE product_id=?", (product_id,)
                )
            hook = getattr(self, "_after_observation", None)
            if hook:
                hook(
                    product,
                    self.product(product_id),
                    observation_id,
                    bool(new_rules and snapshot.status in FAVORABLE),
                )
        return observation_id

    def history(self, product_id: int, limit: int = 8) -> list[dict]:
        return [
            dict(r)
            for r in self.conn.execute(
                "SELECT * FROM observations WHERE product_id=? ORDER BY id DESC LIMIT ?",
                (product_id, limit),
            )
        ]

    def search(self, intent: Intent) -> list[dict]:
        matches = []
        for row in self.conn.execute("SELECT * FROM products ORDER BY id"):
            product = dict(row)
            attrs = json.loads(product["attributes"])
            searchable = (
                product["title"]
                + " "
                + product["category"].replace("_", " ")
                + " "
                + " ".join(attrs.values())
            )
            if intent.query and not query_matches(intent.query, searchable):
                continue
            if intent.status and product["status"] != intent.status:
                continue
            if intent.source and product["source"] != intent.source:
                continue
            if intent.max_price_minor is not None and (
                product["price_minor"] is None
                or product["currency"] != "INR"
                or product["price_minor"] > intent.max_price_minor
            ):
                continue
            if intent.min_ram_gb and int(attrs.get("ram_gb", "0")) < intent.min_ram_gb:
                continue
            matches.append(product)
        return sorted(
            matches, key=lambda p: (p["price_minor"] is None, p["price_minor"] or 0, p["id"])
        )[:10]

    def watchlist(self, chat_id: str) -> tuple[list[dict], list[dict]]:
        products = [
            dict(r)
            for r in self.conn.execute(
                """
          SELECT p.* FROM products p WHERE p.id IN (
            SELECT product_id FROM watches WHERE chat_id=? UNION
            SELECT rp.product_id FROM rule_products rp JOIN rule_subscribers rs
              ON rp.rule_id=rs.rule_id WHERE rs.chat_id=?
          ) AND p.id NOT IN (SELECT product_id FROM exclusions WHERE chat_id=?) ORDER BY p.id
        """,
                (chat_id, chat_id, chat_id),
            )
        ]
        rules = [
            dict(r)
            for r in self.conn.execute(
                "SELECT r.* FROM rules r JOIN rule_subscribers rs ON r.id=rs.rule_id WHERE rs.chat_id=?",
                (chat_id,),
            )
        ]
        return products, rules

    def _enqueue(self, chat_id: str, text: str, key: str | None = None):
        part = 0
        while text:
            cut = min(3800, len(text))
            if len(text) > 3800:
                newline = text.rfind("\n", 0, cut)
                if newline > 100:
                    cut = newline + 1
            chunk, text = text[:cut], text[cut:]
            chunk_key = key if part == 0 or key is None else f"{key}:part:{part}"
            self.conn.execute(
                "INSERT OR IGNORE INTO outbox(chat_id,text,dedupe_key,available_at,created_at) VALUES (?,?,?,?,?)",
                (chat_id, chunk, chunk_key, utcnow(), utcnow()),
            )
            part += 1

    def enqueue(self, chat_id: str, text: str, key: str | None = None):
        with self.conn:
            self._enqueue(chat_id, text, key)

    def claim_message(self) -> dict | None:
        now = utcnow()
        lease = (datetime.now(UTC) + timedelta(minutes=2)).isoformat(timespec="seconds")
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            row = self.conn.execute(
                """
              SELECT * FROM outbox WHERE (state='pending' AND available_at<=?)
              OR (state='sending' AND lease_until<=?) ORDER BY id LIMIT 1
            """,
                (now, now),
            ).fetchone()
            if row is None:
                return None
            self.conn.execute(
                "UPDATE outbox SET state='sending',lease_until=?,attempts=attempts+1 WHERE id=?",
                (lease, row["id"]),
            )
            return dict(row)

    def message_sent(self, message_id: int):
        with self.conn:
            self.conn.execute(
                "UPDATE outbox SET state='sent',sent_at=?,lease_until=NULL WHERE id=?",
                (utcnow(), message_id),
            )

    def message_failed(self, message_id: int, *, retry_after: int = 30, permanent: bool = False):
        next_at = (datetime.now(UTC) + timedelta(seconds=retry_after)).isoformat(timespec="seconds")
        with self.conn:
            self.conn.execute(
                "UPDATE outbox SET state=?,available_at=?,lease_until=NULL,error=? WHERE id=?",
                ("failed" if permanent else "pending", next_at, "delivery_failed", message_id),
            )

    def get_meta(self, key: str, default: str = "") -> str:
        row = self.conn.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_meta(self, key: str, value: str):
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO metadata VALUES (?,?)", (key, value))

    def cache_get(self, key: str) -> dict | None:
        row = self.conn.execute("SELECT value FROM llm_cache WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def cache_put(self, key: str, value: dict):
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO llm_cache VALUES (?,?)", (key, json.dumps(value))
            )

    def reserve_llm_call(self, limit: int) -> bool:
        day = datetime.now(UTC).date().isoformat()
        with self.conn:
            self.conn.execute("INSERT OR IGNORE INTO llm_usage VALUES (?,0)", (day,))
            cursor = self.conn.execute(
                "UPDATE llm_usage SET calls=calls+1 WHERE day=? AND calls<?", (day, limit)
            )
            return cursor.rowcount == 1

    def start_run(self) -> int:
        with self.conn:
            return self.conn.execute(
                "INSERT INTO runs(started_at) VALUES (?)", (utcnow(),)
            ).lastrowid

    def finish_run(self, run_id: int, stats: dict):
        with self.conn:
            self.conn.execute(
                "UPDATE runs SET finished_at=?,checked=?,failed=?,discovered=?,listing_errors=? WHERE id=?",
                (
                    utcnow(),
                    stats["checked"],
                    stats["failed"],
                    stats["discovered"],
                    stats["listing_errors"],
                    run_id,
                ),
            )
            self.conn.execute(
                "INSERT OR REPLACE INTO metadata VALUES ('last_completed_scan',?)", (utcnow(),)
            )

    def backup(self, destination: str):
        """SQLite's online backup API also captures committed WAL data."""
        with sqlite3.connect(destination) as target:
            self.conn.backup(target)
