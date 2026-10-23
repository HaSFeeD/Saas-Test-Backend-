from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

from .base import ProviderMatch, SearchInput

PLATFORMS = ["Tinder", "Bumble", "Hinge", "Badoo", "OkCupid", "Plenty of Fish"]


class MockProvider:
    """
    Deterministic fake data source for development and demos. The same input always
    yields the same output and no real person's data is produced.

    Convention for demos/local checks:
      * a first name starting with "Nomatch" returns nothing,
      * a first name starting with "Boom" makes the provider raise (to exercise retries).
    """

    name = "mock"

    async def search(self, query: SearchInput) -> list[ProviderMatch]:
        if query.first_name.lower().startswith("boom"):
            raise RuntimeError("mock provider failure")
        if query.first_name.lower().startswith("nomatch"):
            return []

        seed = hashlib.sha256(
            f"{query.first_name}|{query.last_name}|{query.city or ''}".lower().encode()
        ).digest()
        count = 1 + seed[0] % 4
        matches: list[ProviderMatch] = []
        for i in range(count):
            platform = PLATFORMS[seed[i + 1] % len(PLATFORMS)]
            handle = f"{query.first_name}{seed[i + 8] % 100}".lower()
            matches.append(
                ProviderMatch(
                    platform=platform,
                    profile_url=f"https://example.com/mock/{platform.lower().replace(' ', '-')}/{handle}",
                    display_name=f"{query.first_name} {query.last_name[:1]}.",
                    username=handle,
                    age=query.age or 20 + seed[i + 16] % 25,
                    location=query.city or "Unknown",
                    photo_url=f"https://picsum.photos/seed/{seed.hex()[i * 4 : i * 4 + 8]}/400/400",
                    confidence=round(0.55 + (seed[i + 20] % 45) / 100, 2),
                    last_active_at=datetime.now(UTC) - timedelta(days=seed[i + 24] % 30),
                    raw={"mock": True},
                )
            )
        matches.sort(key=lambda m: m.confidence, reverse=True)
        return matches
