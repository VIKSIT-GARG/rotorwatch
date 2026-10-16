from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from string import Template

from .app_store import AppStore
from .domain import utcnow
from .openclaw import OpenClaw, OpenClawError
from .sources import Registry

# Fixed code owned by the application. No model-generated JavaScript or selectors.
INSPECT = r"""() => {
 const forms=[...document.querySelectorAll('form.cart,form[action*="/cart/add"],form[data-cart-item-add]')];
 const candidates=forms.flatMap(f=>[...f.querySelectorAll('button,input[type="submit"]')].filter(b=>/^add\s+to\s+(cart|basket)$/i.test((b.textContent||b.value||'').trim())).map(b=>({f,b})));
 if(candidates.length!==1)return{url:location.href,blocked:'A unique Add to cart control was not found. Login, product selection or manual review may be needed.'};
 const {f,b}=candidates[0],q=f.querySelector('[name="quantity"],input.qty');
 const variation=f.querySelector('[name="variation_id"]');
 const unresolved=[...f.querySelectorAll('select[name^="attribute_"]')].some(s=>!s.value)||(variation&&!Number(variation.value));
 const variant=f.querySelector('[name="id"]')?.value||variation?.value||'';
 const options=f.querySelector('select[name="id"]');
 const priceNode=document.querySelector('.summary .woocommerce-variation-price .price,.summary .price,.product__info-container .price,.productView [data-product-price-with-tax],.productView [data-product-price-without-tax]');
 const sale=priceNode?.querySelector('ins,.price__sale .price-item--sale');
 const price=(sale||priceNode)?.textContent?.trim()||'';
 const counters=[...document.querySelectorAll('.cart-contents .count,.cart-count-bubble,.cart-quantity,[data-cart-quantity]')].map(e=>e.textContent.trim()).join(' ');
 const notice=[...document.querySelectorAll('.woocommerce-message,.alertBox--success,[role="status"]')].map(e=>e.textContent.trim()).join(' ');
 return{url:location.href,title:document.querySelector('h1')?.textContent?.trim()||'',variant,price,counters,notice,
   quantity:q?.value||'1',quantityEditable:!!q,button:(b.textContent||b.value).trim(),
   blocked:unresolved?'Choose an exact product variant first.':b.disabled?'Add to cart is disabled.':!price?'The current page price could not be verified. Review the retailer page manually.':options&&options.options.length>1&&!new URL(location.href).searchParams.has('variant')?'Use an exact variant URL first.':''};
}"""


def fingerprint(probe: dict) -> str:
    fields = {k: probe.get(k) for k in ("url", "title", "variant", "price", "button", "blocked")}
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


