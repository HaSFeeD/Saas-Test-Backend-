from __future__ import annotations

from dataclasses import dataclass

import asyncpg

from .config import Settings
from .integrations.http import HttpClient
from .integrations.mailer import Mailer
from .integrations.storage import Storage
from .integrations.stripe import StripeClient
from .integrations.telegram import TelegramClient
from .integrations.webhooks import WebhookSender
from .jobs.queue import Worker
from .providers.base import SearchProvider


@dataclass
class Context:
    """Everything routes and jobs depend on. Built once at startup, overridable in parts."""

    settings: Settings
    pool: asyncpg.Pool
    http: HttpClient
    mailer: Mailer
    storage: Storage
    search_provider: SearchProvider
    worker: Worker
    webhooks: WebhookSender
    stripe: StripeClient | None = None
    telegram: TelegramClient | None = None

    @property
    def billing_provider_name(self) -> str:
        return "stripe" if self.stripe else "dev"
