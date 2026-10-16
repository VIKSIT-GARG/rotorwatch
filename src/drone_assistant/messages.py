from __future__ import annotations

import json

from .display import ist_time
from .domain import format_money


def product_text(p: dict) -> str:
    attributes = json.loads(p.get("attributes", "{}"))
    details = " | ".join(f"{k}: {v}" for k, v in attributes.items())
    stock = p["status"].replace("_", " ")
    if p["status"] == "unknown" and p.get("confirmed_status"):
        stock += f" (last confirmed: {p['confirmed_status'].replace('_', ' ')} at {ist_time(p['confirmed_at'])})"
    return (
        f"P{p['id']} · {p['title']}\n"
        f"{p['source']} · {stock} · {format_money(p['price_minor'], p['currency'])}\n"
        f"{details + chr(10) if details else ''}"
        f"Checked: {ist_time(p.get('last_checked'))}\n{p['url']}"
        + (f"\nCheck issue: {p['error']}" if p.get("error") else "")
    )


def alert_text(p: dict, previous: str | None, *, new: bool) -> str:
    heading = (
        "NEW MATCH AVAILABLE"
        if new
        else ("PRE-ORDER OPENED" if p["status"] == "pre_order" else "BACK IN STOCK")
    )
    return f"{heading}\n{product_text(p)}\nConfirm the exact variant and availability on the retailer page before purchasing."


HELP = """Drone Parts Assistant
Search, compare and monitor drone components across Robu, ThinkRobotics, Zbotic and Evelta.

/start — subscribe to the seed watchlist
/search Raspberry Pi 5 in stock under 10000
/search flight controller from zbotic
/watch https://robu.in/product/raspberry-pi-5-model-4gb/
/watch Pi 5 HAT — discover and track matching products
/watch P1 — track an existing product
/watchlist — your products and discovery rules
/unwatch P1 — mute this product, including rule matches
/unwatch R1 — stop a discovery rule
/status P1
/history P1
/compare P1 P2

Natural language also works: “find Pixhawk 6X”, “track Raspberry Pi 5”, “compare P1 and P2”. Add “at least 8GB RAM” to a search to filter specifications. Complex phrasing uses the configured small LLM.

Searches use the local catalog; a query with no catalog matches queues a bounded retailer search. Prices and stock always show their check timestamp. Unknown means unverified, not out of stock. Displayed times are IST.
"""
