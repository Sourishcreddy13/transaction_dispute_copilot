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
    """LangMem integration boundary.

    TieredMemory (above) remains the sole source of truth for decision-critical facts:
    every write that feeds the recommendation/decision path goes through
    ``TieredMemory.write`` first and ``TieredMemory.recall`` is what
    ``CopilotGraph.ingress`` gates on for anything the agent is allowed to *act* on.

    LangMem is layered on top, purely as an additional *semantic* recall surface:
    every fact accepted by ``TieredMemory.write`` is mirrored here via ``remember()``
    so it becomes searchable by meaning (not just substring), and ``semantic_recall()``
    returns those hits tagged ``trust_level="model_inferred"`` so callers can never
    mistake a semantic match for a verified fact. If the ``langmem``/``langgraph.store``
    packages are unavailable, or their API drifts, every method below degrades to a
    no-op rather than raising -- no part of the decision path depends on LangMem being
    importable, matching the optional-dependency pattern used elsewhere in this repo
    (e.g. ``src/observability/tracing.py``).
    """

    NAMESPACE = ("dispute_memory", "{customer_id}")

    def __init__(self):
        self.available = False
        self._store = None
        self._manage_tool = None
        self._search_tool = None
        try:
            from langgraph.store.memory import InMemoryStore
            from langmem import create_manage_memory_tool, create_search_memory_tool

            self._store = InMemoryStore()
            self._manage_tool = create_manage_memory_tool(namespace=self.NAMESPACE, store=self._store)
            self._search_tool = create_search_memory_tool(namespace=self.NAMESPACE, store=self._store)
            self.available = True
        except Exception:
            self.available = False

    def available_status(self) -> bool:
        return self.available

    async def remember(self, customer_id: str, fact_type: str, fact_value: object) -> bool:
        """Mirror a fact TieredMemory already accepted into LangMem's semantic index.

        Returns False (never raises) if LangMem is unavailable or the write fails --
        callers must not treat this as authoritative persistence.
        """
        if not self.available:
            return False
        try:
            content = json.dumps({"fact_type": fact_type, "fact_value": fact_value}, sort_keys=True, default=str)
            await self._manage_tool.ainvoke(
                {"content": content, "action": "create"},
                config={"configurable": {"customer_id": customer_id}},
            )
            return True
        except Exception:
            return False

    async def semantic_recall(self, customer_id: str, query: str, limit: int = 5) -> list[dict]:
        """Best-effort semantic search over this customer's mirrored facts.

        Always returns items tagged trust_level="model_inferred" -- read-only context
        for the LLM-facing agents, never a substitute for TieredMemory.recall() on the
        decision path. Returns [] (never raises) when unavailable or on any tool error.
        """
        if not self.available or not query:
            return []
        try:
            result = await self._search_tool.ainvoke(
                {"query": query, "limit": limit},
                config={"configurable": {"customer_id": customer_id}},
            )
            if isinstance(result, dict):
                hits = result.get("memories") or result.get("results") or []
            elif isinstance(result, list):
                hits = result
            else:
                hits = []
            out = []
            for h in hits[:limit]:
                content = h.get("value", h.get("content", h)) if isinstance(h, dict) else h
                out.append(
                    {
                        "fact_type": "semantic_recall",
                        "fact_value": content,
                        "trust_level": "model_inferred",
                        "source": "langmem",
                    }
                )
            return out
        except Exception:
            return []
