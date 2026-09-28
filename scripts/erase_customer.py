from __future__ import annotations
import sqlite3, sys
from pathlib import Path
from app.core.db import DB
from src.memory.store import TieredMemory

customer=sys.argv[1] if len(sys.argv)>1 else None
if not customer: raise SystemExit('usage: uv run python scripts/erase_customer.py C-1001')

db=DB()
with db.conn() as c:
    c.execute('UPDATE cases SET customer_id="ERASED" WHERE customer_id=?',(customer,))
    c.execute('INSERT INTO audit_events(case_id,stage,payload_json,previous_hash,event_hash,created_at) SELECT case_id,"customer_erasure","{}","","erasure",strftime("%s","now") FROM cases WHERE customer_id="ERASED"')
mem=TieredMemory()
with sqlite3.connect(mem.path) as c: c.execute('DELETE FROM memory_facts WHERE customer_id=?',(customer,))
print(f'erased synthetic customer {customer}')
