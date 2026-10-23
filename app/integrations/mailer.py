from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .http import HttpClient

log = logging.getLogger("mail")


@dataclass
class Mail:
    to: str
    subject: str
    text: str


class Mailer:
    async def send(self, mail: Mail) -> None:  # pragma: no cover - interface
        raise NotImplementedError


@dataclass
class ConsoleMailer(Mailer):
    """Logs mail instead of sending it. The outbox lets dev tooling read what was 'sent'."""

    outbox: list[Mail] = field(default_factory=list)

    async def send(self, mail: Mail) -> None:
        self.outbox.append(mail)
        log.info("mail (console) to=%s subject=%s\n%s", mail.to, mail.subject, mail.text)


class ResendMailer(Mailer):
    def __init__(self, http: HttpClient, api_key: str, sender: str) -> None:
        self._http = http
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._from = sender

    async def send(self, mail: Mail) -> None:
        await self._http.request_json(
            "POST",
            "https://api.resend.com/emails",
            headers=self._headers,
            json={"from": self._from, "to": mail.to, "subject": mail.subject, "text": mail.text},
        )