class Cart:
    """OpenClaw's Playwright-backed browser, with a durable human review before cart mutation."""

    def __init__(self, store: AppStore, gateway: OpenClaw, registry: Registry):
        self.store, self.gateway, self.registry = store, gateway, registry
        self.lock = asyncio.Lock()

    def request(self, identifier: int) -> dict:
        row = self.store.conn.execute(
            "SELECT r.*,p.url,p.source FROM purchase_requests r JOIN products p ON p.id=r.product_id WHERE r.id=?",
            (identifier,),
        ).fetchone()
        if not row:
            raise OpenClawError(404, "Purchase review not found.")
        return dict(row)

    def update(self, identifier: int, state: str, note: str, **fields):
        allowed = {"browser_target", "browser_probe", "browser_fingerprint", "prepared_at"}
        if fields.keys() - allowed:
            raise ValueError("Invalid purchase fields.")
        with self.store.conn:
            self.store.conn.execute(
                "UPDATE purchase_requests SET state=?,note=?"
                + "".join(f",{key}=?" for key in fields)
                + " WHERE id=?",
                (state, note, *fields.values(), identifier),
            )

    async def inspect(self, target: str, expected_url: str) -> dict:
        result = await self.gateway.browser(
            "act", targetId=target, request={"kind": "evaluate", "fn": INSPECT}
        )
        probe = result.get("result", result)
        if not isinstance(probe, dict) or not isinstance(probe.get("url"), str):
            raise OpenClawError(502, "The browser did not return a verifiable product page.")
        try:
            if self.registry.canonical(probe["url"], product=True) != self.registry.canonical(
                expected_url, product=True
            ):
                raise ValueError("wrong page")
        except ValueError:
            raise OpenClawError(
                409,
                "Browser navigation changed the retailer/product. Review the exact product URL again.",
            ) from None
        return probe

    async def prepare(self, identifier: int) -> dict:
        async with self.lock:
            item = self.request(identifier)
            if item["state"] in {"cart_submitting", "cart_updated", "cart_check_required"}:
                raise OpenClawError(
                    409,
                    "A cart attempt already exists. Check the retailer cart; it will not be repeated automatically.",
                )
            url = self.registry.canonical(item["url"], product=True)
            opened = await self.gateway.browser("open", targetUrl=url)
            target = opened.get("targetId")
            if not isinstance(target, str) or not target:
                raise OpenClawError(502, "OpenClaw did not return a browser tab ID.")
            probe = await self.inspect(target, url)
            if probe.get("blocked"):
                self.update(
                    identifier, "browser_action_required", probe["blocked"], browser_target=target
                )
                return {
                    "state": "browser_action_required",
                    "message": probe["blocked"],
                    "preview": probe,
                }
            if item["quantity"] != 1 and not probe.get("quantityEditable"):
                self.update(
                    identifier,
                    "browser_action_required",
                    "The requested quantity cannot be set safely on this form.",
                )
                return {
                    "state": "browser_action_required",
                    "message": "Set the quantity manually at the retailer.",
                    "preview": probe,
                }
            self.update(
                identifier,
                "cart_ready",
                "Product page inspected. Confirm Add to cart in the dashboard within five minutes. Checkout is not performed.",
                browser_target=target,
                browser_probe=json.dumps(probe),
                browser_fingerprint=fingerprint(probe),
                prepared_at=utcnow(),
            )
            return {
                "state": "cart_ready",
                "preview": probe,
                "quantity": item["quantity"],
                "message": "Review this exact variant, current page price and quantity, then confirm Add to cart. No order will be placed.",
            }

    async def confirm(self, identifier: int) -> dict:
        async with self.lock:
            item = self.request(identifier)
            if item["state"] != "cart_ready" or not item["prepared_at"]:
                raise OpenClawError(
                    409, "Prepare and review this product page before adding to cart."
                )
            if (
                datetime.now(UTC) - datetime.fromisoformat(item["prepared_at"])
            ).total_seconds() > 300:
                raise OpenClawError(409, "The cart preview expired. Prepare a fresh preview.")
            probe = await self.inspect(item["browser_target"], item["url"])
            if probe.get("blocked") or fingerprint(probe) != item["browser_fingerprint"]:
                self.update(
                    identifier,
                    "browser_action_required",
                    "The product, variant or price changed. Prepare and review it again.",
                )
                raise OpenClawError(
                    409,
                    "The product page changed since review. Nothing was added; prepare it again.",
                )
            # Mark before any mutating request. A timeout/restart can never blindly replay it.
            self.update(identifier, "cart_submitting", "Cart action in progress; do not repeat.")
            code = Template("""() => {
 const before=($INSPECT)(); if(before.blocked||before.url!==$URL||before.variant!==$VARIANT||before.price!==$PRICE)return{clicked:false};
 const forms=[...document.querySelectorAll('form.cart,form[action*="/cart/add"],form[data-cart-item-add]')];
 const matches=forms.flatMap(f=>[...f.querySelectorAll('button,input[type="submit"]')].filter(b=>/^add\\s+to\\s+(cart|basket)$$/i.test((b.textContent||b.value||'').trim())).map(b=>({f,b})));
 if(matches.length!==1||matches[0].b.disabled)return{clicked:false};
 const {f,b}=matches[0],q=f.querySelector('[name="quantity"],input.qty');
 if(q){const setter=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;setter.call(q,String($QTY));q.dispatchEvent(new Event('input',{bubbles:true}));q.dispatchEvent(new Event('change',{bubbles:true}));if(Number(q.value)!==$QTY)return{clicked:false};}
 else if($QTY!==1)return{clicked:false};
 b.click(); return{clicked:true};
}""").substitute(
                INSPECT=INSPECT,
                URL=json.dumps(probe["url"]),
                VARIANT=json.dumps(probe.get("variant", "")),
                PRICE=json.dumps(probe.get("price", "")),
                QTY=item["quantity"],
            )
            try:
                result = await self.gateway.browser(
                    "act", targetId=item["browser_target"], request={"kind": "evaluate", "fn": code}
                )
                outcome = result.get("result", result)
                if outcome.get("clicked") is not True:
                    self.update(
                        identifier,
                        "browser_action_required",
                        "The page changed before the click. Nothing was clicked; prepare again.",
                    )
                    return {
                        "state": "browser_action_required",
                        "message": "Cart control changed. Prepare a fresh preview.",
                    }
                # Read-only observation after the click; no automatic retry of the click.
                await asyncio.sleep(1)
                after = await self.gateway.browser(
                    "act",
                    targetId=item["browser_target"],
                    request={"kind": "evaluate", "fn": INSPECT},
                )
                after = after.get("result", after)
                notice = str(after.get("notice", ""))
                verified = (
                    "added to your cart" in notice.lower()
                    and bool(probe.get("title"))
                    and probe["title"].lower() in notice.lower()
                )
                state = "cart_updated" if verified else "cart_check_required"
                note = (
                    "Retailer confirmed this product was added to cart. No order was placed."
                    if verified
                    else "Add-to-cart was clicked once. Check the retailer cart manually; success/quantity was not confirmed. No order was placed."
                )
                self.update(identifier, state, note)
                return {"state": state, "message": note}
            except (OpenClawError, ValueError, TypeError, AttributeError):
                self.update(
                    identifier,
                    "cart_check_required",
                    "The cart request outcome is uncertain. Check the retailer cart manually before making another request. No checkout was attempted.",
                )
                return {
                    "state": "cart_check_required",
                    "message": "Cart outcome is uncertain. Check the retailer cart; no automatic retry or checkout was performed.",
                }
