from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, EmailStr, Field, field_validator

from ..context import Context
from ..deps import get_context, rate_limit
from ..errors import bad_request, conflict, unauthorized
from ..jobs.queue import enqueue
from ..security import hash_password, new_token, normalize_email, sha256, sign_access_token, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_TTL_DAYS = 30
RESET_TTL_MIN = 60

_auth_limit = Depends(rate_limit("auth", "auth_rate_limit_per_min"))


class RegisterBody(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    accept_terms: bool = Field(alias="acceptTerms")

    @field_validator("accept_terms")
    @classmethod
    def _must_accept(cls, v: bool) -> bool:
        if not v:
            raise ValueError("You must accept the Terms of Service")
        return v


class LoginBody(BaseModel):
    email: EmailStr
    password: str = Field(max_length=128)


class RefreshBody(BaseModel):
    refresh_token: str = Field(alias="refreshToken")


class ForgotBody(BaseModel):
    email: EmailStr


class ResetBody(BaseModel):
    token: str
    password: str = Field(min_length=8, max_length=128)


# Compared against when the e-mail is unknown, so login timing doesn't reveal which accounts exist.
_DUMMY_HASH = hash_password("dummy-password-for-timing")


async def _issue_tokens(conn: asyncpg.Connection, secret: str, user: dict) -> dict:
    refresh = new_token()
    await conn.execute(
        "INSERT INTO refresh_tokens (user_id, token_hash, expires_at) "
        "VALUES ($1, $2, now() + make_interval(days => $3))",
        user["id"], sha256(refresh), REFRESH_TTL_DAYS,
    )
    access = sign_access_token(secret, user["id"], user["role"])
    return {
        "accessToken": access,
        "refreshToken": refresh,
        "user": {"id": user["id"], "email": user["email"], "role": user["role"]},
    }


@router.post("/register", status_code=201, dependencies=[_auth_limit])
async def register(body: RegisterBody, ctx: Context = Depends(get_context)) -> dict:
    email = normalize_email(body.email)
    pw_hash = hash_password(body.password)
    async with ctx.pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            "INSERT INTO users (email, password_hash, tos_accepted_at) VALUES ($1,$2,now()) "
            "ON CONFLICT DO NOTHING RETURNING id::text, email, role",
            email, pw_hash,
        )
        if row is None:
            raise conflict("email_taken", "An account with this email already exists")
        return await _issue_tokens(conn, ctx.settings.jwt_secret, dict(row))


@router.post("/login", dependencies=[_auth_limit])
async def login(body: LoginBody, ctx: Context = Depends(get_context)) -> dict:
    row = await ctx.pool.fetchrow(
        "SELECT id::text, email, role, password_hash FROM users WHERE lower(email)=$1 AND deleted_at IS NULL",
        normalize_email(body.email),
    )
    stored = row["password_hash"] if row else _DUMMY_HASH
    if not verify_password(body.password, stored) or row is None:
        raise unauthorized("Invalid email or password")
    async with ctx.pool.acquire() as conn:
        return await _issue_tokens(conn, ctx.settings.jwt_secret, dict(row))


@router.post("/refresh", dependencies=[_auth_limit])
async def refresh(body: RefreshBody, ctx: Context = Depends(get_context)) -> dict:
    reuse = False
    result: dict = {}
    async with ctx.pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                "SELECT t.id, t.user_id::text, t.revoked_at, t.expires_at < now() AS expired, "
                "u.email, u.role, u.deleted_at "
                "FROM refresh_tokens t JOIN users u ON u.id = t.user_id "
                "WHERE t.token_hash = $1 FOR UPDATE OF t",
                sha256(body.refresh_token),
            )
            if row is None or row["expired"] or row["deleted_at"]:
                raise unauthorized("Invalid refresh token")
            if row["revoked_at"]:
                # A rotated token was replayed: assume theft, kill every session of this user.
                await conn.execute(
                    "UPDATE refresh_tokens SET revoked_at = now() WHERE user_id = $1 AND revoked_at IS NULL",
                    row["user_id"],
                )
                reuse = True
            else:
                await conn.execute("UPDATE refresh_tokens SET revoked_at = now() WHERE id = $1", row["id"])
                result = await _issue_tokens(
                    conn, ctx.settings.jwt_secret, {"id": row["user_id"], "email": row["email"], "role": row["role"]}
                )
    if reuse:  # raised after commit so the mass revocation is persisted
        raise unauthorized("Invalid refresh token")
    return result


@router.post("/logout", status_code=204)
async def logout(body: RefreshBody, ctx: Context = Depends(get_context)) -> Response:
    await ctx.pool.execute(
        "UPDATE refresh_tokens SET revoked_at = now() WHERE token_hash = $1 AND revoked_at IS NULL",
        sha256(body.refresh_token),
    )
    return Response(status_code=204)


@router.post("/forgot-password", status_code=202, dependencies=[_auth_limit])
async def forgot_password(body: ForgotBody, ctx: Context = Depends(get_context)) -> Response:
    row = await ctx.pool.fetchrow(
        "SELECT id::text, email FROM users WHERE lower(email)=$1 AND deleted_at IS NULL", normalize_email(body.email)
    )
    if row:
        token = new_token()
        async with ctx.pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "INSERT INTO password_resets (user_id, token_hash, expires_at) "
                "VALUES ($1, $2, now() + make_interval(mins => $3))",
                row["id"], sha256(token), RESET_TTL_MIN,
            )
            await enqueue(
                conn,
                "send_email",
                {
                    "to": row["email"],
                    "subject": "Reset your password",
                    "text": f"Reset your password: {ctx.settings.app_url}/reset-password?token={token}\n"
                    f"The link expires in {RESET_TTL_MIN} minutes.",
                },
            )
    # Same response either way: do not reveal which e-mails are registered.
    return Response(status_code=202)


@router.post("/reset-password", status_code=204, dependencies=[_auth_limit])
async def reset_password(body: ResetBody, ctx: Context = Depends(get_context)) -> Response:
    pw_hash = hash_password(body.password)
    async with ctx.pool.acquire() as conn, conn.transaction():
        user_id = await conn.fetchval(
            "UPDATE password_resets SET used_at = now() "
            "WHERE token_hash = $1 AND used_at IS NULL AND expires_at > now() RETURNING user_id::text",
            sha256(body.token),
        )
        if user_id is None:
            raise bad_request("invalid_token", "Reset link is invalid or expired")
        await conn.execute("UPDATE users SET password_hash = $2 WHERE id = $1", user_id, pw_hash)
        await conn.execute(
            "UPDATE refresh_tokens SET revoked_at = now() WHERE user_id = $1 AND revoked_at IS NULL", user_id
        )
    return Response(status_code=204)
