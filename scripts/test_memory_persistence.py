from pathlib import Path
from src.memory.store import TieredMemory
from app.models import TrustLevel

path=Path('runtime/memory_evidence.db'); path.parent.mkdir(parents=True,exist_ok=True)
m1=TieredMemory(str(path)); m1.write('C-1001','preferred_contact_channel','secure_message',TrustLevel.SYSTEM_VERIFIED,'synthetic')
m2=TieredMemory(str(path)); hits=m2.recall('C-1001','preferred_contact_channel',TrustLevel.CUSTOMER_CONFIRMED)
out=Path('logs/memory_test.log'); out.parent.mkdir(exist_ok=True); out.write_text(f'cross_session_recall={bool(hits)}\n')
print(out.read_text().strip())
