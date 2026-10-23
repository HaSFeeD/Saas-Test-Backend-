"""Standalone worker process: `python worker.py`. Scale it independently of the API."""
import asyncio
import logging

from app.config import get_settings
from app.db import migrate
from app.main import build_context


async def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level.upper())
    ctx = await build_context(settings)
    await migrate(ctx.pool)
    ctx.worker.start()
    logging.getLogger("worker").info("worker started")
    try:
        await asyncio.Event().wait()  # run until cancelled
    finally:
        await ctx.worker.stop()
        await ctx.http.aclose()
        await ctx.pool.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
