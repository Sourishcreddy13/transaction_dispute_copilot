def test_human_review_flow(test_copilot):
    cid=test_copilot.db.create_case('C-1002','analyst:A-001','T-2001')
    out=test_copilot.run(cid,'analyst:A-001','I did not make this transaction')
    assert out.recommendation is not None
    assert out.review_task is not None
    task=out.review_task
    assert test_copilot.db.claim_review(task.task_id,'reviewer:R-001',task.version)
    assert test_copilot.db.resolve_review(task.task_id,'reviewer:R-001',task.version+1,cid,'investigate','HIGH_RISK','manual review')
    assert test_copilot.db.get_case(cid)['state']=='RESOLVED'

def test_idempotent_rerun(test_copilot):
    cid=test_copilot.db.create_case('C-1001','analyst:A-001','T-1007')
    a=test_copilot.run(cid,'analyst:A-001','I did not make this transaction')
    b=test_copilot.run(cid,'analyst:A-001','I did not make this transaction')
    assert a.run_id==b.run_id
    assert len(test_copilot.db.get_audit(cid))>=1
