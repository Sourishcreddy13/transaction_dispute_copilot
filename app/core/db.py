from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = PROJECT_ROOT / "logs"


_SENSITIVE_KEY_RE = re.compile(
    r"(pan|card[_ -]?number|account[_ -]?number|access[_ -]?context|authorization|api[_ -]?key|secret|password)",
    re.I,
)
_PAN_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")


def _safe_audit_value(value: Any, key: str | None = None, depth: int = 0) -> Any:
    if depth > 5:
        return "<TRUNCATED>"
    if key and _SENSITIVE_KEY_RE.search(key):
        return "<REDACTED>"
    if isinstance(value, dict):
        return {str(k): _safe_audit_value(v, str(k), depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_audit_value(v, key, depth + 1) for v in value[:50]]
    if isinstance(value, str):
        value = _PAN_RE.sub("<REDACTED_PAN>", value)
        if len(value) > 1000:
            return value[:997] + "..."
    return value


def _safe_error(exc: Exception) -> str:
    return f"{type(exc).__name__.upper()}_FAILED"


class DB:
    """SQLite persistence with optimistic concurrency and bounded work leases."""

    TRANSITIONS = {
        "NEW": {"RUNNING", "FAILED"},
        "RUNNING": {"CLASSIFIED", "NEEDS_INFO", "FAILED", "RESOLVED"},
        "CLASSIFIED": {"DATA_COLLECTED", "NEEDS_INFO", "FAILED", "RESOLVED"},
        "DATA_COLLECTED": {"FRAUD_ASSESSED", "NEEDS_INFO", "FAILED"},
        "FRAUD_ASSESSED": {"POLICY_EVALUATED", "NEEDS_INFO", "FAILED"},
        "POLICY_EVALUATED": {"RECOMMENDATION_READY", "NEEDS_INFO", "FAILED"},
        "RECOMMENDATION_READY": {"PENDING_REVIEW", "RESOLVED", "NEEDS_INFO", "FAILED"},
        "PENDING_REVIEW": {"RESOLVED", "NEEDS_INFO", "FAILED"},
        "NEEDS_INFO": {"RUNNING", "RESOLVED", "CLOSED"},
        "RESOLVED": {"CLOSED"},
        "FAILED": {"RUNNING", "CLOSED"},
        "CLOSED": set(),
    }

    def __init__(self, path: str = "runtime/copilot.db"):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.init()

    def conn(self):
        c = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA busy_timeout=10000")
        return c

    @staticmethod
    def _column_names(c: sqlite3.Connection, table: str) -> set[str]:
        return {r["name"] for r in c.execute(f"PRAGMA table_info({table})").fetchall()}

    def _ensure_column(self, c: sqlite3.Connection, table: str, name: str, ddl: str) -> None:
        if name not in self._column_names(c, table):
            c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")

    def init(self) -> None:
        with self.conn() as c:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS cases(
                  case_id TEXT PRIMARY KEY,
                  customer_id TEXT NOT NULL,
                  opened_by TEXT NOT NULL,
                  primary_transaction_id TEXT,
                  state TEXT NOT NULL,
                  review_state TEXT NOT NULL,
                  audit_state TEXT NOT NULL,
                  version INTEGER NOT NULL DEFAULT 0,
                  created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS review_tasks(
                  task_id TEXT PRIMARY KEY,
                  case_id TEXT NOT NULL,
                  recommendation_id TEXT NOT NULL,
                  version INTEGER NOT NULL DEFAULT 0,
                  status TEXT NOT NULL,
                  reviewer_id TEXT,
                  claimed_by TEXT,
                  claim_expires_at REAL,
                  expires_at REAL,
                  resolution_json TEXT,
                  created_at REAL NOT NULL,
                  recommendation_creator TEXT,
                  UNIQUE(case_id,recommendation_id)
                );
                CREATE TABLE IF NOT EXISTS operation_ledger(
                  idempotency_key TEXT PRIMARY KEY,
                  operation_type TEXT NOT NULL,
                  case_id TEXT NOT NULL,
                  input_hash TEXT NOT NULL,
                  status TEXT NOT NULL,
                  result_json TEXT,
                  attempt INTEGER NOT NULL DEFAULT 1,
                  lease_expires_at REAL,
                  owner_token TEXT,
                  created_at REAL NOT NULL,
                  updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit_events(
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  case_id TEXT NOT NULL,
                  stage TEXT NOT NULL,
                  payload_json TEXT NOT NULL,
                  previous_hash TEXT,
                  event_hash TEXT NOT NULL,
                  created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS outbox(
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  topic TEXT NOT NULL,
                  key TEXT NOT NULL UNIQUE,
                  payload_json TEXT NOT NULL,
                  status TEXT NOT NULL DEFAULT 'PENDING',
                  attempts INTEGER NOT NULL DEFAULT 0,
                  last_error TEXT,
                  lease_expires_at REAL,
                  created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS snapshots(
                  snapshot_id TEXT PRIMARY KEY,
                  case_id TEXT NOT NULL,
                  payload_json TEXT NOT NULL,
                  content_hash TEXT NOT NULL,
                  created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS short_context(
                  case_id TEXT NOT NULL,
                  turn_id INTEGER NOT NULL,
                  masked_text TEXT NOT NULL,
                  summary TEXT,
                  created_at REAL NOT NULL,
                  PRIMARY KEY(case_id,turn_id)
                );
                """
            )
            self._ensure_column(c, "review_tasks", "recommendation_creator", "TEXT")
            self._ensure_column(c, "operation_ledger", "attempt", "INTEGER NOT NULL DEFAULT 1")
            self._ensure_column(c, "operation_ledger", "lease_expires_at", "REAL")
            self._ensure_column(c, "operation_ledger", "owner_token", "TEXT")
            self._ensure_column(c, "outbox", "lease_expires_at", "REAL")

    def create_case(self, customer_id: str, opened_by: str, primary_transaction_id: str | None = None) -> str:
        cid = "CASE-" + uuid.uuid4().hex[:10].upper()
        with self.conn() as c:
            c.execute(
                "INSERT INTO cases(case_id,customer_id,opened_by,primary_transaction_id,state,review_state,audit_state,version,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (cid, customer_id, opened_by, primary_transaction_id, "NEW", "NONE", "PENDING", 0, time.time()),
            )
        return cid

    def get_case(self, cid: str):
        with self.conn() as c:
            return c.execute("SELECT * FROM cases WHERE case_id=?", (cid,)).fetchone()

    def cases_for_customer(self, customer_id: str):
        with self.conn() as c:
            return [
                dict(r)
                for r in c.execute(
                    "SELECT * FROM cases WHERE customer_id=? ORDER BY created_at DESC", (customer_id,)
                )
            ]

    def cases_for_queue(self):
        with self.conn() as c:
            return [
                dict(r)
                for r in c.execute(
                    "SELECT * FROM cases WHERE state IN ('PENDING_REVIEW','NEEDS_INFO','RECOMMENDATION_READY','FAILED') ORDER BY created_at DESC"
                )
            ]

    def set_state(
        self,
        cid: str,
        state: str,
        review_state: str | None = None,
        audit_state: str | None = None,
        expected_version: int | None = None,
        allow_same: bool = True,
    ) -> bool:
        with self.conn() as c:
            row = c.execute("SELECT * FROM cases WHERE case_id=?", (cid,)).fetchone()
            if not row:
                raise LookupError("CASE_NOT_FOUND")
            current = row["state"]
            if current != state and state not in self.TRANSITIONS.get(current, set()):
                raise ValueError(f"ILLEGAL_STATE_TRANSITION:{current}->{state}")
            if current == state and not allow_same:
                raise ValueError(f"ILLEGAL_STATE_TRANSITION:{current}->{state}")
            version = int(row["version"] if expected_version is None else expected_version)
            fields = ["state=?", "version=version+1"]
            vals: list[Any] = [state]
            if review_state is not None:
                fields.append("review_state=?")
                vals.append(review_state)
            if audit_state is not None:
                fields.append("audit_state=?")
                vals.append(audit_state)
            vals.extend([cid, version])
            cur = c.execute(
                f"UPDATE cases SET {','.join(fields)} WHERE case_id=? AND version=?", vals
            )
            if cur.rowcount != 1:
                raise ValueError("STATE_VERSION_CONFLICT")
            return True

    def save_context(self, cid: str, turn_id: int, masked_text: str, summary: str | None = None) -> None:
        with self.conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO short_context VALUES(?,?,?,?,?)",
                (cid, turn_id, masked_text[:12000], summary[:4000] if summary else None, time.time()),
            )

    def get_context(self, cid: str, limit: int = 10):
        limit = max(1, min(int(limit), 20))
        with self.conn() as c:
            return [
                dict(r)
                for r in c.execute(
                    "SELECT * FROM short_context WHERE case_id=? ORDER BY turn_id DESC LIMIT ?",
                    (cid, limit),
                )
            ]

    def append_context(self, cid: str, masked_text: str, summary: str | None = None) -> int:
        with self.conn() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                row = c.execute("SELECT COALESCE(MAX(turn_id),0)+1 AS n FROM short_context WHERE case_id=?", (cid,)).fetchone()
                turn_id = int(row["n"])
                c.execute(
                    "INSERT INTO short_context(case_id,turn_id,masked_text,summary,created_at) VALUES(?,?,?,?,?)",
                    (cid, turn_id, masked_text[:12000], summary[:4000] if summary else None, time.time()),
                )
                c.execute("COMMIT")
                return turn_id
            except Exception:
                try: c.execute("ROLLBACK")
                except sqlite3.Error: pass
                raise

    def next_turn_id(self, cid: str) -> int:
        with self.conn() as c:
            row = c.execute(
                "SELECT COALESCE(MAX(turn_id),0)+1 AS n FROM short_context WHERE case_id=?", (cid,)
            ).fetchone()
            return int(row["n"])

    def snapshot(self, cid: str, payload: dict[str, Any]) -> str:
        safe_payload = _safe_audit_value(payload)
        raw = json.dumps(safe_payload, sort_keys=True, default=str)
        sid = "SNAP-" + uuid.uuid4().hex
        h = hashlib.sha256(raw.encode()).hexdigest()
        with self.conn() as c:
            c.execute(
                "INSERT INTO snapshots VALUES(?,?,?,?,?)", (sid, cid, raw, h, time.time())
            )
        return sid

    def get_snapshot(self, sid: str):
        with self.conn() as c:
            return c.execute("SELECT * FROM snapshots WHERE snapshot_id=?", (sid,)).fetchone()

    def audit(self, cid: str, stage: str, payload: dict[str, Any]) -> str:
        safe_payload = _safe_audit_value(payload)
        raw = json.dumps(safe_payload, sort_keys=True, default=str)
        now = time.time()
        with self.conn() as c:
            prev = c.execute(
                "SELECT event_hash FROM audit_events WHERE case_id=? ORDER BY id DESC LIMIT 1", (cid,)
            ).fetchone()
            previous_hash = prev["event_hash"] if prev else ""
            event_hash = hashlib.sha256((previous_hash + "|" + stage + "|" + raw).encode()).hexdigest()
            c.execute(
                "INSERT INTO audit_events(case_id,stage,payload_json,previous_hash,event_hash,created_at) VALUES(?,?,?,?,?,?)",
                (cid, stage, raw, previous_hash, event_hash, now),
            )
            c.execute("UPDATE cases SET audit_state=? WHERE case_id=?", ("COMMITTED", cid))

        LOG_DIR.mkdir(parents=True, exist_ok=True)
        event = {
            "timestamp": now,
            "actor": safe_payload.get("actor_id", safe_payload.get("reviewer_id", "system")),
            "action": stage,
            "tool": safe_payload.get("tool_name"),
            "decision": (safe_payload.get("recommendation") or {}).get("primary_action"),
            "case_id": cid,
            "event_hash": event_hash,
            "payload": safe_payload,
        }
        with (LOG_DIR / "agent_actions.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, default=str) + "\n")
        return event_hash

    def get_audit(self, cid: str):
        with self.conn() as c:
            return [
                dict(r)
                for r in c.execute("SELECT * FROM audit_events WHERE case_id=? ORDER BY id", (cid,))
            ]

    @staticmethod
    def _scoped_key(key: str, op_type: str, case_id: str) -> str:
        digest = hashlib.sha256(f"{op_type}|{case_id}|{key}".encode()).hexdigest()
        return f"idem:{digest}"

    def begin_operation(
        self,
        key: str,
        op_type: str,
        case_id: str,
        input_hash: str,
        lease_seconds: int = 600,
    ) -> dict[str, Any]:
        if not key or len(key) > 200:
            raise ValueError("IDEMPOTENCY_KEY_INVALID")
        now = time.time()
        lease = now + min(max(int(lease_seconds), 30), 3600)
        scoped_key = self._scoped_key(key, op_type, case_id)
        owner_token = uuid.uuid4().hex
        with self.conn() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                inserted = c.execute(
                    "INSERT OR IGNORE INTO operation_ledger(idempotency_key,operation_type,case_id,input_hash,status,result_json,attempt,lease_expires_at,owner_token,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (scoped_key, op_type, case_id, input_hash, "STARTED", None, 1, lease, owner_token, now, now),
                ).rowcount == 1
                row = c.execute(
                    "SELECT * FROM operation_ledger WHERE idempotency_key=?", (scoped_key,)
                ).fetchone()
                if row is None:
                    raise RuntimeError("IDEMPOTENCY_LEDGER_WRITE_FAILED")
                if row["operation_type"] != op_type or row["case_id"] != case_id:
                    raise ValueError("IDEMPOTENCY_SCOPE_MISMATCH")
                if row["input_hash"] != input_hash:
                    raise ValueError("IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_INPUT")
                if inserted:
                    c.execute("COMMIT")
                    return dict(row)
                if row["status"] == "COMPLETED":
                    c.execute("COMMIT")
                    return dict(row)
                if row["status"] == "STARTED" and row["lease_expires_at"] and row["lease_expires_at"] > now:
                    c.execute("ROLLBACK")
                    raise ValueError("OP_IN_PROGRESS")

                attempt = int(row["attempt"]) + 1 if row["status"] == "FAILED" or row["lease_expires_at"] <= now else int(row["attempt"])
                cur = c.execute(
                    "UPDATE operation_ledger SET status='STARTED',attempt=?,lease_expires_at=?,owner_token=?,updated_at=? WHERE idempotency_key=? AND status IN ('FAILED','STARTED') AND (lease_expires_at IS NULL OR lease_expires_at<=?)",
                    (attempt, lease, owner_token, now, scoped_key, now),
                )
                if cur.rowcount != 1:
                    c.execute("ROLLBACK")
                    raise ValueError("OP_IN_PROGRESS")
                row = c.execute("SELECT * FROM operation_ledger WHERE idempotency_key=?", (scoped_key,)).fetchone()
                c.execute("COMMIT")
                return dict(row)
            except Exception:
                try:
                    c.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise

    def finish_operation(self, operation: dict[str, Any], result: dict[str, Any]) -> None:
        key = operation["idempotency_key"]
        owner = operation.get("owner_token")
        attempt = operation.get("attempt")
        with self.conn() as c:
            cur = c.execute(
                "UPDATE operation_ledger SET status='COMPLETED',result_json=?,lease_expires_at=NULL,owner_token=NULL,updated_at=? WHERE idempotency_key=? AND status='STARTED' AND owner_token=? AND attempt=?",
                (json.dumps(result, default=str), time.time(), key, owner, attempt),
            )
            if cur.rowcount != 1:
                raise ValueError("OPERATION_OWNERSHIP_CONFLICT")

    def fail_operation(self, operation: dict[str, Any], error: str) -> None:
        key = operation["idempotency_key"]
        owner = operation.get("owner_token")
        attempt = operation.get("attempt")
        with self.conn() as c:
            cur = c.execute(
                "UPDATE operation_ledger SET status='FAILED',result_json=?,lease_expires_at=NULL,owner_token=NULL,updated_at=? WHERE idempotency_key=? AND status='STARTED' AND owner_token=? AND attempt=?",
                (json.dumps({"error": _safe_error(Exception(error))}), time.time(), key, owner, attempt),
            )
            if cur.rowcount != 1:
                raise ValueError("OPERATION_OWNERSHIP_CONFLICT")

    def create_review(
        self,
        cid: str,
        recommendation_id: str,
        expires_at: float | None = None,
        recommendation_creator: str | None = None,
    ):
        task_id = "TASK-" + uuid.uuid4().hex[:10].upper()
        now = time.time()
        expires = expires_at or now + 86400
        with self.conn() as c:
            c.execute(
                "INSERT OR IGNORE INTO review_tasks(task_id,case_id,recommendation_id,status,expires_at,created_at,recommendation_creator) VALUES(?,?,?,?,?,?,?)",
                (task_id, cid, recommendation_id, "PENDING", expires, now, recommendation_creator),
            )
            row = c.execute(
                "SELECT * FROM review_tasks WHERE case_id=? AND recommendation_id=?", (cid, recommendation_id)
            ).fetchone()
            return self.review_model(row)

    def review_model(self, row):
        from app.models import ReviewTask
        return ReviewTask.model_validate(dict(row))

    def get_review(self, tid: str):
        with self.conn() as c:
            return c.execute("SELECT * FROM review_tasks WHERE task_id=?", (tid,)).fetchone()

    def claim_review(self, tid: str, reviewer: str, expected_version: int, lease_seconds: int = 900) -> bool:
        if not reviewer:
            return False
        now = time.time()
        lease = now + min(max(int(lease_seconds), 60), 3600)
        with self.conn() as c:
            cur = c.execute(
                """
                UPDATE review_tasks
                SET status='CLAIMED', claimed_by=?, claim_expires_at=?, version=version+1
                WHERE task_id=? AND version=? AND (expires_at IS NULL OR expires_at>?)
                  AND (status='PENDING' OR (status='CLAIMED' AND claim_expires_at<?))
                """,
                (reviewer, lease, tid, expected_version, now, now),
            )
            return cur.rowcount == 1

    def resolve_review(
        self,
        tid: str,
        reviewer: str,
        expected_version: int,
        case_id: str,
        action: str,
        reason: str,
        note: str,
    ) -> bool:
        from app.models import Action, FinalDisposition

        now = time.time()
        d = FinalDisposition(
            task_id=tid,
            case_id=case_id,
            action=Action(action),
            reviewer_id=reviewer,
            verdict="approve" if action != "investigate" else "override",
            reason_code=reason,
            note=note[:300],
        )
        with self.conn() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                case_row = c.execute(
                    "SELECT case_id,opened_by,state,version FROM cases WHERE case_id=?", (case_id,)
                ).fetchone()
                task = c.execute("SELECT * FROM review_tasks WHERE task_id=?", (tid,)).fetchone()
                if not case_row or not task or task["case_id"] != case_id:
                    c.execute("ROLLBACK")
                    return False
                if reviewer in {case_row["opened_by"], task["recommendation_creator"]}:
                    c.execute("ROLLBACK")
                    return False
                if task["expires_at"] is not None and task["expires_at"] <= now:
                    c.execute("ROLLBACK")
                    return False
                if task["claim_expires_at"] is not None and task["claim_expires_at"] <= now:
                    c.execute("ROLLBACK")
                    return False
                if case_row["state"] != "PENDING_REVIEW":
                    c.execute("ROLLBACK")
                    return False
                cur = c.execute(
                    """
                    UPDATE review_tasks
                    SET status='RESOLVED', reviewer_id=?, resolution_json=?, version=version+1, claim_expires_at=NULL
                    WHERE task_id=? AND version=? AND status='CLAIMED' AND claimed_by=? AND expires_at>?
                    """,
                    (reviewer, d.model_dump_json(), tid, expected_version, reviewer, now),
                )
                if cur.rowcount != 1:
                    c.execute("ROLLBACK")
                    return False
                payload = d.model_dump(mode="json")
                raw = json.dumps(_safe_audit_value(payload), sort_keys=True)
                prev = c.execute(
                    "SELECT event_hash FROM audit_events WHERE case_id=? ORDER BY id DESC LIMIT 1", (case_id,)
                ).fetchone()
                previous_hash = prev["event_hash"] if prev else ""
                event_hash = hashlib.sha256((previous_hash + "|final_disposition|" + raw).encode()).hexdigest()
                c.execute(
                    "INSERT INTO audit_events(case_id,stage,payload_json,previous_hash,event_hash,created_at) VALUES(?,?,?,?,?,?)",
                    (case_id, "final_disposition", raw, previous_hash, event_hash, now),
                )
                # Do not mark the case RESOLVED until the checkpointed graph resumes and
                # passes its final release gate; this keeps review persistence and workflow
                # state reconcilable when the resume process fails.
                cur = c.execute(
                    "UPDATE cases SET review_state='RESOLVED',audit_state='COMMITTED',version=version+1 WHERE case_id=? AND state='PENDING_REVIEW' AND version=?",
                    (case_id, case_row["version"]),
                )
                if cur.rowcount != 1:
                    c.execute("ROLLBACK")
                    return False
                c.execute("COMMIT")
            except Exception:
                try:
                    c.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise

        LOG_DIR.mkdir(parents=True, exist_ok=True)
        event = {
            "timestamp": now,
            "actor": reviewer,
            "action": "final_disposition",
            "tool": None,
            "decision": action,
            "case_id": case_id,
            "event_hash": event_hash,
            "payload": _safe_audit_value(payload),
        }
        with (LOG_DIR / "agent_actions.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, default=str) + "\n")
        return True

    def enqueue(self, topic: str, key: str, payload: dict[str, Any]) -> None:
        if len(key) > 250:
            raise ValueError("OUTBOX_KEY_TOO_LONG")
        safe = _safe_audit_value(payload)
        with self.conn() as c:
            c.execute(
                "INSERT OR IGNORE INTO outbox(topic,key,payload_json,created_at) VALUES(?,?,?,?)",
                (topic, key, json.dumps(safe, default=str), time.time()),
            )

    def pending_outbox(self, limit: int = 50, max_attempts: int = 5):
        limit = max(1, min(int(limit), 100))
        now = time.time()
        with self.conn() as c:
            return c.execute(
                """
                SELECT * FROM outbox
                WHERE (status IN ('PENDING','FAILED') AND attempts<?)
                   OR (status='PROCESSING' AND lease_expires_at<? AND attempts<?)
                ORDER BY id LIMIT ?
                """,
                (max_attempts, now, max_attempts, limit),
            ).fetchall()

    def claim_outbox(self, row_id: int, lease_seconds: int = 60, max_attempts: int = 5) -> bool:
        now = time.time()
        lease = now + min(max(int(lease_seconds), 10), 300)
        max_attempts = max(1, int(max_attempts))
        with self.conn() as c:
            cur = c.execute(
                """
                UPDATE outbox SET status='PROCESSING',lease_expires_at=?
                WHERE id=? AND attempts<? AND (
                    status IN ('PENDING','FAILED') OR
                    (status='PROCESSING' AND lease_expires_at<?)
                )
                """,
                (lease, row_id, max_attempts, now),
            )
            return cur.rowcount == 1

    def mark_outbox(self, row_id: int, status: str, error: str | None = None, max_attempts: int = 5) -> None:
        with self.conn() as c:
            if status == "FAILED":
                row = c.execute("SELECT attempts FROM outbox WHERE id=?", (row_id,)).fetchone()
                attempts = int(row["attempts"]) if row else 0
                target = "DEAD_LETTER" if attempts + 1 >= max_attempts else "FAILED"
            else:
                target = status
            c.execute(
                "UPDATE outbox SET status=?,attempts=attempts+1,last_error=?,lease_expires_at=NULL WHERE id=? AND status='PROCESSING'",
                (target, error[:500] if error else None, row_id),
            )

    def cleanup_expired(self, *, context_days: int = 30, snapshot_days: int = 365, audit_days: int = 730) -> dict[str, int]:
        now = time.time()
        removed = {}
        with self.conn() as c:
            for table, days in (("short_context", context_days), ("snapshots", snapshot_days), ("audit_events", audit_days)):
                cutoff = now - days * 86400
                cur = c.execute(f"DELETE FROM {table} WHERE created_at<?", (cutoff,))
                removed[table] = cur.rowcount
            c.execute("VACUUM")
        return removed
