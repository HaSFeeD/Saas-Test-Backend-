from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import Settings, get_settings
from .context import Context
from .db import create_pool, migrate
from .errors import register_error_handlers
from .integrations.http import HttpClient
from .integrations.mailer import ConsoleMailer, Mailer, ResendMailer
from .integrations.storage import LocalStorage, Storage
from .integrations.stripe import StripeClient
from .integrations.telegram import TelegramClient
from .integrations.webhooks import WebhookSender
from .jobs.handlers import register_jobs
from .jobs.queue import Worker
from .modules import admin, auth, billing, me, privacy, searches, telegram_webhook, uploads, ws
from .providers.registry import create_search_provider


async def build_context(settings: Settings, *, storage: Storage | None = None, mailer: Mailer | None = None) -> Context:
    pool = await create_pool(settings.database_url)
    http = HttpClient(
        timeout=settings.http_timeout_seconds,
        max_retries=settings.http_max_retries,
        proxy=settings.http_proxy_url,
        default_headers={"User-Agent": settings.webhook_user_agent},
    )
    if mailer is None:
        mailer = (
            ResendMailer(http, settings.resend_api_key, settings.mail_from)
            if settings.resend_api_key
            else ConsoleMailer()
        )
    stripe = (
        StripeClient(http, settings.stripe_secret_key, settings.stripe_webhook_secret)
        if settings.stripe_secret_key
        else None
    )
    telegram = (
        TelegramClient(http, settings.telegram_bot_token, settings.telegram_webhook_secret)
        if settings.telegram_bot_token
        else None
    )
    ctx = Context(
        settings=settings,
        pool=pool,
        http=http,
        mailer=mailer,
        storage=storage or LocalStorage(settings.storage_dir),
        search_provider=create_search_provider(settings),
        worker=Worker(pool),
        webhooks=WebhookSender(http, settings.webhook_user_agent),
        stripe=stripe,
        telegram=telegram,
    )
    register_jobs(ctx)
    return ctx


def create_app(settings: Settings | None = None, *, context: Context | None = None, run_worker: bool | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level.upper())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        ctx = context or await build_context(settings)
        app.state.ctx = ctx
        await migrate(ctx.pool)
        should_run = settings.run_worker if run_worker is None else run_worker
        if should_run:
            ctx.worker.start()
        try:
            yield
        finally:
            await ctx.worker.stop()
            if context is None:  # only tear down what we created here
                await ctx.http.aclose()
                await ctx.pool.close()

    app = FastAPI(title="SaaS Search API", version="1.0.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list or [],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_error_handlers(app)
    @app.middleware("http")
    async def _global_rate_limit(request, call_next):
        from fastapi.responses import JSONResponse

        from .deps import _limiter
        from .errors import AppError

        client = request.client.host if request.client else "unknown"
        try:
            _limiter.check(f"global:{client}", settings.rate_limit_per_min)
        except AppError as exc:
            return JSONResponse(status_code=exc.status_code, content={"error": {"code": exc.code, "message": exc.message}})
        return await call_next(request)


    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict:
        return {"ok": True}

    @app.get("/readyz", include_in_schema=False)
    async def readyz() -> dict:
        await app.state.ctx.pool.execute("SELECT 1")
        return {"ok": True}

    for module in (auth, me, billing, uploads, searches, privacy, admin, telegram_webhook):
        app.include_router(module.router, prefix="/v1")
    app.include_router(ws.router, prefix="/v1")
    return app

