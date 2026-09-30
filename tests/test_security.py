import time,pytest
from app.models import Principal,Role
from app.core.security import mint_context,verify_context

def test_signed_context_roundtrip():
 p=Principal(actor_id='analyst:A-001',role=Role.analyst); t=mint_context(p,'CASE-1','C-1001',['txn:read'],'test-secret-0123456789-abcdef-0123456789'); c=verify_context(t,'test-secret-0123456789-abcdef-0123456789','txn:read'); assert c.customer_id=='C-1001'
def test_tampered_context_rejected():
 p=Principal(actor_id='analyst:A-001',role=Role.analyst); t=mint_context(p,'CASE-1','C-1001',['txn:read'],'test-secret-0123456789-abcdef-0123456789'); a,b=t.split('.'); bad=a[:-1]+('A' if a[-1]!='A' else 'B');
 with pytest.raises(PermissionError):verify_context(bad+'.'+b,'test-secret-0123456789-abcdef-0123456789','txn:read')

def test_access_context_replay_is_rejected():
    p = Principal(actor_id="analyst:A-001", role=Role.analyst)
    secret = "test-secret-0123456789-abcdef-0123456789"
    token = mint_context(p, "CASE-1", "C-1001", ["txn:read"], secret)
    first = verify_context(token, secret, "txn:read")
    assert first.customer_id == "C-1001"
    with pytest.raises(PermissionError, match="ACCESS_CONTEXT_INVALID"):
        verify_context(token, secret, "txn:read")


def test_weak_access_secret_is_rejected():
    p = Principal(actor_id="analyst:A-001", role=Role.analyst)
    with pytest.raises(ValueError, match="ACCESS_SECRET_TOO_WEAK"):
        mint_context(p, "CASE-1", "C-1001", ["txn:read"], "too-short")
