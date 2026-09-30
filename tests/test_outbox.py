def test_outbox_delivery(test_copilot,tmp_path):
 from app.core.outbox import OutboxWorker
 test_copilot.db.enqueue('test','k1',{'hello':'world'})
 w=OutboxWorker(test_copilot.db,str(tmp_path/'outbox.jsonl')); assert w.deliver_once()==1
 assert 'world' in (tmp_path/'outbox.jsonl').read_text()

def test_expired_processing_event_can_be_reclaimed(test_copilot):
    import time
    test_copilot.db.enqueue("test", "reclaim-key", {"hello":"world"})
    with test_copilot.db.conn() as c:
        c.execute("UPDATE outbox SET status='PROCESSING', lease_expires_at=? WHERE key=?", (time.time()-1, "reclaim-key"))
    rows = test_copilot.db.pending_outbox(10, 5)
    assert rows and rows[0]["key"] == "reclaim-key"
    assert test_copilot.db.claim_outbox(rows[0]["id"]) is True

def test_malformed_outbox_payload_is_failed_not_fatal(test_copilot, tmp_path):
    from app.core.outbox import OutboxWorker
    test_copilot.db.enqueue("test", "bad-json", {"hello":"world"})
    with test_copilot.db.conn() as c:
        c.execute("UPDATE outbox SET payload_json=? WHERE key=?", ("{not-json", "bad-json"))
    assert OutboxWorker(test_copilot.db, str(tmp_path/"outbox.jsonl")).deliver_once() == 0
    with test_copilot.db.conn() as c:
        row = c.execute("SELECT status, attempts FROM outbox WHERE key=?", ("bad-json",)).fetchone()
    assert row["status"] == "FAILED"
    assert row["attempts"] == 1
