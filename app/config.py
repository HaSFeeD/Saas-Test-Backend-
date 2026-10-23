from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Validated application configuration, loaded from the environment (and .env)."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    env: str = "development"
    port: int = 8000
    log_level: str = "info"
    public_url: str = "http://localhost:8000"
    app_url: str = "http://localhost:5173"
    cors_origins: str = ""
    database_url: str = "postgres://postgres:postgres@localhost:5432/saas"
    jwt_secret: str = Field(min_length=32)
    run_worker: bool = True

    http_proxy_url: str | None = None
    http_timeout_seconds: float = 20.0
    http_max_retries: int = 4

    stripe_secret_key: str | None = None
    stripe_webhook_secret: str | None = None
    stripe_price_pack_1: str | None = None
    stripe_price_pack_5: str | None = None
    stripe_price_pack_15: str | None = None
    stripe_price_pro_monthly: str | None = None
    checkout_success_url: str = "http://localhost:5173/billing/success"
    checkout_cancel_url: str = "http://localhost:5173/billing/cancel"

    telegram_bot_token: str | None = None
    telegram_webhook_secret: str | None = None

    mail_from: str = "Support <no-reply@example.com>"
    resend_api_key: str | None = None

    webhook_user_agent: str = "saas-backend/1.0"

    free_searches_per_day: int = 3
    search_provider: str = "mock"
    storage_dir: str = "./storage"
    max_upload_mb: int = 8
    pii_pepper: str = Field(default="dev-only-pepper-change-me", min_length=16)
    rate_limit_per_min: int = 120
    auth_rate_limit_per_min: int = 10

    @field_validator("stripe_secret_key", "telegram_bot_token", "resend_api_key", "http_proxy_url", mode="before")
    @classmethod
    def _blank_to_none(cls, v: object) -> object:
        return None if v in ("", None) else v

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    def enforce_production(self) -> None:
        if not self.is_production:
            return
        if not self.stripe_secret_key:
            raise ValueError("STRIPE_SECRET_KEY is required in production")
        if self.pii_pepper == "dev-only-pepper-change-me":
            raise ValueError("PII_PEPPER must be set in production")
        if self.search_provider == "mock":
            raise ValueError("The mock search provider must not be used in production")


@lru_cache
def get_settings() -> Settings:
    settings = Settings()  # type: ignore[call-arg]
    settings.enforce_production()
    return settings
