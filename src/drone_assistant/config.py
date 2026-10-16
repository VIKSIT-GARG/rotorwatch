from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_dotenv(path: Path = Path(".env")) -> None:
    """Small literal .env loader. No evaluation, interpolation, or shell execution."""
    if not path.is_file():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if value[:1] in ("'", '"') and value[-1:] == value[:1]:
            value = value[1:-1]
        if key.replace("_", "").isalnum():
            os.environ.setdefault(key, value)


@dataclass(frozen=True)
class Settings:
    database: str = "data/assistant.sqlite3"
    interval_seconds: int = 86400
    source_delay: float = 3.0
    request_timeout: float = 25.0
    browser_enabled: bool = False
    headless: bool = True
    proxy_resolves_dns: bool = False
    max_response_bytes: int = 3_000_000
    max_discovery_pages: int = 2
    max_products_per_listing: int = 30
    max_products: int = 500
    telegram_token: str = ""
    allowed_chat_ids: tuple[str, ...] = ()
    llm_api_key: str = ""
    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_model: str = "openai/gpt-4.1-nano"
    llm_daily_limit: int = 60
    user_agent: str = "DronePartsAssistant/0.3 (stock monitoring; respects robots.txt)"
    sources_file: str = ""
    api_token: str = ""
    cors_origins: tuple[str, ...] = ()
    whatsapp_token: str = ""
    whatsapp_phone_id: str = ""
    whatsapp_to: str = ""
    whatsapp_template: str = ""
    whatsapp_language: str = "en_US"
    whatsapp_graph_version: str = ""
    notification_transport: str = "direct"
    openclaw_url: str = "http://127.0.0.1:18789"
    openclaw_token: str = ""
    openclaw_telegram_account: str = "default"
    openclaw_whatsapp_account: str = "default"
    openclaw_browser_profile: str = "openclaw"

    @classmethod
    def from_env(cls) -> Settings:
        load_dotenv()
        settings = cls(
            database=os.getenv("DATABASE_PATH", "data/assistant.sqlite3"),
            interval_seconds=int(os.getenv("CHECK_INTERVAL_SECONDS", "86400")),
            source_delay=float(os.getenv("SOURCE_DELAY_SECONDS", "3")),
            request_timeout=float(os.getenv("REQUEST_TIMEOUT_SECONDS", "25")),
            browser_enabled=os.getenv("BROWSER_ENABLED", "false").lower() == "true",
            proxy_resolves_dns=os.getenv("PROXY_RESOLVES_DNS", "false").lower() == "true",
            telegram_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            allowed_chat_ids=tuple(
                x.strip()
                for x in os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", "").split(",")
                if x.strip()
            ),
            llm_api_key=os.getenv("LLM_API_KEY", ""),
            llm_base_url=os.getenv("LLM_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/"),
            llm_model=os.getenv("LLM_MODEL", "openai/gpt-4.1-nano"),
            llm_daily_limit=int(os.getenv("LLM_DAILY_CALL_LIMIT", "60")),
            max_discovery_pages=int(os.getenv("MAX_DISCOVERY_PAGES", "2")),
            max_products=int(os.getenv("MAX_PRODUCTS", "500")),
            sources_file=os.getenv("SOURCES_FILE", ""),
            api_token=os.getenv("API_TOKEN", ""),
            cors_origins=tuple(
                x.strip().rstrip("/") for x in os.getenv("CORS_ORIGINS", "").split(",") if x.strip()
            ),
            whatsapp_token=os.getenv("WHATSAPP_ACCESS_TOKEN", ""),
            whatsapp_phone_id=os.getenv("WHATSAPP_PHONE_NUMBER_ID", ""),
            whatsapp_to=os.getenv("WHATSAPP_TO", ""),
            whatsapp_template=os.getenv("WHATSAPP_TEMPLATE_NAME", ""),
            whatsapp_language=os.getenv("WHATSAPP_TEMPLATE_LANGUAGE", "en_US"),
            whatsapp_graph_version=os.getenv("WHATSAPP_GRAPH_VERSION", ""),
            notification_transport=os.getenv("NOTIFICATION_TRANSPORT", "direct"),
            openclaw_url=os.getenv("OPENCLAW_URL", "http://127.0.0.1:18789").rstrip("/"),
            openclaw_token=os.getenv("OPENCLAW_GATEWAY_TOKEN", ""),
            openclaw_telegram_account=os.getenv("OPENCLAW_TELEGRAM_ACCOUNT", "default"),
            openclaw_whatsapp_account=os.getenv("OPENCLAW_WHATSAPP_ACCOUNT", "default"),
            openclaw_browser_profile=os.getenv("OPENCLAW_BROWSER_PROFILE", "openclaw"),
        )
        if settings.interval_seconds < 60 or settings.source_delay < 0:
            raise ValueError("Use CHECK_INTERVAL_SECONDS >= 60 and SOURCE_DELAY_SECONDS >= 0.")
        if not 1 <= settings.max_discovery_pages <= 10 or not 1 <= settings.max_products <= 10000:
            raise ValueError("Discovery pages must be 1..10; MAX_PRODUCTS must be 1..10000.")
        if settings.llm_daily_limit < 0:
            raise ValueError("LLM_DAILY_CALL_LIMIT cannot be negative.")
        if settings.notification_transport not in {"direct", "openclaw"}:
            raise ValueError("NOTIFICATION_TRANSPORT must be direct or openclaw.")
        return settings
