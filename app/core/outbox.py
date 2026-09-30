from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

from app.core.db import DB


class OutboxWorker:
    def __init__(self, db: DB, sink: str = "./runtime/outbox-deliveries.jsonl", webhook: str = "", max_attempts: int = 5):
        self.db = db
        self.sink = Path(sink)
        self.webhook = webhook
        self.max_attempts = max(1, max_attempts)

    def deliver_once(self, limit: int = 50) -> int:
        rows = self.db.pending_outbox(limit, max_attempts=self.max_attempts)
        delivered = 0
        for row in rows:
            if not self.db.claim_outbox(row["id"], max_attempts=self.max_attempts):
                continue
            try:
                payload = json.loads(row["payload_json"])
                event_id = f"outbox-{row['id']}"
                envelope = {"event_id": event_id, "topic": row["topic"], "key": row["key"], "payload": payload}
                if self.webhook:
                    req = urllib.request.Request(
                        self.webhook,
                        data=json.dumps(envelope).encode(),
                        headers={"Content-Type": "application/json", "Idempotency-Key": event_id},
                    )
                    with urllib.request.urlopen(req, timeout=10):
                        pass
                else:
                    self.sink.parent.mkdir(parents=True, exist_ok=True)
                    with self.sink.open("a", encoding="utf-8") as f:
                        f.write(json.dumps(envelope, default=str) + "\n")
                self.db.mark_outbox(row["id"], "DELIVERED", max_attempts=self.max_attempts)
                delivered += 1
            except Exception as exc:  # noqa: BLE001
                self.db.mark_outbox(row["id"], "FAILED", type(exc).__name__, max_attempts=self.max_attempts)
        return delivered
