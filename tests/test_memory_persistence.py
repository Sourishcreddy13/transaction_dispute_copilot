from src.memory.store import TieredMemory
from app.models import TrustLevel

def test_cross_session_memory(tmp_path):
    path=tmp_path/'memory.db'
    a=TieredMemory(str(path))
    a.write('C-1001','last_dispute_type','unauthorized',TrustLevel.SYSTEM_VERIFIED,'deterministic')
    b=TieredMemory(str(path))
    hits=b.recall('C-1001','last_dispute_type',TrustLevel.CUSTOMER_CONFIRMED)
    assert hits and hits[0]['fact_value']=='unauthorized'
