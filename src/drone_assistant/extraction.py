from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup

from .domain import Snapshot, Status, money_minor
from .sources import Source

SCHEMA_STATUS = {
    "instock": Status.IN_STOCK,
    "limitedavailability": Status.IN_STOCK,
    "outofstock": Status.OUT_OF_STOCK,
    "soldout": Status.OUT_OF_STOCK,
    "discontinued": Status.OUT_OF_STOCK,
    "preorder": Status.PRE_ORDER,
    "presale": Status.PRE_ORDER,
    "backorder": Status.PRE_ORDER,
}


def text_status(text: str) -> Status:
    text = " ".join(text.casefold().split())
    text = re.sub(r"\bnot (?:currently )?in[ -]stock\b", "out of stock", text)
    if re.search(r"\bpre[ -]?orders? (?:are )?(?:closed|unavailable|not open)\b", text):
        return Status.OUT_OF_STOCK
    negative = bool(
        re.search(
            r"\bout[ -]of[ -]stock\b|\bsold out\b|\bunavailable\b|\bnot (?:currently )?available\b",
            text,
        )
    )
    positive = bool(
        re.search(r"\bin[ -]stock\b|\bavailable now\b|\bready (?:to|for) (?:ship|dispatch)\b", text)
    )
    preorder = bool(re.search(r"\bpre[ -]?order\b|\bback[ -]?order\b", text))
    if negative and (positive or preorder):
        return Status.UNKNOWN
    if negative:
        return Status.OUT_OF_STOCK
    if preorder:
        return Status.PRE_ORDER
    if positive:
        return Status.IN_STOCK
    return Status.UNKNOWN


def attributes_from(title: str, text: str) -> dict[str, str]:
    attrs = {}
    # Only a single unambiguous RAM capacity in the title is considered selected.
    ram_values = set(re.findall(r"\b(\d+)\s*GB\b", title, re.I))
    if len(ram_values) == 1 and not re.search(r"\d\s*[/|]\s*\d", title):
        attrs["ram_gb"] = next(iter(ram_values))
    for key, pattern in {
        "processor": r"\b(STM32[A-Z0-9]+|BCM\d+|Cortex[- ]?[AM]\d+)\b",
        "interface": r"\b(PCIe|I2C|SPI|UART|CAN|Ethernet)\b",
    }.items():
        match = re.search(pattern, text, re.I)
        if match:
            attrs[key] = match.group(0)
    return attrs


def _products(node):
    if isinstance(node, list):
        for child in node:
            yield from _products(child)
    elif isinstance(node, dict):
        types = node.get("@type", [])
        if types == "Product" or (isinstance(types, list) and "Product" in types):
            yield node
        if "@graph" in node:
            yield from _products(node["@graph"])


def _offer_list(value) -> list[dict]:
    if isinstance(value, list):
        return [x for x in value if isinstance(x, dict)]
    if isinstance(value, dict):
        if isinstance(value.get("offers"), list):
            return _offer_list(value["offers"])
        return [value]
    return []


def page_scope(html: str, source: Source) -> tuple[BeautifulSoup, str, str]:
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.find("h1")
    title = h1.get_text(" ", strip=True)[:300] if h1 else "Unknown product"
    scopes = [soup.select_one(selector) for selector in source.main_selectors]
    scope = next((s for s in scopes if s is not None), None)
    text = (
        scope.get_text(" ", strip=True)
        if scope
        else (h1.parent.get_text(" ", strip=True) if h1 else "")
    )
    return soup, title, text[:10000]


