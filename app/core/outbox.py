from __future__ import annotations
import json, time, urllib.request
from pathlib import Path
from app.core.db import DB
class OutboxWorker:
    def __init__(self, db: DB, sink='./runtime/outbox-deliveries.jsonl', webhook=''):
        self.db=db; self.sink=Path(sink); self.webhook=webhook
    def deliver_once(self, limit=50):
        rows=self.db.pending_outbox(limit); delivered=0
        for row in rows:
            payload=json.loads(row['payload_json'])
            try:
                if self.webhook:
                    req=urllib.request.Request(self.webhook,data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
                    with urllib.request.urlopen(req,timeout=10): pass
                else:
                    self.sink.parent.mkdir(parents=True,exist_ok=True)
                    with self.sink.open('a',encoding='utf-8') as f:f.write(json.dumps(payload)+'\n')
                self.db.mark_outbox(row['id'],'DELIVERED'); delivered+=1
            except Exception as exc:
                self.db.mark_outbox(row['id'],'FAILED',str(exc))
        return delivered
