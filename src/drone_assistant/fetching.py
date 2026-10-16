from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import socket
import time
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx

from .config import Settings
from .domain import FetchError
from .sources import Registry


class Fetcher:
    """Bounded HTTP, per-host pacing, robots.txt, safe redirects and optional Chromium."""

    def __init__(
        self, settings: Settings, registry: Registry, client: httpx.AsyncClient | None = None
    ):
        self.settings, self.registry = settings, registry
        self.client = client or httpx.AsyncClient(
            timeout=settings.request_timeout,
            follow_redirects=False,
            headers={"User-Agent": settings.user_agent, "Accept": "text/html,application/json"},
            limits=httpx.Limits(max_connections=5),
        )
        self._owns_client = client is None
        self._robots: dict[str, tuple[float, RobotFileParser]] = {}
        self._last_request: dict[str, float] = {}
        self._host_locks: dict[str, asyncio.Lock] = {}
        self._playwright = None
        self._browser = None
        self._browser_lock = asyncio.Lock()

    async def close(self):
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        if self._owns_client:
            await self.client.aclose()

    async def _public_host(self, host: str):
        try:
            addresses = await asyncio.wait_for(
                asyncio.to_thread(socket.getaddrinfo, host, 443, 0, socket.SOCK_STREAM),
                timeout=self.settings.request_timeout,
            )
        except (OSError, TimeoutError) as exc:
            # Explicit remote-DNS proxy support; fixed hostname allowlists still apply.
            if self.settings.proxy_resolves_dns and any(
                os.getenv(k) for k in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy")
            ):
                return
            raise FetchError("dns_failed") from exc
        if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            raise FetchError("private_address_blocked")

    async def _raw(
        self, url: str, delay: float | None = None, *, respect_robots: bool = False
    ) -> tuple[str, int, str]:
        current = url
        for _ in range(5):
            try:
                self.registry.resolve(current)
            except ValueError as exc:
                raise FetchError("redirect_destination_blocked") from exc
            host = urlsplit(current).hostname
            await self._public_host(host)
            lock = self._host_locks.setdefault(host, asyncio.Lock())
            async with lock:
                pause = max(
                    0.0,
                    self._last_request.get(host, 0)
                    + (self.settings.source_delay if delay is None else delay)
                    - time.monotonic(),
                )
                await asyncio.sleep(pause)
                self._last_request[host] = time.monotonic()
                try:
                    async with self.client.stream("GET", current) as response:
                        if response.status_code in (301, 302, 303, 307, 308):
                            current = urljoin(current, response.headers.get("location", ""))
                            try:
                                self.registry.resolve(current)
                            except ValueError as exc:
                                raise FetchError("redirect_destination_blocked") from exc
                            if respect_robots:
                                redirect_policy = await self.policy(current)
                                if not redirect_policy.can_fetch(self.settings.user_agent, current):
                                    raise FetchError("redirect_robots_disallowed")
                            continue
                        if response.status_code != 200:
                            return "", response.status_code, current
                        content_type = response.headers.get("content-type", "").lower()
                        if content_type and not any(
                            t in content_type for t in ("html", "json", "text/plain", "javascript")
                        ):
                            raise FetchError("unexpected_content_type")
                        chunks, total = [], 0
                        async for chunk in response.aiter_bytes():
                            total += len(chunk)
                            if total > self.settings.max_response_bytes:
                                raise FetchError("response_too_large")
                            chunks.append(chunk)
                        return (
                            b"".join(chunks).decode(response.encoding or "utf-8", errors="replace"),
                            response.status_code,
                            current,
                        )
                except (httpx.HTTPError, UnicodeError) as exc:
                    raise FetchError("http_request_failed") from exc
        raise FetchError("too_many_redirects")

    async def policy(self, url: str) -> RobotFileParser:
        self.registry.resolve(url)
        host = urlsplit(url).hostname
        cached = self._robots.get(host)
        if cached and time.monotonic() - cached[0] < 3600:
            return cached[1]
        text, status, _ = await self._raw(f"https://{host}/robots.txt")
        parser = RobotFileParser()
        if status in (404, 410):
            parser.parse(["User-agent: *", "Allow: /"])
        elif status == 200:
            if text.strip() and not re.search(r"(?im)^\s*user-agent\s*:", text):
                raise FetchError("robots_invalid_response")
            parser.parse(text.splitlines())
        else:
            # Fail closed, retry on the next scan rather than scraping through a block.
            raise FetchError(f"robots_unavailable_http_{status}")
        self._robots[host] = (time.monotonic(), parser)
        return parser

    async def fetch(self, url: str) -> str:
        parser = await self.policy(url)
        if not parser.can_fetch(self.settings.user_agent, url):
            raise FetchError("robots_disallowed")
        delay = max(
            self.settings.source_delay,
            parser.crawl_delay(self.settings.user_agent) or parser.crawl_delay("*") or 0,
        )
        html, status, final_url = await self._raw(url, delay, respect_robots=True)
        if status != 200:
            raise FetchError(f"http_{status}")
        # A redirect destination must independently permit crawling.
        if final_url != url:
            final_policy = await self.policy(final_url)
            if not final_policy.can_fetch(self.settings.user_agent, final_url):
                raise FetchError("redirect_robots_disallowed")
        return html

    async def product(self, url: str) -> tuple[str, str]:
        source = self.registry.resolve(url)
        if source.platform == "shopify":
            parts = urlsplit(url)
            ajax_url = urlunsplit(
                (parts.scheme, parts.netloc, parts.path.rstrip("/") + ".js", parts.query, "")
            )
            try:
                return await self.fetch(ajax_url), "shopify"
            except FetchError as exc:
                # A missing endpoint may fall back to HTML; access blocks are respected.
                if str(exc) not in ("http_404", "http_410"):
                    raise
        return await self.fetch(url), "html"

    async def render(self, url: str) -> str:
        if not self.settings.browser_enabled:
            raise FetchError("browser_disabled")
        source = self.registry.resolve(url)
        parser = await self.policy(url)
        if not parser.can_fetch(self.settings.user_agent, url):
            raise FetchError("robots_disallowed")
        async with self._browser_lock:
            try:
                if self._browser is None:
                    from playwright.async_api import async_playwright

                    self._playwright = await async_playwright().start()
                    self._browser = await self._playwright.chromium.launch(headless=True)
                context = await self._browser.new_context(
                    user_agent=self.settings.user_agent, service_workers="block"
                )
                page = await context.new_page()

                async def route_request(route):
                    request = route.request
                    parts = urlsplit(request.url)
                    if (
                        parts.scheme != "https"
                        or parts.port not in (None, 443)
                        or parts.hostname not in (*source.hosts, *source.resource_hosts)
                        or request.resource_type in ("image", "media", "font")
                    ):
                        await route.abort()
                        return
                    try:
                        await self._public_host(parts.hostname)
                        if parts.hostname in source.hosts:
                            policy = await self.policy(request.url)
                            if not policy.can_fetch(self.settings.user_agent, request.url):
                                await route.abort()
                                return
                        await route.continue_()
                    except (FetchError, ValueError):
                        await route.abort()

                await context.route("**/*", route_request)
                try:
                    response = await page.goto(
                        url,
                        wait_until="domcontentloaded",
                        timeout=int(self.settings.request_timeout * 1000),
                    )
                    if not response or response.status != 200:
                        raise FetchError("browser_navigation_failed")
                    await page.wait_for_timeout(1000)
                    html = await page.content()
                    if len(html.encode()) > self.settings.max_response_bytes:
                        raise FetchError("response_too_large")
                    return html
                finally:
                    await context.close()
                    # Keep no idle Chromium process between jobs.
                    await self._browser.close()
                    self._browser = None
            except FetchError:
                raise
            except Exception as exc:
                raise FetchError("browser_failed_or_not_installed") from exc


class FixtureFetcher:
    """Explicit offline transport; never silently used by a live monitor."""

    def __init__(self, responses: dict[str, str | Exception]):
        self.responses = responses

    async def fetch(self, url: str) -> str:
        result = self.responses.get(url, FetchError("fixture_not_found"))
        if isinstance(result, Exception):
            raise result
        return result

    async def product(self, url: str) -> tuple[str, str]:
        return await self.fetch(url), "html"

    async def render(self, url: str) -> str:
        raise FetchError("fixture_browser_disabled")

    async def close(self):
        pass
