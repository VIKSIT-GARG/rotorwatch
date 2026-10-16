from __future__ import annotations

import asyncio
import re
from collections.abc import Callable

from .display import ist_time
from .domain import Intent, money_minor
from .llm import SmallLLM
from .messages import HELP, product_text
from .sources import Registry
from .storage import Store


def parse_intent(text: str) -> Intent | None:
    text = text.strip()
    text = re.sub(r"^(/\w+)@\w+", r"\1", text)
    command = re.match(
        r"^/?(start|help|search|find|show|watch|track|monitor|notify|unwatch|stop|remove|status|history|compare|watchlist)\b\s*(.*)",
        text,
        re.I,
    )
    if not command:
        return None
    action, query = command.group(1).lower(), command.group(2).strip()
    action = {
        "find": "search",
        "show": "search",
        "track": "watch",
        "monitor": "watch",
        "notify": "watch",
        "stop": "unwatch",
        "remove": "unwatch",
        "start": "start",
    }.get(action, action)
    if action in {"start", "help", "watchlist"}:
        return Intent(action)
    if "https://" in query:
        # Preserve variant parameters and periods in URLs.
        match = re.search(r"https://\S+", query)
        return Intent(action, match.group(0))
    ids = [int(i) for i in re.findall(r"\b[Pp](\d+)\b", query)]
    if action in {"watch", "status", "history", "compare"} and ids:
        return Intent(action, query, ids)
    if action == "unwatch":
        return Intent(action, query)
    intent = Intent(action=action)
    price = re.search(
        r"(?:under|below|less than|max(?:imum)?|budget(?: of)?)\s*(?:₹|rs\.?|inr)?\s*([\d,]+(?:\.\d{1,2})?)\s*(k)?\b",
        query,
        re.I,
    )
    if price:
        intent.max_price_minor = money_minor(price.group(1))
        if intent.max_price_minor is not None and price.group(2):
            intent.max_price_minor *= 1000
        query = query[: price.start()] + " " + query[price.end() :]
    for pattern, status in [
        (r"\bout[ -]of[ -]stock\b", "out_of_stock"),
        (r"\bin[ -]stock\b|\bavailable now\b", "in_stock"),
        (r"\bpre[ -]?order\b", "pre_order"),
    ]:
        if re.search(pattern, query, re.I):
            intent.status = status
            query = re.sub(pattern, " ", query, flags=re.I)
            break
    source = re.search(
        r"\b(?:from|on|at)\s+(robu(?:\.in)?|thinkrobotics(?:\.com)?|zbotic(?:\.in)?|evelta(?:\.com)?)\b",
        query,
        re.I,
    )
    if source:
        intent.source = source.group(1).lower().split(".")[0]
        query = query[: source.start()] + " " + query[source.end() :]
    ram = re.search(r"(?:at least|min(?:imum)?)\s*(\d+)\s*gb(?:\s*ram)?", query, re.I)
    if ram:
        intent.min_ram_gb = int(ram.group(1))
        query = query[: ram.start()] + " " + query[ram.end() :]
    query = re.sub(r"^(?:me\s+)?(?:all\s+|some\s+|the\s+|an?\s+)?", "", query, flags=re.I)
    query = re.sub(r"\bpi5\b", "Pi 5", query, flags=re.I)
    intent.query = " ".join(query.split()).strip(" ?.,")
    return intent


