from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from app.models import TrustLevel


class TieredMemory:
    """Verified durable memory plus bounded retrieval; model-inferred memory never drives decisions."""

    MAX_FACTS_PER_CUSTOMER = 250
    def __init__(self, path="./runtime/memory.db"):
        project_root = Path(__file__).resolve().parents[2]
        path_obj = Path(path)
        self.path = path_obj if path_obj.is_absolute() else project_root / path_obj
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.customer_confirmed_level = TrustLevel.CUSTOMER_CONFIRMED
        self.system_verified_level = TrustLevel.SYSTEM_VERIFIED
        with sqlite3.connect(self.path) as c:
            c.execute("""CREATE TABLE IF NOT EXISTS memory_facts(
                customer_id TEXT, fact_id TEXT PRIMARY KEY, fact_type TEXT, fact_value TEXT,
                trust TEXT, source TEXT, created_at REAL, expires_at REAL, content_hash TEXT)""")
            c.execute("CREATE INDEX IF NOT EXISTS idx_memory_customer_expiry ON memory_facts(customer_id,expires_at,created_at)")

    def write(self, customer_id: str, fact_type: str, fact_value: object, trust: TrustLevel, source: str, ttl_days: int = 400):
        if trust == TrustLevel.MODEL_INFERRED:
            return None
        raw = json.dumps(fact_value, sort_keys=True, default=str)
        fid = hashlib.sha256(f"{customer_id}|{fact_type}|{raw}".encode()).hexdigest()[:24]
        now = time.time(); exp = now + min(max(int(ttl_days), 1), 730) * 86400
        with sqlite3.connect(self.path) as c:
            c.execute(
                "INSERT OR REPLACE INTO memory_facts VALUES(?,?,?,?,?,?,?,?,?)",
                (customer_id, fid, fact_type, raw, trust.value, source[:200], now, exp, hashlib.sha256(raw.encode()).hexdigest()),
            )
            c.execute("DELETE FROM memory_facts WHERE customer_id=? AND expires_at<=?", (customer_id, now))
            c.execute(
                "DELETE FROM memory_facts WHERE fact_id IN (SELECT fact_id FROM memory_facts WHERE customer_id=? ORDER BY created_at DESC LIMIT -1 OFFSET ?)",
                (customer_id, self.MAX_FACTS_PER_CUSTOMER),
            )
        return fid

    def recall(self, customer_id: str, query: str = "", minimum: TrustLevel = TrustLevel.CUSTOMER_CONFIRMED, limit: int = 50):
        now = time.time(); q = str(query).lower()[:1000]
        limit = max(1, min(int(limit), 100))
        rank = {TrustLevel.MODEL_INFERRED.value: 0, TrustLevel.CUSTOMER_CONFIRMED.value: 1, TrustLevel.REVIEWER_VERIFIED.value: 2, TrustLevel.SYSTEM_VERIFIED.value: 3}
        clauses = ["customer_id=?", "expires_at>?", "trust IN (?,?,?)"]
        params: list[object] = [customer_id, now, TrustLevel.CUSTOMER_CONFIRMED.value, TrustLevel.REVIEWER_VERIFIED.value, TrustLevel.SYSTEM_VERIFIED.value]
        if minimum == TrustLevel.REVIEWER_VERIFIED:
            clauses[-1] = "trust IN (?,?)"
            params[-2:] = [TrustLevel.REVIEWER_VERIFIED.value, TrustLevel.SYSTEM_VERIFIED.value]
        elif minimum == TrustLevel.SYSTEM_VERIFIED:
            clauses[-1] = "trust=?"
            params[-3:] = [TrustLevel.SYSTEM_VERIFIED.value]
        if q:
            clauses.append("lower(fact_type || ' ' || fact_value) LIKE ?")
            params.append(f"%{q}%")
        sql = f"SELECT fact_id,fact_type,fact_value,trust,source FROM memory_facts WHERE {' AND '.join(clauses)} ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with sqlite3.connect(self.path) as c:
            rows = c.execute(sql, params).fetchall()
        return [
            {"fact_id": r[0], "fact_type": r[1], "fact_value": json.loads(r[2]), "trust_level": r[3], "source": r[4]}
            for r in rows if rank.get(r[3], 0) >= rank[minimum.value]
        ]

    def cleanup_expired(self) -> int:
        with sqlite3.connect(self.path) as c:
            cur = c.execute("DELETE FROM memory_facts WHERE expires_at<=?", (time.time(),))
            return cur.rowcount


class LangMemBridge:
    """Bounded semantic overlay; SQLite remains authoritative."""

    NAMESPACE = ("dispute_memory", "{customer_id}")
    MAX_REHYDRATE_FACTS = 50

    def __init__(self, durable_memory: TieredMemory | None = None):
        self.available = False
        self._store = None
        self._manage_tool = None
        self._search_tool = None
        self._durable_memory = durable_memory
        self._rehydrated: set[str] = set()
        self._max_customers = 500
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

    async def rehydrate_customer(self, customer_id: str) -> int:
        if not self.available or self._durable_memory is None or customer_id in self._rehydrated:
            return 0
        facts = self._durable_memory.recall(customer_id, limit=self.MAX_REHYDRATE_FACTS, minimum=TrustLevel.CUSTOMER_CONFIRMED)
        count = 0
        for fact in facts:
            if await self.remember(customer_id, fact["fact_type"], fact["fact_value"]):
                count += 1
        self._rehydrated.add(customer_id)
        if len(self._rehydrated) > self._max_customers:
            self._rehydrated.pop()
        return count

    async def remember(self, customer_id: str, fact_type: str, fact_value: object) -> bool:
        if not self.available:
            return False
        try:
            content = json.dumps({"fact_type": fact_type, "fact_value": fact_value}, sort_keys=True, default=str)[:5000]
            await self._manage_tool.ainvoke({"content": content, "action": "create"}, config={"configurable": {"customer_id": customer_id}})
            return True
        except Exception:
            return False

    async def semantic_recall(self, customer_id: str, query: str, limit: int = 5) -> list[dict]:
        if not self.available or not query:
            return []
        limit = max(1, min(int(limit), 10))
        try:
            await self.rehydrate_customer(customer_id)
            result = await self._search_tool.ainvoke({"query": str(query)[:1000], "limit": limit}, config={"configurable": {"customer_id": customer_id}})
            hits = result.get("memories") or result.get("results") or [] if isinstance(result, dict) else (result if isinstance(result, list) else [])
            out = []
            for h in hits[:limit]:
                content = h.get("value", h.get("content", h)) if isinstance(h, dict) else h
                out.append({"fact_type": "semantic_recall", "fact_value": content, "trust_level": "model_inferred", "source": "langmem"})
            return out
        except Exception:
            return []
