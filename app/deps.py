from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass

import jwt
from fastapi import Depends, Request

from .context import Context
from .errors import forbidden, too_many, unauthorized
from .security import verify_access_token


def get_context(request: Request) -> Context:
    return request.app.state.ctx


@dataclass
class AuthUser:
    id: str
    email: str
    role: str


async def _load_user(ctx: Context, token: str) -> AuthUser:
    try:
        payload = verify_access_token(ctx.settings.jwt_secret, token)
    except jwt.PyJWTError as exc:
        raise unauthorized("Invalid or expired token") from exc
    # Re-read the user so deleted/demoted accounts are cut off immediately, not at token expiry.
    row = await ctx.pool.fetchrow(
        "SELECT id::text, email, role FROM users WHERE id = $1 AND deleted_at IS NULL", payload["sub"]
    )
    if row is None:
        raise unauthorized("Invalid or expired token")
    return AuthUser(id=row["id"], email=row["email"], role=row["role"])


async def require_user(request: Request, ctx: Context = Depends(get_context)) -> AuthUser:
    header = request.headers.get("authorization", "")
    if not header.startswith("Bearer "):
        raise unauthorized()
    return await _load_user(ctx, header[7:])


async def require_admin(user: AuthUser = Depends(require_user)) -> AuthUser:
    if user.role != "admin":
        raise forbidden()
    return user


class RateLimiter:
    """
    Simple in-process sliding-window limiter. Keys requests by client IP + scope.

    For a single instance this is enough; behind several instances use a shared store
    (e.g. Redis) with the same window logic.
    """

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str, limit: int, window: float = 60.0) -> None:
        now = time.monotonic()
        bucket = self._hits[key]
        while bucket and bucket[0] <= now - window:
            bucket.popleft()
        if len(bucket) >= limit:
            raise too_many("Too many requests, slow down")
        bucket.append(now)


_limiter = RateLimiter()


def rate_limit(scope: str, per_minute_attr: str):
    """Dependency factory. `per_minute_attr` names the Settings field holding the limit."""

    async def dependency(request: Request, ctx: Context = Depends(get_context)) -> None:
        limit = getattr(ctx.settings, per_minute_attr)
        client = request.client.host if request.client else "unknown"
        _limiter.check(f"{scope}:{client}", limit)

    return dependency
