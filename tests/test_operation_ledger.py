import time
import pytest
from app.core.db import DB

def test_idempotency_is_scoped_to_case_and_operation(tmp_path):
    db = DB(str(tmp_path / "db.sqlite"))
    first = db.begin_operation("same-key", "copilot_run", "CASE-1", "hash-a")
    second = db.begin_operation("same-key", "copilot_run", "CASE-2", "hash-a")
    assert first["case_id"] == "CASE-1"
    assert second["case_id"] == "CASE-2"
    with pytest.raises(ValueError, match="OP_IN_PROGRESS"):
        db.begin_operation("same-key", "copilot_run", "CASE-1", "hash-a")

def test_stale_worker_cannot_finish_reclaimed_operation(tmp_path):
    db = DB(str(tmp_path / "db.sqlite"))
    first = db.begin_operation("retry-key", "copilot_run", "CASE-1", "hash-a")
    with db.conn() as c:
        c.execute("UPDATE operation_ledger SET lease_expires_at=? WHERE idempotency_key=?", (time.time()-1, first["idempotency_key"]))
    second = db.begin_operation("retry-key", "copilot_run", "CASE-1", "hash-a")
    assert second["attempt"] == 2
    with pytest.raises(ValueError, match="OPERATION_OWNERSHIP_CONFLICT"):
        db.finish_operation(first, {"case_state":"RESOLVED"})
    db.finish_operation(second, {"case_state":"RESOLVED"})

def test_input_mismatch_on_reused_key_is_rejected(tmp_path):
    db = DB(str(tmp_path / "db.sqlite"))
    db.begin_operation("same-key", "copilot_run", "CASE-1", "hash-a")
    with db.conn() as c:
        c.execute("UPDATE operation_ledger SET lease_expires_at=?", (time.time()-1,))
    with pytest.raises(ValueError, match="IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_INPUT"):
        db.begin_operation("same-key", "copilot_run", "CASE-1", "hash-b")
