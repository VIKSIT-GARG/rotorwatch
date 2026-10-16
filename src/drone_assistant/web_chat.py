from __future__ import annotations

import json
import re

from .app_store import WEB_OWNER, AppStore
from .chat import Chat, parse_intent
from .display import ist_text, ist_time
from .domain import Intent, format_money, utcnow
from .llm import SmallLLM
from .messages import product_text


class WebChat:
    """LLM intent → bounded Python database query → grounded answer with product cards."""

    def __init__(self, store: AppStore, llm: SmallLLM, commands: Chat, shopping=None):
        self.store, self.llm, self.commands = store, llm, commands
        self.shopping = shopping

    async def ask(self, message: str, session_id: str, owner: str = WEB_OWNER) -> dict:
        self.store.add_message(session_id, "user", message)
        if self.shopping:
            result = await self.shopping.ask(message, session_id)
            if result is not None:
                self.store.add_message(
                    session_id, "assistant", result["answer"], [p["id"] for p in result["products"]]
                )
                return result
        intent = await self.llm.interpret(message) if self.llm.enabled else None
        intent = intent or parse_intent(message) or Intent(query=message.strip())
        if re.search(r"\b(?:changed|changes|updates|activity)\b", message, re.I) and not re.search(
            r"\b(?:watch|track|remove|stop)\b", message, re.I
        ):
            events = self.store.recent_changes()
            latest = self.store.conn.execute(
                "SELECT MAX(observed_at) FROM observations"
            ).fetchone()[0]
            answer = (
                f"Recorded changes in the last 24 hours · as of {ist_time(utcnow())}\n"
                f"Latest product check: {ist_time(latest)}\n"
                f"Latest completed scheduled scan: {ist_time(self.store.get_meta('last_full_scan'))}\n\n"
            )
            if events:
                answer += f"Newest recorded change: {ist_time(events[0]['created_at'])}. No later change has been recorded.\n\n"
            if not events:
                answer += "No meaningful changes were recorded in this period. Initial catalog checks are not counted as changes."
            else:
                labels = {
                    "stock": "Stock changed",
                    "price": "Price changed",
                    "new_match": "New match recorded",
                    "specifications": "Details changed",
                    "verification": "Verification changed",
                }
                entries = []
                for event in events:
                    facts = event["facts"]
                    price = format_money(facts.get("price_minor"), facts.get("currency", "INR"))
                    if event["kind"] == "price" and facts.get("previous_price_minor") is not None:
                        price = (
                            format_money(
                                facts["previous_price_minor"], facts.get("currency", "INR")
                            )
                            + " → "
                            + price
                        )
                    entries.append(
                        f"{ist_time(event['created_at'])} · {labels.get(event['kind'], event['kind'])}\n"
                        f"P{event['product_id']} · {facts['title']}\n"
                        f"{facts['source']} · {facts['status'].replace('_', ' ')} · {price}\n"
                        f"Observed at that event: {ist_time(facts.get('checked_at'))}\n{facts['url']}"
                    )
                answer += "\n\n".join(entries)
            answer += "\n\nThese are historical change events, not live stock confirmations. Ask for a product's latest price/availability to recheck it, or use Refresh checks for a catalog scan."
            self.store.add_message(session_id, "assistant", answer)
            return {"answer": answer, "products": [], "model_used": False, "action": "activity"}
        if re.fullmatch(
            r"(?:show |what(?:'s| is) (?:on )?)?(?:me )?(?:my |the )?watchlist[?.! ]*",
            message,
            re.I,
        ):
            intent = Intent("watchlist")
        if intent.action in {"watch", "unwatch", "watchlist", "help", "start"}:
            # Mutations still use validated IDs/URLs in the bounded command handler.
            canonical = {
                "watch": "/watch " + ("P" + str(intent.ids[0]) if intent.ids else intent.query),
                "unwatch": "/unwatch " + intent.query,
                "watchlist": "/watchlist",
                "help": "/help",
                "start": "/start",
            }[intent.action]
            answer = await self.commands.handle(owner, canonical)
            self.store.add_message(session_id, "assistant", answer)
            return {"answer": answer, "products": [], "model_used": False, "action": intent.action}
        if intent.ids:
            rows = [self.store.product(i) for i in intent.ids[:4]]
            rows = [p for p in rows if p]
        else:
            rows = self.store.search(intent)
        # Store queries are parameterized/typed. The model never supplies executable SQL.
        records = [self.store.public_product(p) for p in rows[:6]]
        answer = "I couldn't find a matching product in the monitored database. Add a product URL or a name to your watchlist to start tracking it."
        if (
            not records
            and intent.action == "search"
            and intent.query
            and self.commands.queue_search
        ):
            answer = "No catalog match yet. " + self.commands.queue_note(owner, intent)
            if owner == WEB_OWNER:
                answer = answer.replace(
                    "I’ll reply here when it finishes.",
                    "Results will appear in Explore when the scan finishes; then search again.",
                )
        if records:
            if intent.action == "history":
                history = self.store.history(records[0]["id"], 8)
                answer = f"Here are the latest checks for P{records[0]['id']}:\n" + "\n".join(
                    f"{ist_time(r['observed_at'])} · {r['status']} · {r['evidence']}"
                    for r in history
                )
            else:
                answer = (
                    f"I found {len(records)} matching product{'s' if len(records) != 1 else ''} in the database.\n\n"
                    + "\n\n".join(product_text(p) for p in rows[:6])
                )
        model_used = False
        if records and self.llm.enabled and intent.action != "history":
            result = await self.llm._json(
                "database_answer",
                'Answer the question using only the supplied database records. Return {"answer":"a concise helpful reply","product_ids":[1]}. Reference IDs as P1. Do not invent stock, prices, specifications, compatibility, retailer guarantees or purchases. Unknown means unverified. Explain that records are observations at their check timestamps. If comparing unlike products, explain their different roles rather than naming a winner.',
                json.dumps({"question": message, "records": records}),
            )
            if (
                result
                and isinstance(result.get("answer"), str)
                and 0 < len(result["answer"]) <= 3500
            ):
                ids = result.get("product_ids", [])
                allowed = {p["id"] for p in records}
                if (
                    isinstance(ids, list)
                    and ids
                    and all(type(i) is int and i in allowed for i in ids)
                ):
                    answer, model_used = result["answer"], True
        answer = ist_text(answer)
        self.store.add_message(session_id, "assistant", answer, [p["id"] for p in records])
        return {
            "answer": answer,
            "products": records,
            "model_used": model_used,
            "action": intent.action,
        }
