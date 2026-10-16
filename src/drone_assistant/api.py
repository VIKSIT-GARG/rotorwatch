from __future__ import annotations

import secrets
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .app_store import WEB_OWNER
from .config import Settings
from .openclaw import OpenClawError
from .service import Service

BearerCredentials = Annotated[
    HTTPAuthorizationCredentials | None, Depends(HTTPBearer(auto_error=False))
]


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChatInput(StrictInput):
    message: str = Field(min_length=1, max_length=1200)
    session_id: str = Field(
        default="workspace", min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$"
    )


class WatchInput(StrictInput):
    product_id: int | None = Field(default=None, gt=0)
    query: str | None = Field(default=None, min_length=1, max_length=500)


class ScanInput(StrictInput):
    product_id: int | None = Field(default=None, gt=0)


class PreferenceInput(StrictInput):
    telegram: bool | None = None
    whatsapp: bool | None = None
    stock: bool | None = None
    price: bool | None = None
    specifications: bool | None = None
    new_match: bool | None = None
    verification: bool | None = None


class PurchaseInput(StrictInput):
    product_id: int = Field(gt=0)
    quantity: int = Field(default=1, ge=1, le=20)


def create_app(
    settings: Settings | None = None,
    *,
    demo: bool = False,
    start_workers: bool = True,
    web_dir: Path | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    if not demo and len(settings.api_token) < 24:
        raise ValueError(
            "Set API_TOKEN to a random secret of at least 24 characters before starting the web API."
        )

    @asynccontextmanager
    async def lifespan(app):
        engine = Service(settings, demo=demo)
        app.state.engine = engine
        try:
            if start_workers:
                await engine.start()
            yield
        finally:
            await engine.close()

    app = FastAPI(
        title="RotorWatch API",
        version="0.3.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-RotorWatch-Token"],
        expose_headers=["ETag"],
        max_age=600,
    )

    async def authorize(
        request: Request,
        credentials: BearerCredentials,
    ):
        if demo:
            return
        token = request.headers.get("X-RotorWatch-Token")
        if token is None and credentials:
            token = credentials.credentials
        if not token or not secrets.compare_digest(token, settings.api_token):
            raise HTTPException(
                401,
                "Connect using your private workspace API token.",
                headers={"WWW-Authenticate": "Bearer"},
            )

    protected = APIRoutes(app, authorize)

    @app.middleware("http")
    async def headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/health")
    async def health():
        return {"ok": True, "mode": "demo" if demo else "live"}

    @protected.get("/api/state")
    async def state(request: Request, version: str | None = Query(default=None, max_length=40)):
        result = request.app.state.engine.state()
        if result["version"] == version:
            return Response(status_code=204)
        return result

    @protected.get("/api/products/{product_id}/history")
    async def history(request: Request, product_id: int):
        engine = request.app.state.engine
        if not engine.store.product(product_id):
            raise HTTPException(404, "Product not found.")
        return {"product_id": product_id, "observations": engine.store.history(product_id, 40)}

    @protected.post("/api/watchlist")
    async def watch(request: Request, body: WatchInput):
        if (body.product_id is None) == (body.query is None):
            raise HTTPException(422, "Supply one product_id or one query/product URL.")
        engine = request.app.state.engine
        if body.product_id is not None and not engine.store.product(body.product_id):
            raise HTTPException(404, "Product not found.")
        if body.query is not None:
            query = body.query.strip()
            if not query:
                raise HTTPException(422, "Enter a product name or URL.")
            if "://" in query or query.startswith(("www.", "http:")):
                try:
                    engine.registry.canonical(query, product=True)
                except ValueError as exc:
                    raise HTTPException(422, str(exc)) from None
            body.query = query
        text = "/watch " + (f"P{body.product_id}" if body.product_id else body.query)
        reply = await engine.commands.handle(WEB_OWNER, text)
        if not reply.startswith("Watching"):
            raise HTTPException(422, reply)
        return {"message": reply}

    @protected.delete("/api/watchlist/{product_id}")
    async def unwatch(request: Request, product_id: int):
        engine = request.app.state.engine
        if not engine.store.product(product_id):
            raise HTTPException(404, "Product not found.")
        engine.store.unwatch(WEB_OWNER, "P", product_id)
        return {"message": "Product removed from your web watchlist."}

    @protected.delete("/api/rules/{rule_id}")
    async def unwatch_rule(request: Request, rule_id: int):
        request.app.state.engine.store.unwatch(WEB_OWNER, "R", rule_id)
        return {"message": "Discovery rule removed."}

    @protected.post("/api/chat")
    async def chat(request: Request, body: ChatInput):
        return await request.app.state.engine.web_chat.ask(body.message.strip(), body.session_id)

    @protected.get("/api/chat")
    async def chat_history(
        request: Request,
        session_id: str = Query(default="workspace", pattern=r"^[a-zA-Z0-9_-]{1,80}$"),
    ):
        return {"messages": request.app.state.engine.store.messages(session_id)}

    @protected.post("/api/scans", status_code=202)
    async def scan(request: Request, body: ScanInput):
        engine = request.app.state.engine
        if engine.demo:
            await engine.advance_demo()
            return {
                "message": "Synthetic demo changes applied. No retailer requests or external messages were sent."
            }
        try:
            engine.queue_manual(body.product_id)
        except ValueError as exc:
            raise HTTPException(429, str(exc)) from None
        return {"message": "Check queued. The table updates when verified observations arrive."}

    @protected.patch("/api/preferences")
    async def preferences(request: Request, body: PreferenceInput):
        engine = request.app.state.engine
        values = body.model_dump(exclude_none=True)
        capabilities = engine.capabilities()
        for channel in ("telegram", "whatsapp"):
            if values.get(channel) and not capabilities[channel]:
                raise HTTPException(
                    409,
                    f"Configure {channel} credentials and its approved destination on the GCP backend first.",
                )
        engine.store.set_preferences(values)
        return {"preferences": engine.store.preferences()}

    @protected.post("/api/purchases", status_code=201)
    async def purchase(request: Request, body: PurchaseInput):
        engine = request.app.state.engine
        try:
            identifier = engine.store.purchase_request(body.product_id, body.quantity)
        except ValueError:
            raise HTTPException(404, "Product not found.") from None
        return {
            "id": identifier,
            "state": "browser_session_required",
            "message": "Saved to purchase review. Cart automation needs a connected retailer browser session. No cart was changed and no order was placed.",
        }

    @protected.post("/api/purchases/{identifier}/prepare-browser")
    async def prepare_browser(request: Request, identifier: int):
        engine = request.app.state.engine
        if engine.demo:
            raise HTTPException(409, "Demo mode never connects to a retailer browser.")
        try:
            return await engine.cart.prepare(identifier)
        except OpenClawError as exc:
            raise HTTPException(exc.code if 400 <= exc.code < 600 else 502, exc.message) from None

    @protected.post("/api/purchases/{identifier}/confirm-cart")
    async def confirm_cart(request: Request, identifier: int):
        engine = request.app.state.engine
        if engine.demo:
            raise HTTPException(409, "Demo mode cannot change a retailer cart.")
        try:
            return await engine.cart.confirm(identifier)
        except OpenClawError as exc:
            raise HTTPException(exc.code if 400 <= exc.code < 600 else 502, exc.message) from None

    @protected.post("/api/integrations/test-notifications", status_code=202)
    async def test_notifications(request: Request):
        engine = request.app.state.engine
        if engine.demo or not all(
            engine.notifications.channel_ready(c) for c in ("telegram", "whatsapp")
        ):
            raise HTTPException(
                409,
                "Configure both Telegram and WhatsApp destinations first. Demo mode sends nothing.",
            )
        from datetime import UTC, datetime

        last = engine.store.get_meta("last_notification_test")
        if last and (datetime.now(UTC) - datetime.fromisoformat(last)).total_seconds() < 30:
            raise HTTPException(429, "Wait 30 seconds between notification tests.")
        targets = [("telegram", t) for t in engine.settings.allowed_chat_ids] + [
            ("whatsapp", engine.settings.whatsapp_to)
        ]
        batch = uuid.uuid4().hex
        with engine.store.conn:
            for channel, target in targets:
                key = f"connection-test:{batch}:{channel}:{target}"
                engine.store._enqueue(
                    target,
                    "RotorWatch connection test. This is a test notification, not a stock alert or order confirmation.",
                    key,
                )
                engine.store.conn.execute(
                    "UPDATE outbox SET channel=? WHERE dedupe_key=?", (channel, key)
                )
        engine.store.set_meta("last_notification_test", datetime.now(UTC).isoformat())
        return {
            "message": "Test notifications queued for both Telegram and WhatsApp. See Message delivery for each result."
        }

    selected_web = web_dir or Path(__file__).resolve().parents[2] / "web"
    if not selected_web.is_dir():
        selected_web = Path(sys.prefix) / "share/drone-assistant/web"
    if selected_web.is_dir():
        app.mount("/", StaticFiles(directory=selected_web, html=True), name="dashboard")
    return app


class APIRoutes:
    def __init__(self, app: FastAPI, authorize):
        self.app, self.dependencies = app, [Depends(authorize)]

    def __getattr__(self, method):
        def route(path, **kwargs):
            return getattr(self.app, method)(path, dependencies=self.dependencies, **kwargs)

        return route
