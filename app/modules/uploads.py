from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Response, UploadFile
from fastapi.responses import StreamingResponse

from ..context import Context
from ..deps import AuthUser, get_context, require_user
from ..errors import bad_request, not_found
from ..security import sha256

router = APIRouter(prefix="/uploads", tags=["uploads"])


def sniff_image(buf: bytes) -> tuple[str, str] | None:
    """Trust file signatures, not the client-supplied Content-Type."""
    if buf[:3] == b"\xff\xd8\xff":
        return "image/jpeg", "jpg"
    if buf[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png", "png"
    if buf[:4] == b"RIFF" and buf[8:12] == b"WEBP":
        return "image/webp", "webp"
    return None


@router.post("", status_code=201)
async def upload(
    file: UploadFile, ctx: Context = Depends(get_context), user: AuthUser = Depends(require_user)
) -> dict:
    data = await file.read(ctx.settings.max_upload_mb * 1024 * 1024 + 1)
    if len(data) > ctx.settings.max_upload_mb * 1024 * 1024:
        raise bad_request("file_too_large", f"Max upload size is {ctx.settings.max_upload_mb} MB")
    kind = sniff_image(data)
    if kind is None:
        raise bad_request("unsupported_type", "Only JPEG, PNG and WebP images are accepted")
    mime, ext = kind

    key = f"{user.id}/{uuid.uuid4()}.{ext}"
    await ctx.storage.put(key, data)
    row = await ctx.pool.fetchrow(
        "INSERT INTO uploads (user_id, storage_key, mime, size, sha256) VALUES ($1,$2,$3,$4,$5) "
        "RETURNING id::text, mime, size, created_at AS \"createdAt\"",
        user.id, key, mime, len(data), sha256(data),
    )
    return dict(row)


@router.get("/{upload_id}")
async def get_upload(
    upload_id: str, ctx: Context = Depends(get_context), user: AuthUser = Depends(require_user)
) -> Response:
    row = await ctx.pool.fetchrow(
        "SELECT storage_key, mime FROM uploads WHERE id=$1 AND user_id=$2", upload_id, user.id
    )
    if row is None:
        raise not_found()
    data = await ctx.storage.get(row["storage_key"])
    return Response(content=data, media_type=row["mime"], headers={"Cache-Control": "private, max-age=300"})


_ = StreamingResponse  # reserved for future large-file streaming
