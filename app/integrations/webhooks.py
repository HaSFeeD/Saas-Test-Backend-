from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

from .http import HttpClient


def sign_payload(secret: str, timestamp: int, body: bytes) -> str:
    """HMAC-SHA256 over '<timestamp>.<body>' — the scheme Stripe popularised, re-used for our own hooks."""
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return mac.hexdigest()


class WebhookSender:
    """
    Delivers signed outbound webhooks to a customer-supplied URL.

    Each request carries X-Webhook-Signature: t=<ts>,v1=<hmac> so the receiver can
    verify authenticity, exactly how we verify Stripe on the way in.
    """

    def __init__(self, http: HttpClient, user_agent: str) -> None:
        self._http = http
        self._user_agent = user_agent

    async def deliver(self, url: str, secret: str, event: str, data: dict[str, Any]) -> int:
        body = json.dumps({"event": event, "data": data}, separators=(",", ":")).encode()
        ts = int(time.time())
        headers = {
            "Content-Type": "application/json",
            "User-Agent": self._user_agent,
            "X-Webhook-Event": event,
            "X-Webhook-Signature": f"t={ts},v1={sign_payload(secret, ts, body)}",
        }
        resp = await self._http.request("POST", url, content=body, headers=headers)
        if resp.status_code >= 400:
            raise RuntimeError(f"webhook delivery to {url} returned {resp.status_code}")
        return resp.status_code
