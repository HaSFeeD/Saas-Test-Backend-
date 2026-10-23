from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, EmailStr, Field, model_validator

from ..context import Context
from ..deps import get_context, rate_limit
from ..errors import bad_request
from ..jobs.queue import enqueue
from ..pii import hash_identifier
from ..security import new_token, normalize_email, sha256

router = APIRouter(prefix="/privacy", tags=["privacy"])
_limit = Depends(rate_limit("privacy", "auth_rate_limit_per_min"))


class OptOutBody(BaseModel):
    kind: str = Field(pattern="^(email|phone|username)$")
    value: str = Field(min_length=1, max_length=254)
    contact_email: EmailStr = Field(alias="contactEmail")

    @model_validator(mode="after")
    def _email_self_only(self) -> OptOutBody:
        if self.kind == "email" and normalize_email(self.value) != normalize_email(self.contact_email):
            raise ValueError("To opt out an email address, the confirmation must be sent to that same address")
        return self


@router.post("/opt-out", status_code=202, dependencies=[_limit])
async def opt_out(body: OptOutBody, ctx: Context = Depends(get_context)) -> dict:
    token = new_token()
    async with ctx.pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            "INSERT INTO opt_outs (kind, value_hash, token_hash) VALUES ($1,$2,$3) "
            "ON CONFLICT (kind, value_hash) DO UPDATE SET token_hash = EXCLUDED.token_hash "
            "WHERE opt_outs.confirmed_at IS NULL RETURNING id",
            body.kind, hash_identifier(ctx.settings.pii_pepper, body.kind, body.value), sha256(token),
        )
        if row is not None:  # None => already confirmed, nothing to do
            await enqueue(
                conn,
                "send_email",
                {
                    "to": body.contact_email,
                    "subject": "Confirm your removal request",
                    "text": "Confirm that we should exclude this "
                    f"{body.kind} from all search results:\n"
                    f"{ctx.settings.public_url}/v1/privacy/opt-out/confirm?token={token}",
                },
            )
    return {"message": "If the request is valid, a confirmation email has been sent."}


@router.get("/opt-out/confirm", dependencies=[_limit])
async def confirm(token: str, ctx: Context = Depends(get_context)) -> dict:
    updated = await ctx.pool.execute(
        "UPDATE opt_outs SET confirmed_at = now(), token_hash = NULL WHERE token_hash = $1 AND confirmed_at IS NULL",
        sha256(token),
    )
    if updated.endswith(" 0"):
        raise bad_request("invalid_token", "This link is invalid or was already used")
    return {"message": "Done. This identifier is excluded from all future search results."}


_ = Response
