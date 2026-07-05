from functools import lru_cache
from pathlib import Path
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "local"
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    app_secret_key: str = "change_me"
    admin_username: str = "admin"
    admin_password: str = "change_me"
    admin_auth_enabled: bool = True

    database_url: str = "sqlite:///./data/app.db"

    telegram_api_id: int | None = None
    telegram_api_hash: str | None = None
    telegram_session_path: str = "./data/telegram_session/user.session"
    source_provider: str = "telethon"
    telegram_bot_token: str | None = None

    # Proxy for Telethon: type = socks5 | http | mtproxy | "" (disabled)
    telegram_proxy_type: str = ""
    telegram_proxy_host: str = ""
    telegram_proxy_port: int = 0
    telegram_proxy_username: str = ""
    telegram_proxy_password: str = ""
    telegram_proxy_secret: str = ""  # MTProxy only

    timeweb_ai_gateway_api_key: str | None = None
    timeweb_ai_gateway_base_url: str | None = None
    timeweb_ai_gateway_model: str | None = None
    ai_temperature: float = 0.4
    ai_max_tokens: int = 2400
    ai_timeout_seconds: int = 60

    fetch_interval_seconds: int = 120
    default_lookback_limit: int = 50
    max_media_mb: int = 50

    auto_publish_enabled: bool = False
    default_post_mode: str = "manual"

    @property
    def media_root(self) -> Path:
        return Path("data/media")

    @field_validator("telegram_api_id", mode="before")
    @classmethod
    def parse_optional_int(cls, value):
        if value in ("", None):
            return None
        return value


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if s.app_env != "local":
        import sys
        if s.app_secret_key == "change_me":
            sys.exit("FATAL: APP_SECRET_KEY не задан или равен дефолтному значению. Установите его в .env")
        if s.admin_password == "change_me":
            sys.exit("FATAL: ADMIN_PASSWORD не задан или равен дефолтному значению. Установите его в .env")
        if s.admin_username == "admin":
            sys.exit("FATAL: ADMIN_USERNAME не задан или равен дефолтному значению. Установите его в .env")
    return s
