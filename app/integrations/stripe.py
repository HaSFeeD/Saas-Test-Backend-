from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any

from ..errors import bad_request
from .http import HttpClient

STRIPE_API = "https://api.stripe.com/v1"


class StripeClient:
    """
    Minimal Stripe integration over the raw REST API (no SDK).

    Demonstrates working with a third-party REST API directly: bearer auth,
    form-encoded bodies, nested bracket params, and HMAC webhook verification.
    """

    def __init__(self, http: HttpClient, secret_key: str, webhook_secret: str | None) -> None:
        self._http = http
        self._auth = {"Authorization": f"Bearer {secret_key}"}
        self._webhook_secret = webhook_secret

    async def _post(self, path: str, data: dict[str, Any]) -> dict:
        return await self._http.request_json("POST", f"{STRIPE_API}{path}", data=data, headers=self._auth)

    async def create_customer(self, email: str, user_id: str) -> str:
        res = await self._post("/customers", {"email": email, "metadata[user_id]": user_id})
        return res["id"]

    async def create_checkout_session(
        self, *, customer_id: str, price_id: str, mode: str, metadata: dict[str, str], success_url: str, cancel_url: str
    ) -> str:
        data: dict[str, Any] = {
            "mode": mode,
            "customer": customer_id,
            "line_items[0][price]": price_id,
            "line_items[0][quantity]": 1,
            "success_url": success_url,
            "cancel_url": cancel_url,
        }
        for key, value in metadata.items():
            data[f"metadata[{key}]"] = value
            if mode == "subscription":
                data[f"subscription_data[metadata][{key}]"] = value
        res = await self._post("/checkout/sessions", data)
        return res["url"]

    async def create_billing_portal_session(self, customer_id: str, return_url: str) -> str:
        res = await self._post("/billing_portal/sessions", {"customer": customer_id, "return_url": return_url})
        return res["url"]

    async def list_active_subscriptions(self, customer_id: str) -> list[str]:
        res = await self._http.request_json(
            "GET", f"{STRIPE_API}/subscriptions", params={"customer": customer_id, "status": "active"}, headers=self._auth
        )
        return [s["id"] for s in res.get("data", [])]

    async def cancel_subscription(self, subscription_id: str) -> None:
        await self._http.request_json("DELETE", f"{STRIPE_API}/subscriptions/{subscription_id}", headers=self._auth)

    async def cancel_customer_subscriptions(self, customer_id: str) -> None:
        for sub_id in await self.list_active_subscriptions(customer_id):
            await self.cancel_subscription(sub_id)

    def verify_webhook(self, payload: bytes, signature_header: str | None, tolerance: int = 300) -> dict:
        """
        Verify a Stripe webhook signature exactly like the SDK does, by hand:
        the header carries t=<ts> and one or more v1=<hex hmac> of "<ts>.<payload>".
        """
        if not signature_header or not self._webhook_secret:
            raise bad_request("bad_signature", "Missing webhook signature")

        parts = dict(p.split("=", 1) for p in signature_header.split(",") if "=" in p)
        timestamp, provided = parts.get("t"), parts.get("v1")
        if not timestamp or not provided:
            raise bad_request("bad_signature", "Malformed webhook signature")

        signed = f"{timestamp}.{payload.decode()}".encode()
        expected = hmac.new(self._webhook_secret.encode(), signed, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, provided):
            raise bad_request("bad_signature", "Invalid webhook signature")
        if abs(time.time() - int(timestamp)) > tolerance:
            raise bad_request("bad_signature", "Webhook timestamp outside tolerance")

        import json

        return json.loads(payload)
