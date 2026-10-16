from dataclasses import replace

import httpx
import pytest

from drone_assistant.config import Settings
from drone_assistant.domain import FetchError
from drone_assistant.fetching import Fetcher


async def make_fetcher(registry, handler, monkeypatch, **kwargs):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False)
    fetcher = Fetcher(replace(Settings(), source_delay=0, **kwargs), registry, client)

    async def public_host(host):
        return None

    monkeypatch.setattr(fetcher, "_public_host", public_host)
    return fetcher, client


async def test_disallowed_page_never_requested(registry, monkeypatch):
    requests = []

    def handler(request):
        requests.append(request.url.path)
        return httpx.Response(
            200, text="User-agent: *\nDisallow: /product/", headers={"content-type": "text/plain"}
        )

    fetcher, client = await make_fetcher(registry, handler, monkeypatch)
    try:
        with pytest.raises(FetchError, match="robots_disallowed"):
            await fetcher.fetch("https://robu.in/product/pi-5/")
        assert requests == ["/robots.txt"]
    finally:
        await client.aclose()


async def test_403_and_unknown_robots_fail_closed(registry, monkeypatch):
    fetcher, client = await make_fetcher(registry, lambda r: httpx.Response(403), monkeypatch)
    try:
        with pytest.raises(FetchError, match="robots_unavailable_http_403"):
            await fetcher.fetch("https://robu.in/product/pi-5/")
    finally:
        await client.aclose()


async def test_redirect_to_metadata_never_followed(registry, monkeypatch):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(
            302, headers={"location": "https://169.254.169.254/latest/meta-data/"}
        )

    fetcher, client = await make_fetcher(registry, handler, monkeypatch)
    try:
        with pytest.raises(FetchError, match="redirect_destination_blocked"):
            await fetcher.fetch("https://robu.in/product/pi-5/")
        assert not any("169.254" in url for url in seen)
    finally:
        await client.aclose()


async def test_redirect_robots_checked_before_destination_fetch(registry, monkeypatch):
    seen = []

    def handler(request):
        seen.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(
                200,
                text="User-agent: *\nDisallow: /private/",
                headers={"content-type": "text/plain"},
            )
        return httpx.Response(302, headers={"location": "https://robu.in/private/stock/"})

    fetcher, client = await make_fetcher(registry, handler, monkeypatch)
    try:
        with pytest.raises(FetchError, match="redirect_robots_disallowed"):
            await fetcher.fetch("https://robu.in/product/pi-5/")
        assert "/private/stock/" not in seen
    finally:
        await client.aclose()


async def test_response_size_is_bounded(registry, monkeypatch):
    def handler(request):
        return (
            httpx.Response(404)
            if request.url.path == "/robots.txt"
            else httpx.Response(200, text="X" * 300)
        )

    fetcher, client = await make_fetcher(registry, handler, monkeypatch, max_response_bytes=100)
    try:
        with pytest.raises(FetchError, match="response_too_large"):
            await fetcher.fetch("https://robu.in/product/pi-5/")
    finally:
        await client.aclose()


async def test_allowlisted_hostname_resolving_private_is_blocked(registry, monkeypatch):
    import socket

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 443))],
    )
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200)), trust_env=False
    )
    fetcher = Fetcher(Settings(), registry, client)
    try:
        with pytest.raises(FetchError, match="private_address_blocked"):
            await fetcher.fetch("https://robu.in/product/pi-5/")
    finally:
        await client.aclose()


async def test_html_challenge_returned_as_robots_fails_closed(registry, monkeypatch):
    fetcher, client = await make_fetcher(
        registry,
        lambda r: httpx.Response(200, text="<html><title>Checking browser</title></html>"),
        monkeypatch,
    )
    try:
        with pytest.raises(FetchError, match="robots_invalid_response"):
            await fetcher.fetch("https://robu.in/product/pi-5/")
    finally:
        await client.aclose()
