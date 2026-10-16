import pytest

from drone_assistant.domain import Status
from drone_assistant.extraction import extract_html

playwright = pytest.importorskip("playwright.async_api")


async def test_headless_javascript_and_product_extraction(registry):
    async with playwright.async_playwright() as engine:
        try:
            browser = await engine.chromium.launch(headless=True)
        except playwright.Error as exc:
            if "Executable doesn't exist" in str(exc):
                pytest.skip(
                    "Install the optional Chromium binary to run the browser integration test."
                )
            raise
        try:
            page = await browser.new_page()
            await page.set_content(
                '<div class="summary"><h1>Pi 5 HAT</h1><p class="stock">Loading</p></div><script>setTimeout(()=>{document.querySelector(".stock").textContent="In stock"},20)</script>'
            )
            await page.wait_for_function(
                'document.querySelector(".stock").textContent === "In stock"'
            )
            snapshot = extract_html(
                await page.content(), registry.sources["robu"], "https://robu.in/product/demo-hat/"
            )
            assert snapshot.status == Status.IN_STOCK
            assert snapshot.method == "stock_selector"
        finally:
            await browser.close()
