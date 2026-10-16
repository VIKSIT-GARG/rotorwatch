import json

import pytest

from drone_assistant.domain import Status, money_minor, query_matches
from drone_assistant.extraction import extract_html, extract_shopify, text_status


@pytest.mark.parametrize(
    "text,status",
    [
        ("In stock", Status.IN_STOCK),
        ("Out of stock", Status.OUT_OF_STOCK),
        ("Not in stock", Status.OUT_OF_STOCK),
        ("Pre-order open", Status.PRE_ORDER),
        ("Pre-orders closed", Status.OUT_OF_STOCK),
        ("Out of stock. In stock.", Status.UNKNOWN),
        ("Check availability", Status.UNKNOWN),
        ("Add to cart", Status.UNKNOWN),
    ],
)
def test_conservative_stock_labels(text, status):
    assert text_status(text) == status


def test_related_product_stock_does_not_override_main_product(registry):
    html = '<div class="summary"><h1>Pi 5</h1><p class="stock">Out of stock</p></div><aside><p class="stock">In stock</p></aside>'
    assert (
        extract_html(html, registry.sources["robu"], "https://robu.in/product/pi-5/").status
        == Status.OUT_OF_STOCK
    )


def test_conflicting_dom_and_schema_requires_review(registry):
    html = (
        '<script type="application/ld+json">'
        + json.dumps(
            {
                "@type": "Product",
                "name": "Pi 5",
                "offers": {"availability": "https://schema.org/InStock"},
            }
        )
        + '</script><div class="summary"><h1>Pi 5</h1><p class="stock">Out of stock</p></div>'
    )
    result = extract_html(html, registry.sources["robu"], "https://robu.in/product/pi-5/")
    assert result.status == Status.UNKNOWN and result.method == "conflict"


def test_variable_product_does_not_claim_default_variant(registry):
    html = '<div class="summary"><h1>Pi 5 4GB / 8GB</h1><p class="stock">In stock</p><table class="variations"><select><option value="4">4GB</option><option value="8">8GB</option></select></table></div>'
    result = extract_html(html, registry.sources["robu"], "https://robu.in/product/pi-5/")
    assert result.status == Status.UNKNOWN
    assert result.method == "variant_selection_required"


def test_challenge_page_not_product(registry):
    result = extract_html(
        "<title>Just a moment...</title><h1>Checking browser</h1>",
        registry.sources["robu"],
        "https://robu.in/product/pi-5/",
    )
    assert result.status == Status.UNKNOWN and result.error == "bot_challenge"


def test_json_ld_graph_and_price(registry):
    html = (
        '<h1>Pi 5 8GB</h1><script type="application/ld+json">'
        + json.dumps(
            {
                "@graph": [
                    {"@type": "Organization", "name": "Retailer"},
                    {
                        "@type": "Product",
                        "name": "Pi 5 8GB",
                        "offers": {
                            "price": "7,200.50",
                            "priceCurrency": "INR",
                            "availability": "https://schema.org/PreOrder",
                        },
                    },
                ]
            }
        )
        + "</script>"
    )
    result = extract_html(html, registry.sources["robu"], "https://robu.in/product/pi-5/")
    assert result.status == Status.PRE_ORDER
    assert result.price_minor == 720050
    assert result.attributes["ram_gb"] == "8"


def test_shopify_mixed_variants_not_aggregated_into_in_stock():
    raw = json.dumps(
        {
            "title": "Pi 5",
            "variants": [
                {"id": 1, "title": "4GB", "available": True, "price": 600000},
                {"id": 2, "title": "8GB", "available": False, "price": 800000},
            ],
        }
    )
    aggregate = extract_shopify(raw, "https://thinkrobotics.com/products/raspberry-pi-5")
    exact = extract_shopify(raw, "https://thinkrobotics.com/products/raspberry-pi-5?variant=2")
    assert aggregate.status == Status.UNKNOWN
    assert "ram_gb" not in aggregate.attributes
    assert exact.status == Status.OUT_OF_STOCK
    assert exact.price_minor == 800000 and exact.attributes["ram_gb"] == "8"
    assert (
        extract_shopify(raw, "https://thinkrobotics.com/products/raspberry-pi-5?variant=999").status
        == Status.UNKNOWN
    )


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", True, "not a price"])
def test_invalid_prices_rejected(value):
    assert money_minor(value) is None


def test_query_avoids_pi_500_false_positive():
    assert not query_matches("Pi 5", "Raspberry Pi 500")
    assert query_matches("flight controllers", "Flight controller")


def test_multi_ram_title_needs_selection_even_without_select_markup(registry):
    html = (
        '<h1>Raspberry Pi 5 with 2/4/8GB RAM</h1><script type="application/ld+json">'
        + json.dumps(
            {
                "@type": "Product",
                "name": "Raspberry Pi 5 with 2/4/8GB RAM",
                "offers": {"availability": "https://schema.org/InStock", "price": "5000"},
            }
        )
        + "</script>"
    )
    result = extract_html(
        html, registry.sources["evelta"], "https://evelta.com/raspberry-pi-5-with-2-4-8gb-ram/"
    )
    assert result.status == Status.UNKNOWN and result.method == "variant_selection_required"
    assert "ram_gb" not in result.attributes
