"""Promote a user to admin: `python make_admin.py user@example.com`."""
import asyncio
import sys

from app.config import get_settings
from app.db import create_pool


async def main(email: str) -> None:
    pool = await create_pool(get_settings().database_url)
    result = await pool.execute(
        "UPDATE users SET role='admin' WHERE lower(email)=lower($1) AND deleted_at IS NULL", email
    )
    print(f"{email} is now an admin" if result.endswith(" 1") else f"no active user with email {email}")
    await pool.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python make_admin.py <email>")
    asyncio.run(main(sys.argv[1]))