def displayed_price(soup: BeautifulSoup, source: Source) -> int | None:
    """Read only a main-product price, excluding crossed-out and related prices."""
    selectors = {
        "woocommerce": [
            ".summary .woocommerce-variation-price .price",
            ".summary > .price",
            ".entry-summary .price",
            ".summary .price",
        ],
        "shopify": [".product__info-container .price", ".product-single__meta .price"],
        "bigcommerce": [
            ".productView [data-product-price-with-tax]",
            ".productView [data-product-price-without-tax]",
        ],
    }
    for selector in selectors.get(source.platform, []):
        for node in soup.select(selector):
            if node.find_parent(["aside", "del"]) or node.find_parent(class_="related"):
                continue
            # A sale price takes precedence over the old regular price.
            selected = node.select_one("ins, .price__sale .price-item--sale") or node
            copy = BeautifulSoup(str(selected), "html.parser")
            for old in copy.select("del, .price__regular, .screen-reader-text, .visually-hidden"):
                old.decompose()
            amounts = copy.select(".woocommerce-Price-amount, .price-item--sale")
            texts = [n.get_text(" ", strip=True) for n in amounts] or [
                copy.get_text(" ", strip=True)
            ]
            prices = set()
            for text in texts:
                # A price range must remain unknown, never silently choose one variant.
                for number in re.findall(r"(?<![\d.])\d[\d,]*(?:\.\d{1,2})?(?![\d.])", text):
                    value = money_minor(number)
                    if value is not None:
                        prices.add(value)
            if len(prices) == 1:
                return prices.pop()
    return None


def extract_html(html: str, source: Source, url: str) -> Snapshot:
    soup, title, scope_text = page_scope(html, source)
    page_title = soup.title.get_text().casefold() if soup.title else ""
    if any(
        marker in page_title
        for marker in (
            "just a moment",
            "access denied",
            "attention required",
            "verify you are human",
        )
    ):
        return Snapshot(
            title=title,
            error="bot_challenge",
            evidence="A challenge page is not a stock observation.",
        )
    snapshot = Snapshot(title=title, attributes=attributes_from(title, scope_text))
    candidates = []
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            candidates.extend(_products(json.loads(script.string or script.get_text())))
        except (ValueError, TypeError):
            continue
    # Do not read a related/recommended product's availability as the main product.
    matching = [
        p for p in candidates if title.casefold() == str(p.get("name", "")).strip().casefold()
    ]
    product = (
        matching[0]
        if matching
        else (candidates[0] if len(candidates) == 1 and title == "Unknown product" else None)
    )
    if product:
        snapshot.title = str(product.get("name") or title)[:300]
        offers = _offer_list(product.get("offers"))
        variant = parse_qs(urlsplit(url).query).get("variant", [None])[0]
        if variant:
            selected = [
                o
                for o in offers
                if parse_qs(urlsplit(str(o.get("url", ""))).query).get("variant", [None])[0]
                == variant
            ]
            offers = selected  # Missing selected variant must not fall back to another.
        statuses = {
            SCHEMA_STATUS.get(
                str(o.get("availability", "")).rstrip("/").rsplit("/", 1)[-1].casefold(),
                Status.UNKNOWN,
            )
            for o in offers
        }
        prices = [money_minor(o.get("price", o.get("lowPrice"))) for o in offers]
        if len(statuses) == 1 and Status.UNKNOWN not in statuses:
            snapshot.status = statuses.pop()
            snapshot.method, snapshot.confidence = "json_ld", 0.97
            snapshot.evidence = (
                "Product offer availability: "
                + ", ".join(str(o.get("availability")) for o in offers)[:350]
            )
        elif len(statuses) > 1:
            snapshot.attributes["variant_note"] = (
                "Variant availability differs; use an exact variant URL."
            )
        non_null_prices = [p for p in prices if p is not None]
        if non_null_prices:
            snapshot.price_minor = min(non_null_prices)
            snapshot.currency = str(offers[0].get("priceCurrency", "INR"))[:3].upper()
            if len(set(non_null_prices)) > 1:
                snapshot.attributes["price_note"] = "Starting price across variants."

    if snapshot.price_minor is None:
        snapshot.price_minor = displayed_price(soup, source)

    stock_nodes = [node for selector in source.stock_selectors for node in soup.select(selector)]
    stock_text = " ".join(
        node.get_text(" ", strip=True) for node in stock_nodes if node.get_text(strip=True)
    )
    dom_status = text_status(stock_text)
    if dom_status != Status.UNKNOWN:
        if snapshot.status != Status.UNKNOWN and snapshot.status != dom_status:
            snapshot.status, snapshot.confidence, snapshot.method = Status.UNKNOWN, 0.0, "conflict"
            snapshot.evidence = "Structured availability conflicts with the product stock label."
            snapshot.attributes["review_required"] = "Conflicting stock evidence."
        else:
            snapshot.status, snapshot.confidence, snapshot.method = (
                dom_status,
                0.95,
                "stock_selector",
            )
            snapshot.evidence = stock_text[:350]
    elif stock_text and snapshot.status == Status.UNKNOWN:
        snapshot.evidence = stock_text[:350]

    # A variable WooCommerce/BigCommerce page's initial offer can describe only a default variant.
    variant_selects = soup.select(".variations select, [data-product-attribute] select")
    needs_selection = any(
        len(s.select("option[value]:not([value=''])")) > 1 for s in variant_selects
    )
    needs_selection = needs_selection or bool(
        re.search(r"\b\d+(?:\s*/\s*\d+)+\s*GB\b", title, re.I)
    )
    if needs_selection:
        snapshot.status, snapshot.confidence, snapshot.method = (
            Status.UNKNOWN,
            0.0,
            "variant_selection_required",
        )
        snapshot.evidence = "This product requires a variant selection; initial HTML does not verify the chosen variant."
        snapshot.attributes["variant_note"] = (
            "Choose an exact variant; automatic BigCommerce/WooCommerce variant selection is not implemented."
        )
        if snapshot.price_minor is not None:
            snapshot.attributes["price_note"] = (
                "Default/starting price; exact variant price is unverified."
            )
    snapshot.attributes.update(attributes_from(snapshot.title, scope_text))
    return snapshot


