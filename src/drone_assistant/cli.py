from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

from .chat import Chat
from .config import Settings
from .demo import run_demo
from .fetching import Fetcher
from .llm import SmallLLM
from .messages import product_text
from .monitor import Monitor, initialize
from .runtime import run
from .sources import Registry
from .storage import Store
from .telegram import Telegram


async def execute(args):
    if args.command == "demo":
        if args.telegram_chat:
            settings = Settings.from_env()
            if not settings.telegram_token or args.telegram_chat not in settings.allowed_chat_ids:
                raise ValueError(
                    "The Telegram demo requires a bot token and a target ID explicitly included in TELEGRAM_ALLOWED_CHAT_IDS."
                )
            telegram = Telegram(settings.telegram_token)
            try:
                await telegram.verify()
                return await run_demo(telegram, args.telegram_chat)
            finally:
                await telegram.close()
        return await run_demo()
    settings = Settings.from_env()
    if args.command == "run":
        return await run(settings)
    if args.command == "health":
        try:
            connection = sqlite3.connect(
                f"file:{Path(settings.database).absolute()}?mode=ro", uri=True
            )
            row = connection.execute("SELECT value FROM metadata WHERE key='heartbeat'").fetchone()
            connection.close()
            healthy = (
                row is not None
                and (datetime.now(UTC) - datetime.fromisoformat(row[0])).total_seconds() < 90
            )
        except (sqlite3.Error, ValueError):
            healthy = False
        print(json.dumps({"process_healthy": healthy}))
        if not healthy:
            raise SystemExit(1)
        return
    if args.command == "telegram-id":
        if not settings.telegram_token:
            raise ValueError(
                "Set TELEGRAM_BOT_TOKEN, send /start to your bot in Telegram, then rerun this command."
            )
        telegram = Telegram(settings.telegram_token)
        try:
            updates = await telegram.call(
                "getUpdates", {"timeout": 0, "allowed_updates": ["message"]}
            )
            ids = sorted(
                {
                    str(u.get("message", {}).get("chat", {}).get("id"))
                    for u in updates
                    if u.get("message", {}).get("chat", {}).get("type") == "private"
                }
            )
            print("Private chat IDs:", ", ".join(ids) or "none; send /start to the bot, then retry")
        finally:
            await telegram.close()
        return
    registry, store = Registry(settings.sources_file), Store(settings.database)
    initialize(store, registry)
    try:
        if args.command == "init":
            print(
                f"Initialized {settings.database}: {store.product_count()} seed products, {len(store.rules())} discovery rules."
            )
            for product in store.active_products():
                print(f"P{product['id']} {product['title']} ({product['source']})")
        elif args.command == "backup":
            store.backup(args.destination)
            print(f"Database backup written to {args.destination}")
        elif args.command == "check":
            fetcher, llm = Fetcher(settings, registry), SmallLLM(settings, store)
            try:
                stats = await Monitor(settings, store, registry, fetcher, llm).run_once(
                    discovery=not args.no_discovery, product_id=args.product
                )
                print(json.dumps(stats, indent=2))
                for product in store.active_products():
                    print("\n" + product_text(product))
            finally:
                await fetcher.close()
                await llm.close()
        elif args.command == "chat":
            llm = SmallLLM(settings, store)
            chat = Chat(store, registry, llm)
            try:
                if args.text:
                    print(await chat.handle("local", " ".join(args.text)))
                else:
                    print(
                        "Local catalog chat. /help for commands; /quit to exit. No Telegram messages are sent."
                    )
                    while True:
                        try:
                            text = await asyncio.to_thread(input, "> ")
                        except EOFError:
                            break
                        if text.strip() == "/quit":
                            break
                        print(await chat.handle("local", text))
            finally:
                await llm.close()
    finally:
        store.close()


def main():
    parser = argparse.ArgumentParser(description="Headless Drone Parts Assistant")
    parser.add_argument("--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("init", "demo", "run", "health", "telegram-id"):
        command_parser = sub.add_parser(command)
        if command == "demo":
            command_parser.add_argument(
                "--telegram-chat",
                help="Send clearly labelled synthetic demo alerts to this allowlisted private chat.",
            )
    check = sub.add_parser("check")
    check.add_argument("--no-discovery", action="store_true")
    check.add_argument("--product", type=int)
    chat = sub.add_parser("chat")
    chat.add_argument("text", nargs="*")
    backup = sub.add_parser("backup")
    backup.add_argument("destination")
    serve = sub.add_parser("serve", help="Start the web API, scraper and notifications together")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument(
        "--demo", action="store_true", help="Local synthetic demo; no external requests/messages"
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # httpx debug logs can expose Telegram tokens embedded in request URLs.
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    try:
        if args.command == "serve":
            if args.demo and args.host not in {"127.0.0.1", "::1", "localhost"}:
                raise ValueError("Unauthenticated demo mode must bind to loopback only.")
            import uvicorn

            from .api import create_app

            uvicorn.run(
                create_app(demo=args.demo),
                host=args.host,
                port=args.port,
                workers=1,
                access_log=False,
            )
        else:
            asyncio.run(execute(args))
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        if type(exc) is ValueError:
            print(str(exc), file=sys.stderr)
        else:
            print(
                f"Operation failed ({type(exc).__name__}); check configuration and service connectivity.",
                file=sys.stderr,
            )
        raise SystemExit(1) from None
