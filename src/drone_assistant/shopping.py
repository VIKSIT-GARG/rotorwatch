"""Grounded price and cart commands shared by the dashboard and channel tools."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass

from .display import ist_time
from .domain import Intent, format_money
from .openclaw import OpenClawError


@dataclass
class ShoppingIntent:
    action: str
    query: str
    ids: list[int]
    source: str | None = None
    quantity: int = 1


def shopping_intent(message: str) -> ShoppingIntent | None:
    cart = re.search(r"\badd\b.*\bto\s+(?:(?:my|the|a)\s+)?cart\b", message, re.I)
    price = re.search(r"\b(?:price|prices|cost|pricing)\b|^/price\b", message, re.I)
    stock = re.search(
        r"\b(?:in[ -]stock|out[ -]of[ -]stock|availability|available)\b", message, re.I
    ) and re.match(
        r"^\s*(?:is\b|are\b|do\b|does\b|can\b|could\b|check\b|/?stock\b|/?availability\b)",
        message,
        re.I,
    )
    if not cart and not price and not stock:
        return None
    source = re.search(
        r"\b(robu(?:\.in)?|thinkrobotics(?:\.com)?|zbotic(?:\.in)?|evelta(?:\.com)?)\b",
        message,
        re.I,
    )
    query = message
    if cart:
        query = re.sub(r"\badd\b|\bto\s+(?:(?:my|the|a)\s+)?cart\b", " ", query, flags=re.I)
    query = re.sub(r"\bwhat['’]s\b", "what is", query, flags=re.I)
    if stock:
        query = re.sub(
            r"\b(?:out[ -]of[ -]stock|in[ -]stock|availability|available)\b", " ", query, flags=re.I
        )
        query = re.sub(
            r"\b(?:any|websites?|sites?|stores?|retailers?|whether|if|there|currently|do|does|have|stock)\b",
            " ",
            query,
            flags=re.I,
        )
    if source:
        query = re.sub(re.escape(source.group()), " ", query, flags=re.I)
    quantity = re.search(r"\b(?:qty|quantity)\s*[:=]?\s*(\d+)\b", query, re.I)
    query = re.sub(r"\b(?:qty|quantity)\s*[:=]?\s*\d+\b", " ", query, flags=re.I)
    ids = [int(i) for i in re.findall(r"\bP(\d+)\b", query, re.I)]
    query = re.sub(r"\bP\d+\b", " ", query, flags=re.I)
    query = re.sub(
        r"\b(?:what|whats|is|are|the|a|an|latest|current|live|price|prices|pricing|cost|of|for|on|from|at|in|please|can|could|would|you|me|tell|give|show|check|fetch|and|also|use|specific|it|this|that|now|to|my)\b",
        " ",
        query,
        flags=re.I,
    )
    query = re.sub(r"\b(raspberry|raspbery|rabperry|rabperr)\b", "Raspberry", query, flags=re.I)
    query = re.sub(r"\bpi\s*5\b", "Pi 5", query, flags=re.I)
    query = " ".join(re.findall(r"[\w.-]+", query)).strip(".- /")
    return ShoppingIntent(
        "cart" if cart else "price" if price else "stock",
        query,
        ids,
        source.group(1).lower().split(".")[0] if source else None,
        int(quantity.group(1)) if quantity else 1,
    )


class Shopping:
    def __init__(self, store, monitor, cart, *, demo=False, queue_search=None):
        self.store, self.monitor, self.cart, self.demo = store, monitor, cart, demo
        self.queue_search = queue_search

    async def ask(self, message: str, session: str) -> dict | None:
        confirmation = re.fullmatch(r"\s*/?confirm\s+cart\s+C(\d+)\s*", message, re.I)
        intent = shopping_intent(message)
        if not confirmation and intent is None:
            return None

        def reply(answer, rows=(), action="price"):
            return {
                "answer": answer,
                "products": [self.store.public_product(p) for p in rows],
                "model_used": False,
                "action": action,
            }

        if confirmation:
            identifier = int(confirmation.group(1))
            permitted = json.loads(self.store.get_meta("cart_reviews:" + session, "[]"))
            if identifier not in permitted:
                return reply(
                    "That cart review was not prepared in this chat. Ask to add the exact product again.",
                    action="cart",
                )
            if self.demo:
                return reply("Demo mode cannot change a retailer cart.", action="cart")
            try:
                result = await self.cart.confirm(identifier)
                return reply(result["message"], action="cart")
            except OpenClawError as exc:
                return reply(exc.message, action="cart")
        if not 1 <= intent.quantity <= 20:
            return reply("Choose a quantity between 1 and 20.", action="cart")
        if not intent.query and not intent.ids:
            prior = json.loads(self.store.get_meta("shopping_query:" + session, "{}"))
            intent.query = prior.get("query", "")
            intent.ids = prior.get("ids", [])
            intent.source = intent.source or prior.get("source")
        if not intent.query and not intent.ids:
            return reply(
                "Which product? Send its product ID, for example 'price P1' or 'add to cart P1'."
            )
        self.store.set_meta(
            "shopping_query:" + session,
            json.dumps(
                {
                    "query": intent.query,
                    "ids": intent.ids,
                    "source": intent.source,
                }
            ),
        )
        search = Intent(query=intent.query, source=intent.source)

        def matches():
            rows = (
                [self.store.product(i) for i in intent.ids]
                if intent.ids
                else self.store.search(search)
            )
            return [p for p in rows if p and (not intent.source or p["source"] == intent.source)]

        rows, freshness = matches(), ""
        incomplete = False
        listing_errors = 0
        if self.demo:
            freshness = "Demo records only; no retailer was contacted.\n\n"
        else:
            # Bound synchronous work. Existing background scans retain their lock.
            try:
                async with asyncio.timeout(65):
                    await asyncio.wait_for(self.monitor.lock.acquire(), timeout=2)
                    try:
                        if intent.query and (not rows or intent.action == "stock"):
                            rid = self.store.add_rule(intent.query)
                            discovery_result = await self.monitor.discover_rule(
                                self.store.rule(rid), source_key=intent.source, max_pages=1
                            )
                            if discovery_result:
                                _, listing_errors = discovery_result
                            rows = matches()
                        for product in rows[:3]:
                            await self.monitor.check_product(product)
                    finally:
                        self.monitor.lock.release()
                rows = matches()
                freshness = "Retailer check attempted for up to three matches; observations and listing coverage may be incomplete.\n\n"
            except TimeoutError:
                incomplete = True
                rows = matches()
                freshness = "The live check is busy or timed out. These are saved observations; current stock and price are unverified. Retry shortly.\n\n"
        queued = ""
        if not self.demo and self.queue_search and intent.query and (incomplete or not rows):
            try:
                self.queue_search("web", search)
                queued = " A background retailer search is queued. Results will appear in Explore when the scan finishes; then ask this question again."
            except (ValueError, asyncio.QueueFull):
                queued = " The background search queue is busy; retry shortly."
        if not rows:
            if intent.action == "stock":
                return reply(
                    "I couldn't verify a matching retailer listing for '"
                    + intent.query
                    + "'. This does not mean it is out of stock."
                    + (
                        " Some retailer listing pages were blocked or unavailable."
                        if listing_errors
                        else ""
                    )
                    + (
                        " The live check was busy or timed out before all sites could be checked."
                        if incomplete
                        else ""
                    )
                    + queued
                    + " An exact product URL or model name will make the search more precise.",
                    action="stock",
                )
            return reply(
                "No verified match for that exact product/store. Send the retailer product URL with /watch, then ask for its price or use its P-number. I won't substitute another product."
                + queued
            )
        lines = []
        for p in rows[:6]:
            public = self.store.public_product(p)
            lines.append(
                f"P{p['id']} · {p['title']}\n{p['source']} · {format_money(p['price_minor'], p['currency'])}"
                f"\nPrice observed: {ist_time(public['price_checked_at'])}"
                + (
                    " · saved price; latest check did not verify it"
                    if public["price_stale"]
                    else ""
                )
                + f"\nStock: {p['status'].replace('_', ' ')} · checked {ist_time(p['last_checked'])}\n{p['url']}"
                + (f"\nCheck issue: {p['error']}" if p.get("error") else "")
            )
        facts = freshness + "\n\n".join(lines)
        if intent.action != "cart":
            if intent.action == "stock":
                if listing_errors:
                    facts += "\n\nSome retailer listing pages could not be checked; this is not a complete stock survey."
                facts += queued
            return reply(facts, rows[:6], intent.action)
        if len(rows) != 1:
            return reply(
                facts
                + "\n\nChoose the exact product and retailer: 'add to cart P123'. Nothing was added.",
                rows[:6],
                "cart",
            )
        if self.demo:
            return reply(facts + "\n\nDemo mode cannot change a retailer cart.", rows[:1], "cart")
        identifier = self.store.purchase_request(rows[0]["id"], intent.quantity)
        try:
            result = await self.cart.prepare(identifier)
        except OpenClawError as exc:
            return reply(
                facts
                + "\n\nCart browser: "
                + exc.message
                + f"\nReview C{identifier} was saved; no item was added.",
                rows[:1],
                "cart",
            )
        if result["state"] != "cart_ready":
            return reply(
                facts + "\n\n" + result["message"] + " Nothing was added.", rows[:1], "cart"
            )
        permitted = json.loads(self.store.get_meta("cart_reviews:" + session, "[]"))
        self.store.set_meta("cart_reviews:" + session, json.dumps((permitted + [identifier])[-20:]))
        preview = result["preview"]
        return reply(
            f"Cart review C{identifier}\n{preview.get('title', rows[0]['title'])}"
            f"\nRetailer: {rows[0]['source']}\nPrice on page: {preview.get('price') or 'unverified'}"
            f"\nVariant: {preview.get('variant') or 'standard product'}\nQuantity: {intent.quantity}"
            f"\n{rows[0]['url']}\n\nReply exactly 'confirm cart C{identifier}' within 5 minutes to add this item. Nothing has been added yet. Checkout remains manual.",
            rows[:1],
            "cart",
        )
