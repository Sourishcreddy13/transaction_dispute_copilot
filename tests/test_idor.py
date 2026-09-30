import pytest
from app.core.data_plane import DataPlane
from app.core.security import mint_context,verify_context
from app.models import Principal,Role

def test_foreign_transaction_is_hidden():
 d=DataPlane(); p=Principal(actor_id='analyst:A-001',role=Role.analyst); t=mint_context(p,'CASE-1','C-1001',['txn:read'],'test-secret-0123456789-abcdef-0123456789'); c=verify_context(t,'test-secret-0123456789-abcdef-0123456789','txn:read')
 with pytest.raises(LookupError): d.get_transaction(c,'T-2001')
