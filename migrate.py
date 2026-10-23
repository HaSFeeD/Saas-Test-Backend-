"""Apply database migrations: `python migrate.py`."""
import asyncio

from app.config import get_settings
from app.db import create_pool, migrate


async def main() -> None:
    pool = await create_pool(get_settings().database_url)
    applied = await migrate(pool)
    print("applied: " + ", ".join(applied) if applied else "database is up to date")
    await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
