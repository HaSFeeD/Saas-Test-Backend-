from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import asyncpg

log = logging.getLogger("worker")

#: A running job is considered abandoned after this long and returned to the queue.
STALE_AFTER_SECONDS = 300

Handler = Callable[[dict[str, Any], "JobContext"], Awaitable[None]]


@dataclass
class JobContext:
    attempt: int
    is_last_attempt: bool


async def enqueue(
    conn: asyncpg.Connection, type_: str, payload: dict[str, Any] | None = None, *, max_attempts: int = 5
) -> None:
    await conn.execute(
        "INSERT INTO jobs (type, payload, max_attempts) VALUES ($1, $2, $3)",
        type_, payload or {}, max_attempts,
    )


class Worker:
    """
    Postgres-backed job runner: claims jobs with FOR UPDATE SKIP LOCKED and retries
    failures with exponential backoff. No Redis or external broker required.
    """

    def __init__(self, pool: asyncpg.Pool, poll_interval: float = 1.0) -> None:
        self._pool = pool
        self._poll = poll_interval
        self._handlers: dict[str, Handler] = {}
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    def register(self, type_: str, handler: Handler) -> Worker:
        self._handlers[type_] = handler
        return self

    async def run_once(self) -> bool:
        """Claim and run one due job. Returns False if the queue is empty."""
        await self._pool.execute(
            "UPDATE jobs SET status='queued' "
            "WHERE status='running' AND locked_at < now() - make_interval(secs => $1)",
            STALE_AFTER_SECONDS,
        )
        row = await self._pool.fetchrow(
            "UPDATE jobs SET status='running', locked_at=now(), attempts=attempts+1 "
            "WHERE id = (SELECT id FROM jobs WHERE status='queued' AND run_at <= now() "
            "            ORDER BY run_at, id FOR UPDATE SKIP LOCKED LIMIT 1) "
            "RETURNING id, type, payload, attempts, max_attempts"
        )
        if row is None:
            return False

        job_id, type_, payload = row["id"], row["type"], row["payload"]
        attempts, max_attempts = row["attempts"], row["max_attempts"]
        handler = self._handlers.get(type_)
        try:
            if handler is None:
                raise RuntimeError(f'no handler for job type "{type_}"')
            await handler(payload, JobContext(attempt=attempts, is_last_attempt=attempts >= max_attempts))
            # Payloads may carry one-time tokens (reset mails): scrub on success.
            await self._pool.execute("UPDATE jobs SET status='done', payload='{}', locked_at=NULL WHERE id=$1", job_id)
        except Exception as exc:  # noqa: BLE001 - the queue is the top-level boundary
            msg = str(exc)
            log.error("job %s (%s) attempt %d failed: %s", job_id, type_, attempts, msg)
            if attempts >= max_attempts:
                await self._pool.execute(
                    "UPDATE jobs SET status='failed', last_error=$2, locked_at=NULL WHERE id=$1", job_id, msg
                )
            else:
                delay = min(3600, 5 * 2 ** (attempts - 1))
                await self._pool.execute(
                    "UPDATE jobs SET status='queued', last_error=$2, locked_at=NULL, "
                    "run_at = now() + make_interval(secs => $3) WHERE id=$1",
                    job_id, msg, delay,
                )
        return True

    async def drain(self, maximum: int = 100) -> int:
        count = 0
        while count < maximum and await self.run_once():
            count += 1
        return count

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.drain(20)
            except Exception:
                log.exception("worker tick failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._poll)
            except TimeoutError:
                pass

    def start(self) -> None:
        if self._task is None:
            self._stop.clear()
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
            self._task = None
