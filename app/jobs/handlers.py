from __future__ import annotations

from typing import Any

from ..context import Context
from ..integrations.mailer import Mail
from ..jobs.queue import JobContext, enqueue
from ..search_service import run_search_job


def register_jobs(ctx: Context) -> None:
    worker = ctx.worker

    async def send_email(payload: dict[str, Any], _: JobContext) -> None:
        await ctx.mailer.send(Mail(to=payload["to"], subject=payload["subject"], text=payload["text"]))

    async def run_search(payload: dict[str, Any], job: JobContext) -> None:
        search_id = payload["searchId"]
        await run_search_job(ctx, search_id, job.is_last_attempt)
        await _notify_search_done(ctx, search_id)

    async def deliver_webhook(payload: dict[str, Any], _: JobContext) -> None:
        await ctx.webhooks.deliver(payload["url"], payload["secret"], payload["event"], payload["data"])

    async def notify_telegram(payload: dict[str, Any], _: JobContext) -> None:
        if ctx.telegram is not None:
            await ctx.telegram.send_message(payload["chatId"], payload["text"])

    worker.register("send_email", send_email)
    worker.register("run_search", run_search)
    worker.register("deliver_webhook", deliver_webhook)
    worker.register("notify_telegram", notify_telegram)


async def _notify_search_done(ctx: Context, search_id: str) -> None:
    """Fan out completion notifications over the channels the user has configured."""
    row = await ctx.pool.fetchrow(
        "SELECT s.status, s.result_count, u.telegram_chat_id, u.webhook_url, u.webhook_secret "
        "FROM searches s JOIN users u ON u.id = s.user_id WHERE s.id = $1",
        search_id,
    )
    if row is None or row["status"] != "done":
        return

    event_data = {"searchId": search_id, "status": row["status"], "resultCount": row["result_count"]}
    async with ctx.pool.acquire() as conn, conn.transaction():
        if row["webhook_url"] and row["webhook_secret"]:
            await enqueue(
                conn,
                "deliver_webhook",
                {
                    "url": row["webhook_url"],
                    "secret": row["webhook_secret"],
                    "event": "search.completed",
                    "data": event_data,
                },
            )
        if row["telegram_chat_id"]:
            await enqueue(
                conn,
                "notify_telegram",
                {
                    "chatId": row["telegram_chat_id"],
                    "text": f"🔎 Your search is ready: {row['result_count']} match(es) found.",
                },
            )
