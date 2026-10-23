from __future__ import annotations

import logging

from .http import HttpClient, HttpError

log = logging.getLogger("telegram")


class TelegramClient:
    """
    Telegram Bot API integration: outbound notifications and inbound webhook setup.

    An example of a messenger-platform API: token in the URL path, JSON bodies,
    and a secret-token header to authenticate inbound webhook calls.
    """

    def __init__(self, http: HttpClient, bot_token: str, webhook_secret: str | None = None) -> None:
        self._http = http
        self._base = f"https://api.telegram.org/bot{bot_token}"
        self._webhook_secret = webhook_secret

    async def send_message(self, chat_id: str, text: str) -> None:
        try:
            await self._http.request_json(
                "POST",
                f"{self._base}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
            )
        except HttpError as exc:
            # A blocked bot or a stale chat_id must not fail the business operation.
            log.warning("telegram sendMessage failed for chat %s: %s", chat_id, exc)

    async def set_webhook(self, url: str) -> dict:
        body: dict[str, object] = {"url": url, "allowed_updates": ["message"]}
        if self._webhook_secret:
            body["secret_token"] = self._webhook_secret
        return await self._http.request_json("POST", f"{self._base}/setWebhook", json=body)

    def verify_webhook_secret(self, header_value: str | None) -> bool:
        if not self._webhook_secret:
            return True
        return header_value == self._webhook_secret
