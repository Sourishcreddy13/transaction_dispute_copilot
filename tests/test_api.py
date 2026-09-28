from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.api.main import app as real_app

def test_end_to_end(test_copilot,monkeypatch):
    from app.api import main
    old=main.copilot; main.copilot=test_copilot
    try:
        client=TestClient(real_app)
        r=client.post('/api/cases',json={'customer_id':'C-1001','transaction_id':'T-1007'}); assert r.status_code==200; cid=r.json()['case_id']
        r=client.post(f'/api/cases/{cid}/run',json={'text':'I did not make this transaction'}); assert r.status_code==200; assert r.json()['recommendation']['primary_action']=='provisional_credit'
    finally: main.copilot=old
