from __future__ import annotations

from ..config import Settings
from .base import SearchProvider
from .mock import MockProvider


def create_search_provider(settings: Settings) -> SearchProvider:
    if settings.search_provider == "mock":
        if settings.is_production:
            raise RuntimeError("The mock search provider must not be used in production")
        return MockProvider()
    raise RuntimeError(
        f'Unknown SEARCH_PROVIDER "{settings.search_provider}". '
        "Implement SearchProvider in app/providers and register it here."
    )