class Chat:
    def __init__(
        self,
        store: Store,
        registry: Registry,
        llm: SmallLLM,
        queue_search: Callable | None = None,
        max_products: int = 500,
    ):
        self.store, self.registry, self.llm = store, registry, llm
        self.queue_search, self.max_products = queue_search, max_products

    def queue_note(self, chat_id: str, intent: Intent) -> str:
        if self.queue_search is None:
            return "Run the monitoring job or start the bot to check the retailer."
        try:
            self.queue_search(chat_id, intent)
            return "A retailer check is queued; I’ll reply here when it finishes."
        except (ValueError, asyncio.QueueFull):
            return "The live-check queue is busy or rate-limited. Your watch remains saved; retry after 10 seconds or wait for the next scheduled scan."

    def results(self, intent: Intent) -> str:
        products = self.store.search(intent)
        if not products:
            return "No matching catalog products yet. Use /watch <product name> to keep discovering matches, or /watch <exact product URL>."
        return "CATALOG RESULTS (up to 10; lowest known price first)\n\n" + "\n\n".join(
            product_text(p) for p in products
        )

    async def handle(self, chat_id: str, text: str) -> str:
        if len(text) > 1200:
            return "Please keep requests under 1,200 characters."
        intent = parse_intent(text)
        if intent is None:
            intent = await self.llm.interpret(text)
        if intent is None:
            # A plain product name is still searchable without an LLM key.
            intent = Intent(query=text.strip())
        action = intent.action
        if action == "start":
            self.store.subscribe_defaults(chat_id)
            return "Subscribed to the seed products and discovery rules.\n\n" + HELP
        if action == "help":
            return HELP
        if action == "watchlist":
            products, rules = self.store.watchlist(chat_id)
            return (
                "YOUR WATCHLIST\n"
                + "\n".join(f"P{p['id']} · {p['title']} · {p['status']}" for p in products)
                + "\n\nDISCOVERY RULES\n"
                + "\n".join(f"R{r['id']} · {r['query']}" for r in rules)
                + "\n\n/unwatch P1 mutes that product, including matching rules. /unwatch R1 stops the rule. Direct product watches remain separate."
            )
        if action == "unwatch":
            match = re.fullmatch(r"([PR])(\d+)", intent.query.strip(), re.I)
            if not match:
                return "Use /unwatch P1 for a product or /unwatch R1 for a discovery rule."
            self.store.unwatch(chat_id, match.group(1).upper(), int(match.group(2)))
            return f"Stopped alerts for {match.group(0).upper()}."
        if action == "watch":
            if intent.ids:
                try:
                    self.store.watch(chat_id, intent.ids[0])
                    return f"Watching P{intent.ids[0]}.\n" + product_text(
                        self.store.product(intent.ids[0])
                    )
                except ValueError as exc:
                    return str(exc)
            if intent.query.startswith("https://"):
                try:
                    url = self.registry.canonical(intent.query, product=True)
                    source = self.registry.resolve(url)
                    existing = self.store.conn.execute(
                        "SELECT id FROM products WHERE url=?", (url,)
                    ).fetchone()
                    if not existing and self.store.product_count() >= self.max_products:
                        return "Catalog capacity reached. Increase MAX_PRODUCTS before adding more."
                    product_id = self.store.add_product(url, source.key, urlsplit_title(url))
                    self.store.watch(chat_id, product_id)
                    return (
                        f"Watching P{product_id}. Alerts follow confirmed favorable changes. "
                        + self.queue_note(chat_id, Intent("status", ids=[product_id]))
                    )
                except ValueError as exc:
                    return str(exc)
            if not intent.query:
                return "Use /watch <product name>, /watch P1 or /watch <HTTPS product URL>."
            if any(
                x is not None
                for x in (intent.max_price_minor, intent.status, intent.source, intent.min_ram_gb)
            ):
                return "Use price/spec/store filters with /search, then /watch the exact product ID. Name-based watch rules match product names across all four retailers."
            rule_id = self.store.add_rule(intent.query)
            self.store.subscribe_rule(chat_id, rule_id)
            # Immediately link existing catalog matches, without generating new-listing alerts.
            for product in self.store.search(Intent(query=intent.query)):
                self.store.link_discoveries(
                    rule_id, product["source"], [product["id"]], complete=False
                )
            return (
                f"Watching rule R{rule_id}: {intent.query}. The first listing pass establishes a baseline; later matching additions and restocks can trigger alerts. "
                + self.queue_note(chat_id, Intent(query=intent.query))
            )
        if action in {"status", "history"}:
            candidates = (
                [self.store.product(i) for i in intent.ids[:1]]
                if intent.ids
                else self.store.search(Intent(query=intent.query))
            )
            candidates = [p for p in candidates if p]
            if len(candidates) != 1:
                return (
                    "Choose a single product ID, for example /status P1 or /history P1.\n\n"
                    + self.results(Intent(query=intent.query))
                )
            product = candidates[0]
            if action == "status":
                return product_text(product)
            history = self.store.history(product["id"])
            return (
                f"P{product['id']} · {product['title']}\nLATEST OBSERVATIONS\n"
                + "\n".join(
                    f"{ist_time(h['observed_at'])} · {h['status']} · {h['method']}\n  {h['error'] or h['evidence']}"
                    for h in history
                )
                or "No checks yet."
            )
        if action == "compare":
            if not 2 <= len(intent.ids) <= 4:
                return "Choose 2–4 product IDs from /search results, for example /compare P1 P2."
            products = [self.store.product(i) for i in intent.ids]
            if any(p is None for p in products):
                return "At least one product ID is unknown. Use /search to find valid IDs."
            return (
                "COMPARISON — recorded retailer facts; compatibility is not inferred\n\n"
                + "\n\n".join(product_text(p) for p in products)
            )
        if action == "search":
            products = self.store.search(intent)
            if not products and self.queue_search and intent.query:
                return "No catalog match yet. " + self.queue_note(chat_id, intent)
            return self.results(intent)
        return HELP


def urlsplit_title(url: str) -> str:
    from urllib.parse import urlsplit

    return urlsplit(url).path.strip("/").split("/")[-1].replace("-", " ")[:300]
