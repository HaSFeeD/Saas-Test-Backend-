from __future__ import annotations

import asyncio
import logging
import random

import httpx

log = logging.getLogger("http")


class HttpError(Exception):
    """A third-party HTTP call that still failed after all retries."""

    def __init__(self, message: str, *, status: int | None = None, body: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.body = body


class HttpClient:
    """
    Thin wrapper around httpx.AsyncClient for talking to third-party APIs.

    Adds the things every real integration needs:
      * a single pooled client with a sane timeout,
      * optional egress proxy (HTTP_PROXY_URL) applied to every outbound call,
      * automatic retries with exponential backoff + jitter on 429 / 5xx / network errors,
      * honouring the Retry-After header on 429 responses (rate limiting).
    """

    #: Status codes worth retrying. 429 = rate limited, 5xx = transient server error.
    RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

    def __init__(
        self,
        *,
        timeout: float = 20.0,
        max_retries: int = 4,
        proxy: str | None = None,
        default_headers: dict[str, str] | None = None,
    ) -> None:
        self._max_retries = max_retries
        self._client = httpx.AsyncClient(
            timeout=timeout,
            proxy=proxy,
            headers=default_headers or {},
            follow_redirects=True,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
        attempt = 0
        while True:
            attempt += 1
            try:
                resp = await self._client.request(method, url, **kwargs)  # type: ignore[arg-type]
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                if attempt > self._max_retries:
                    raise HttpError(f"{method} {url} failed: {exc}") from exc
                await self._sleep_backoff(attempt)
                continue

            if resp.status_code in self.RETRY_STATUSES and attempt <= self._max_retries:
                delay = self._retry_after(resp) or self._backoff_delay(attempt)
                log.warning("%s %s -> %s, retry %d in %.1fs", method, url, resp.status_code, attempt, delay)
                await asyncio.sleep(delay)
                continue
            return resp

    async def request_json(self, method: str, url: str, **kwargs: object) -> dict:
        resp = await self.request(method, url, **kwargs)
        if resp.status_code >= 400:
            raise HttpError(
                f"{method} {url} -> {resp.status_code}", status=resp.status_code, body=resp.text[:2000]
            )
        return resp.json() if resp.content else {}

    @staticmethod
    def _retry_after(resp: httpx.Response) -> float | None:
        value = resp.headers.get("retry-after")
        if not value:
            return None
        try:
            return min(60.0, float(value))
        except ValueError:
            return None

    @staticmethod
    def _backoff_delay(attempt: int) -> float:
        # Exponential backoff (0.5, 1, 2, 4 ...) capped at 30s, with full jitter.
        return random.uniform(0, min(30.0, 0.5 * 2 ** (attempt - 1)))

    async def _sleep_backoff(self, attempt: int) -> None:
        await asyncio.sleep(self._backoff_delay(attempt))
