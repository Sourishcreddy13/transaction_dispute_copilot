from app.core.db import DB

def test_review_compare_and_set(tmp_path):
 db=DB(str(tmp_path/'x.db')); cid=db.create_case('C-1001','analyst:A-001'); task=db.create_review(cid,'REC-1'); assert not db.resolve_review(task.task_id,'reviewer:R-001',0,cid,'provisional_credit','EVIDENCE_SUFFICIENT','too-early'); assert db.claim_review(task.task_id,'reviewer:R-001',0); assert not db.claim_review(task.task_id,'reviewer:R-002',0); assert db.resolve_review(task.task_id,'reviewer:R-001',1,cid,'provisional_credit','EVIDENCE_SUFFICIENT','ok')

def test_reviewer_cannot_resolve_unclaimed_task(tmp_path):
    from app.core.db import DB
    db = DB(str(tmp_path / 'review.db'))
    cid = db.create_case('C-1001', 'analyst:A-001')
    task = db.create_review(cid, 'REC-1', recommendation_creator='analyst:A-001')
    assert not db.resolve_review(task.task_id, 'reviewer:R-001', 0, cid, 'deny', 'FRAUD_CONFIRMED', 'unclaimed')
