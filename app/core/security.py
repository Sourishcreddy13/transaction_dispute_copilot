from __future__ import annotations

import base64
import hashlib
import hmac
import json
import threading
import time
import uuid

from app.models import AccessContext, Principal


_REPLAY_LOCK = threading.Lock()
_USED_NONCES: dict[str, float] = {}
_MAX_REPLAY_CACHE = 10_000


def _enc(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _dec(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * ((4 - len(value) % 4) % 4))


def mint_context(
    principal: Principal,
    case_id: str,
    customer_id: str,
    permissions: list[str],
    secret: str,
    ttl: int = 60,
    audience: str = "mcp",
) -> str:
    if not secret or len(secret) < 32:
        raise ValueError("ACCESS_SECRET_TOO_WEAK")
    if ttl <= 0 or ttl > 300:
        raise ValueError("ACCESS_CONTEXT_TTL_INVALID")
    ctx = AccessContext(
        context_id="CTX-" + uuid.uuid4().hex,
        case_id=case_id,
        customer_id=customer_id,
        actor_id=principal.actor_id,
        role=principal.role,
        permissions=sorted(set(permissions)),
        expires_at=time.time() + ttl,
        audience=audience,
        nonce=uuid.uuid4().hex,
    )
    raw = ctx.model_dump_json().encode()
    signature = hmac.new(secret.encode(), raw, hashlib.sha256).digest()
    return _enc(raw) + "." + _enc(signature)


def _check_and_record_nonce(nonce: str, expires_at: float) -> bool:
    now = time.time()
    with _REPLAY_LOCK:
        expired = [key for key, expiry in _USED_NONCES.items() if expiry <= now]
        for key in expired:
            _USED_NONCES.pop(key, None)
        if nonce in _USED_NONCES:
            return False
        if len(_USED_NONCES) >= _MAX_REPLAY_CACHE:
            oldest = min(_USED_NONCES, key=_USED_NONCES.get)
            _USED_NONCES.pop(oldest, None)
        _USED_NONCES[nonce] = expires_at
        return True


def verify_context(token: str, secret: str, required: str, audience: str = "mcp") -> AccessContext:
    try:
        if not secret or len(secret) < 32:
            raise ValueError("weak secret")
        a, b = token.split(".", 1)
        raw = _dec(a)
        signature = _dec(b)
        expected = hmac.new(secret.encode(), raw, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("invalid signature")
        ctx = AccessContext.model_validate_json(raw)
        if ctx.expires_at <= time.time():
            raise ValueError("expired")
        if required not in ctx.permissions or ctx.audience != audience:
            raise ValueError("scope/audience")
        if not ctx.nonce or not _check_and_record_nonce(ctx.nonce, ctx.expires_at):
            raise ValueError("replay")
        return ctx
    except Exception as exc:  # noqa: BLE001
        raise PermissionError("ACCESS_CONTEXT_INVALID") from exc
