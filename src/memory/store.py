from __future__ import annotations
import hashlib, json, sqlite3, time
from pathlib import Path
from app.models import TrustLevel

class TieredMemory:
    """Working memory is per-run; semantic memory is durable and trust-filtered."""
    def __init__(self, path="./runtime/memory.db"):
        self.path=path; Path(path).parent.mkdir(parents=True,exist_ok=True)
        self.customer_confirmed_level=TrustLevel.CUSTOMER_CONFIRMED
        self.system_verified_level=TrustLevel.SYSTEM_VERIFIED
        with sqlite3.connect(path) as c:
            c.execute("""CREATE TABLE IF NOT EXISTS memory_facts(
                customer_id TEXT, fact_id TEXT PRIMARY KEY, fact_type TEXT, fact_value TEXT,
                trust TEXT, source TEXT, created_at REAL, expires_at REAL, content_hash TEXT)""")
    def write(self, customer_id:str, fact_type:str, fact_value:object, trust:TrustLevel, source:str, ttl_days:int=400):
        if trust == TrustLevel.MODEL_INFERRED:
            return None
        raw=json.dumps(fact_value,sort_keys=True,default=str)
        fid=hashlib.sha256(f"{customer_id}|{fact_type}|{raw}".encode()).hexdigest()[:24]
        now=time.time(); exp=now+ttl_days*86400
        with sqlite3.connect(self.path) as c:
            c.execute("INSERT OR REPLACE INTO memory_facts VALUES(?,?,?,?,?,?,?,?,?)",
                      (customer_id,fid,fact_type,raw,trust.value,source,now,exp,hashlib.sha256(raw.encode()).hexdigest()))
        return fid
    def recall(self, customer_id:str, query:str="", minimum:TrustLevel=TrustLevel.CUSTOMER_CONFIRMED):
        now=time.time(); q=query.lower()
        with sqlite3.connect(self.path) as c:
            rows=c.execute("SELECT * FROM memory_facts WHERE customer_id=? AND expires_at>? ORDER BY created_at DESC",
                           (customer_id,now)).fetchall()
        rank={TrustLevel.MODEL_INFERRED.value:0,TrustLevel.CUSTOMER_CONFIRMED.value:1,TrustLevel.REVIEWER_VERIFIED.value:2,TrustLevel.SYSTEM_VERIFIED.value:3}
        out=[]
        for r in rows:
            if rank[r[4]] < rank[minimum.value]: continue
            if q and q not in (r[2]+" "+r[3]).lower(): continue
            out.append({"fact_id":r[1],"fact_type":r[2],"fact_value":json.loads(r[3]),"trust_level":r[4],"source":r[5]})
        return out

class LangMemBridge:
    """Required LangMem integration boundary. Decision-critical writes remain deterministic."""
    def __init__(self):
        try:
            from langmem import create_manage_memory_tool, create_search_memory_tool
            self.create_manage_memory_tool=create_manage_memory_tool
            self.create_search_memory_tool=create_search_memory_tool
            self.available=True
        except Exception:
            self.available=False
    def available_status(self): return self.available
