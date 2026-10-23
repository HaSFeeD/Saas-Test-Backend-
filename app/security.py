from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time

import jwt

ACCESS_TOKEN_TTL_SECONDS = 15 * 60

# scrypt parameters (OWASP-recommended baseline).
_N, _R, _P, _MAXMEM = 2**15, 8, 1, 64 * 1024 * 1024


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    key = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=64, maxmem=_MAXMEM)
    return f"scrypt${base64.b64encode(salt).decode()}${base64.b64encode(key).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        alg, salt_b64, key_b64 = stored.split("$")
    except ValueError:
        return False
    if alg != "scrypt":
        return False
    expected = base64.b64decode(key_b64)
    actual = hashlib.scrypt(
        password.encode(), salt=base64.b64decode(salt_b64), n=_N, r=_R, p=_P, dklen=len(expected), maxmem=_MAXMEM
    )
    return hmac.compare_digest(actual, expected)


def new_token() -> str:
    """Random opaque token. Only its sha256 hash is ever stored."""
    return secrets.token_urlsafe(32)


def sha256(data: str | bytes) -> str:
    if isinstance(data, str):
        data = data.encode()
    return hashlib.sha256(data).hexdigest()


def normalize_email(email: str) -> str:
    return email.strip().lower()


def sign_access_token(secret: str, user_id: str, role: str) -> str:
    now = int(time.time())
    payload = {"sub": user_id, "role": role, "iat": now, "exp": now + ACCESS_TOKEN_TTL_SECONDS}
    return jwt.encode(payload, secret, algorithm="HS256")


def verify_access_token(secret: str, token: str) -> dict:
    return jwt.decode(token, secret, algorithms=["HS256"])
