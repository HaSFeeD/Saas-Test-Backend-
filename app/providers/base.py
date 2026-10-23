from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


@dataclass
class SearchInput:
    first_name: str
    last_name: str
    age: int | None = None
    city: str | None = None
    region: str | None = None
    country: str | None = None
    email: str | None = None
    phone: str | None = None
    username: str | None = None
    photo: bytes | None = None
    photo_mime: str | None = None


@dataclass
class ProviderMatch:
    platform: str
    profile_url: str
    display_name: str
    confidence: float  # 0..1
    username: str | None = None
    age: int | None = None
    location: str | None = None
    photo_url: str | None = None
    last_active_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class SearchProvider(Protocol):
    """
    Pluggable data source. A real implementation must come from a legally vetted,
    licensed data source (see PLAN.md, sections 7-8).
    """

    name: str

    async def search(self, query: SearchInput) -> list[ProviderMatch]: ...
