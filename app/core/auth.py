from __future__ import annotations

import hashlib
import json
import os
from functools import lru_cache
from typing import Any

from app.models import Principal, Role


class AuthenticationError(PermissionError):
    """Raised when the request does not carry a configured bearer credential."""


class AuthorizationError(PermissionError):
    """Raised when an authenticated principal lacks the requested role/scope."""


def _token_fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


@lru_cache(maxsize=1)
def _configured_tokens(raw: str) -> dict[str, Principal]:
    if not raw.strip():
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("API_TOKENS_JSON_INVALID") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("API_TOKENS_JSON_INVALID")

    principals: dict[str, Principal] = {}
    for token, value in payload.items():
        if not isinstance(token, str) or len(token) < 20:
            raise RuntimeError("API_TOKEN_TOO_SHORT")
        if not isinstance(value, dict):
            raise RuntimeError("API_TOKEN_PRINCIPAL_INVALID")
        try:
            principal = Principal(
                actor_id=str(value["actor_id"]),
                role=Role(value["role"]),
                team=str(value.get("team", "fraud-ops")),
                customer_scope=value.get("customer_scope"),
            )
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("API_TOKEN_PRINCIPAL_INVALID") from exc
        principals[token] = principal
    return principals


def authenticate(authorization: str | None) -> Principal:
    if not authorization:
        raise AuthenticationError("AUTHENTICATION_REQUIRED")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise AuthenticationError("AUTHENTICATION_REQUIRED")

    registry = _configured_tokens(os.getenv("API_TOKENS_JSON", ""))
    principal = registry.get(token)
    if principal is None:
        raise AuthenticationError("AUTHENTICATION_FAILED")
    return principal


def require_roles(principal: Principal, *roles: Role) -> Principal:
    if principal.role not in set(roles):
        raise AuthorizationError("ROLE_NOT_AUTHORIZED")
    return principal


def auth_debug_metadata(principal: Principal) -> dict[str, Any]:
    return {
        "actor_id": principal.actor_id,
        "role": principal.role.value,
        "team": principal.team,
        "customer_scope": principal.customer_scope,
    }
