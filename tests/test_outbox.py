def test_outbox_delivery(test_copilot,tmp_path):
 from app.core.outbox import OutboxWorker
 test_copilot.db.enqueue('test','k1',{'hello':'world'})
 w=OutboxWorker(test_copilot.db,str(tmp_path/'outbox.jsonl')); assert w.deliver_once()==1
 assert 'world' in (tmp_path/'outbox.jsonl').read_text()
