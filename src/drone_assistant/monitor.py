from __future__ import annotations

import asyncio
import logging

from .config import Settings
from .domain import FetchError, Snapshot, Status
from .extraction import extract_html, extract_shopify, page_scope
from .llm import SmallLLM
from .sources import Registry
from .storage import Store

logger = logging.getLogger(__name__)


def initialize(store: Store, registry: Registry):
    for seed in registry.seeds:
        url = registry.canonical(seed["url"], product=True)
        store.add_product(url, registry.resolve(url).key, seed["title"], seed=True)
    for query in registry.seed_queries:
        store.add_rule(query, seed=True)


class Monitor:
    def __init__(
        self, settings: Settings, store: Store, registry: Registry, fetcher, llm: SmallLLM
    ):
        self.settings, self.store, self.registry = settings, store, registry
        self.fetcher, self.llm = fetcher, llm
        self.lock = asyncio.Lock()

    async def check_product(self, product: dict) -> Snapshot:
        try:
            source = self.registry.resolve(product["url"])
            raw, kind = await self.fetcher.product(product["url"])
            if kind == "shopify":
                snapshot = extract_shopify(raw, product["url"])
            else:
                snapshot = extract_html(raw, source, product["url"])
                if snapshot.title == "Unknown product" and not snapshot.error:
                    snapshot.error = "product_markup_missing"
                if (
                    snapshot.status == Status.UNKNOWN
                    and not snapshot.error
                    and snapshot.method not in {"conflict", "variant_selection_required"}
                    and "variant_note" not in snapshot.attributes
                ):
                    if self.settings.browser_enabled:
                        try:
                            raw = await self.fetcher.render(product["url"])
                            snapshot = extract_html(raw, source, product["url"])
                        except FetchError:
                            # Static evidence can still be classified; no browser errors become stock claims.
                            logger.info("Browser fallback unavailable for P%s", product["id"])
                    _, _, text = page_scope(raw, source)
                    snapshot = await self.llm.classify(snapshot, text)
        except FetchError as exc:
            snapshot = Snapshot(
                title=product["title"],
                error=str(exc)[:100],
                evidence="Fetch failed; availability is unverified.",
                method="fetch_error",
            )
        except Exception:
            logger.warning("Product check failed safely for P%s", product["id"])
            snapshot = Snapshot(
                title=product["title"], error="extraction_failed", method="parse_error"
            )
        self.store.observe(product["id"], snapshot)
        logger.info(
            "P%s source=%s status=%s method=%s error=%s",
            product["id"],
            product["source"],
            snapshot.status,
            snapshot.method,
            snapshot.error or "none",
        )
        return snapshot

    async def discover_rule(
        self, rule: dict, *, source_key: str | None = None, max_pages: int | None = None
    ) -> tuple[set[int], int]:
        product_ids, errors = set(), 0
        for source in self.registry.sources.values():
            if source_key and source.key != source_key:
                continue
            found_ids, complete = [], True
            for page in range(1, (max_pages or self.settings.max_discovery_pages) + 1):
                listing_url = source.search_url(rule["query"], page)
                try:
                    html = await self.fetcher.fetch(listing_url)
                    found = self.registry.discover(html, listing_url, rule["query"])
                    if not found:
                        # Empty results and layout drift are intentionally not enough to establish a baseline.
                        if not found_ids:
                            complete = False
                        break
                    for url, title in found[: self.settings.max_products_per_listing]:
                        existing = self.store.conn.execute(
                            "SELECT id FROM products WHERE url=?", (url,)
                        ).fetchone()
                        if (
                            not existing
                            and self.store.product_count() >= self.settings.max_products
                        ):
                            logger.warning("Catalog product capacity reached")
                            complete = False
                            break
                        product_id = self.store.add_product(url, source.key, title)
                        found_ids.append(product_id)
                        product_ids.add(product_id)
                except FetchError as exc:
                    logger.info(
                        "Discovery restricted source=%s rule=R%s reason=%s",
                        source.key,
                        rule["id"],
                        exc,
                    )
                    complete, errors = False, errors + 1
                    break
            self.store.link_discoveries(rule["id"], source.key, found_ids, complete=complete)
        return product_ids, errors

    async def run_once(
        self, *, discovery: bool = True, rule_id: int | None = None, product_id: int | None = None
    ) -> dict:
        async with self.lock:
            run_id, before = self.store.start_run(), self.store.product_count()
            stats = {"checked": 0, "failed": 0, "discovered": 0, "listing_errors": 0}
            selected_ids = set()
            if discovery and product_id is None:
                rules = [self.store.rule(rule_id)] if rule_id else self.store.rules()
                for rule in rules:
                    found, errors = await self.discover_rule(rule)
                    selected_ids.update(found)
                    stats["listing_errors"] += errors
            if product_id:
                products = [self.store.product(product_id)]
            elif rule_id:
                products = [self.store.product(i) for i in sorted(selected_ids)][:40]
            else:
                products = self.store.active_products()
            for product in products:
                if product:
                    snapshot = await self.check_product(product)
                    stats["checked"] += 1
                    stats["failed"] += int(bool(snapshot.error))
            stats["discovered"] = self.store.product_count() - before
            self.store.finish_run(run_id, stats)
            # Manual searches must not postpone the full periodic scan.
            if rule_id or product_id:
                self.store.set_meta("last_manual_scan", str(run_id))
            else:
                self.store.set_meta("last_full_scan", self.store.get_meta("last_completed_scan"))
            return stats
