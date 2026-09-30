import json
import pytest
from app.core.auth import AuthenticationError, authenticate, require_roles
from app.models import Role
def test_bearer_authentication_uses_configured_principal(monkeypatch):
    token="analyst-token-0123456789-abcdef"
    monkeypatch.setenv("API_TOKENS_JSON", json.dumps({token:{"actor_id":"analyst:A-001","role":"analyst","team":"fraud-ops"}}))
    principal=authenticate(f"Bearer {token}")
    assert principal.actor_id=="analyst:A-001"
    assert principal.role is Role.analyst
def test_missing_or_unknown_bearer_is_rejected(monkeypatch):
    monkeypatch.setenv("API_TOKENS_JSON","{}")
    with pytest.raises(AuthenticationError): authenticate(None)
    with pytest.raises(AuthenticationError): authenticate("Bearer unknown-token")
def test_role_guard_rejects_wrong_role(monkeypatch):
    token="reviewer-token-0123456789-abcdef"
    monkeypatch.setenv("API_TOKENS_JSON", json.dumps({token:{"actor_id":"reviewer:R-001","role":"reviewer","team":"fraud-ops"}}))
    principal=authenticate(f"Bearer {token}")
    with pytest.raises(Exception): require_roles(principal, Role.analyst)
