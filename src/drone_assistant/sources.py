from __future__ import annotations

import ipaddress
import json
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup


@dataclass(frozen=True)
class Source:
    key: str
    hosts: tuple[str, ...]
    search_template: str
    platform: str
    product_paths: tuple[str, ...]
    stock_selectors: tuple[str, ...]
    main_selectors: tuple[str, ...]
    resource_hosts: tuple[str, ...] = ()
    listing_templates: dict[str, str] = field(default_factory=dict)

    def search_url(self, query: str, page: int = 1) -> str:
        from urllib.parse import quote_plus

        from .domain import category_for

        template = self.listing_templates.get(category_for(query), self.search_template)
        return template.format(query=quote_plus(query), page=page)


class Registry:
    def __init__(self, path: str = ""):
        raw = (
            Path(path).read_text()
            if path
            else files("drone_assistant").joinpath("data/sources.json").read_text()
        )
        data = json.loads(raw)
        self.sources = {
            item["key"]: Source(
                **{k: tuple(v) if isinstance(v, list) else v for k, v in item.items()}
            )
            for item in data["sources"]
        }
        self.seeds = data["seeds"]
        self.seed_queries = data["seed_queries"]

    def resolve(self, url: str) -> Source:
        try:
            parts = urlsplit(url)
            host = (parts.hostname or "").lower()
            if (
                parts.scheme != "https"
                or parts.username
                or parts.password
                or parts.port not in (None, 443)
            ):
                raise ValueError("Only HTTPS retailer URLs on port 443 are supported.")
            try:
                ipaddress.ip_address(host)
            except ValueError:
                pass
            else:
                raise ValueError("IP addresses are not retailer URLs.")
            for source in self.sources.values():
                if host in source.hosts:
                    return source
        except (ValueError, TypeError) as exc:
            raise ValueError(
                "Use a supported HTTPS retailer URL without credentials or a custom port."
            ) from exc
        raise ValueError("Supported retailers: " + ", ".join(self.sources))

    def canonical(self, url: str, *, product: bool = False) -> str:
        source = self.resolve(url)
        parts = urlsplit(url)
        if product and not self.is_product(url, source):
            raise ValueError("Please paste a product page URL, rather than a search/category page.")
        # Variant identifiers are essential; tracking and session parameters are not.
        keep = {"variant", "sku", "attribute_pa_ram", "attribute_ram", "attribute_pa_memory"}
        query = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query) if k in keep))
        return urlunsplit(("https", parts.hostname.lower(), parts.path or "/", query, ""))

    @staticmethod
    def is_product(url: str, source: Source) -> bool:
        path = urlsplit(url).path
        if source.platform == "bigcommerce":
            return path not in ("/", "/search.php", "/categories/", "/cart.php") and path.endswith(
                "/"
            )
        return any(marker in path for marker in source.product_paths)

    def discover(self, html: str, listing_url: str, query: str) -> list[tuple[str, str]]:
        from .domain import query_matches

        source = self.resolve(listing_url)
        soup = BeautifulSoup(html, "html.parser")
        found = {}
        # Restrict BigCommerce to product cards to avoid treating categories as products.
        selector = (
            "a.card-figure__link, h3.card-title a, h4.card-title a"
            if source.platform == "bigcommerce"
            else "a[href]"
        )
        for anchor in soup.select(selector):
            url = urljoin(listing_url, anchor.get("href", ""))
            try:
                if self.resolve(url).key != source.key or not self.is_product(url, source):
                    continue
                title = anchor.get_text(" ", strip=True) or anchor.get("title", "")
                if not title:
                    img = anchor.find("img")
                    title = img.get("alt", "") if img else ""
                if query_matches(query, title):
                    found[self.canonical(url, product=True)] = title[:300]
            except ValueError:
                continue
        return list(found.items())
