from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ..context import Context
from ..deps import get_context
from ..errors import forbidden

router = APIRouter(prefix="/telegram", tags=["telegram"])


@router.post("/webhook")
async def telegram_webhook(request: Request, ctx: Context = Depends(get_context)) -> dict:
    """
    Inbound Telegram webhook. Telegram authenticates itself with the
    X-Telegram-Bot-Api-Secret-Token header configured via setWebhook.

    Replies to /start by echoing the chat id so a user can link their account.
    """
    if ctx.telegram is None:
        raise forbidden("Telegram is not configured")
    if not ctx.telegram.verify_webhook_secret(request.headers.get("x-telegram-bot-api-secret-token")):
        raise forbidden("Invalid secret token")

    update = await request.json()
    message = update.get("message") or {}
    chat = message.get("chat") or {}
    text = (message.get("text") or "").strip()
    chat_id = chat.get("id")

    if chat_id and text.startswith("/start"):
        await ctx.telegram.send_message(
            str(chat_id),
            f"Your chat id is <code>{chat_id}</code>. "
            "Paste it into the app (Settings → Notifications) to receive search alerts.",
        )
    return {"ok": True}