def extract_shopify(raw: str, url: str) -> Snapshot:
    try:
        product = json.loads(raw)
        if not isinstance(product, dict) or not isinstance(product.get("variants"), list):
            raise ValueError
    except (ValueError, TypeError) as exc:
        raise ValueError("Invalid Shopify product response.") from exc
    title = str(product.get("title", "Unknown product"))[:300]
    selected_id = parse_qs(urlsplit(url).query).get("variant", [None])[0]
    variants = [
        v for v in product["variants"] if not selected_id or str(v.get("id")) == selected_id
    ]
    snapshot = Snapshot(title=title, method="shopify_ajax")
    if not variants:
        snapshot.evidence = "Requested variant was not returned by the retailer."
        return snapshot
    if selected_id or len(variants) == 1:
        title += " — " + str(variants[0].get("title", ""))
        snapshot.title = title[:300]
        snapshot.attributes = attributes_from(title, title)
    available = {v.get("available") for v in variants}
    if available == {True}:
        snapshot.status = (
            Status.PRE_ORDER if text_status(title) == Status.PRE_ORDER else Status.IN_STOCK
        )
    elif available == {False}:
        snapshot.status = Status.OUT_OF_STOCK
    snapshot.confidence = 0.98 if snapshot.status != Status.UNKNOWN else 0.0
    snapshot.evidence = (
        "Retailer product API: "
        + "; ".join(f"{v.get('title')}: available={v.get('available')}" for v in variants)[:500]
    )
    prices = [
        v["price"]
        for v in variants
        if isinstance(v.get("price"), int)
        and not isinstance(v.get("price"), bool)
        and 0 <= v["price"] < 10_000_000_000
    ]
    snapshot.price_minor = min(prices) if prices else None
    if len(variants) > 1:
        snapshot.attributes["variant_note"] = (
            "Product-level result; use ?variant=ID for exact stock, price and RAM."
        )
        snapshot.attributes["variants"] = "; ".join(
            f"{v.get('title')} [variant={v.get('id')}]: {'in stock' if v.get('available') is True else 'out of stock' if v.get('available') is False else 'unknown'}"
            for v in variants
        )[:1600]
        snapshot.attributes["price_note"] = "Starting price across variants."
    return snapshot
