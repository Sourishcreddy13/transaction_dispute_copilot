from app.core.engines import FraudEngine,PolicyEngine,DecisionEngine
from decimal import Decimal
from app.models import Classification,Transaction,CustomerProfile,PriorDispute

def tx(cid='C-1001', tid='T'): return Transaction(transaction_id=tid,customer_id=cid,amount='1299',merchant='Metro',category='groceries',device_id='dev-1',timestamp='2026-01-01T00:00:00Z')
def test_unauthorized_provisional_credit():
 p=CustomerProfile(customer_id='C-1001',known_device_ids=['dev-1'],typical_categories=['groceries']); f=FraudEngine().score(tx(),[tx('C-1001','H1'),tx('C-1001','H2'),tx('C-1001','H3')],p,[]); po=PolicyEngine().evaluate(Classification(intent='unauthorized',confidence=1,source='llm'),tx(),f); r=DecisionEngine().decide('CASE-1',Classification(intent='unauthorized',confidence=1,source='llm'),f,po,txn=tx()); assert r.primary_action.value=='provisional_credit'; assert r.auto_disposition_eligible
def test_high_value_requires_review():
 p=CustomerProfile(customer_id='C-1001',known_device_ids=['dev-1'],typical_categories=['groceries']); high=tx().model_copy(update={'amount': Decimal('60000.00')}); f=FraudEngine().score(high,[tx('C-1001','H1'),tx('C-1001','H2'),tx('C-1001','H3')],p,[]); po=PolicyEngine().evaluate(Classification(intent='unauthorized',confidence=1,source='llm'),high,f); r=DecisionEngine().decide('CASE-HV',Classification(intent='unauthorized',confidence=1,source='llm'),f,po,txn=high); assert r.human_review_required; assert 'HIGH_VALUE' in r.escalation_reasons

def test_high_abuse_requires_review():
 p=CustomerProfile(customer_id='C-1001',known_device_ids=['dev-1'],typical_categories=['groceries']); prior=[PriorDispute(dispute_id=str(i),customer_id='C-1001',transaction_id='x',dispute_type='x',outcome='rejected') for i in range(5)]; f=FraudEngine().score(tx(),[tx('C-1001','H1'),tx('C-1001','H2'),tx('C-1001','H3')],p,prior); po=PolicyEngine().evaluate(Classification(intent='unauthorized',confidence=1,source='llm'),tx(),f); r=DecisionEngine().decide('CASE-1',Classification(intent='unauthorized',confidence=1,source='llm'),f,po,txn=tx()); assert r.human_review_required


def test_velocity_uses_actual_time_window():
    from datetime import timedelta
    base = tx()
    near = base.model_copy(update={"transaction_id": "T-NEAR", "timestamp": "2026-01-01T00:10:00Z"})
    far = base.model_copy(update={"transaction_id": "T-FAR", "timestamp": "2025-12-31T23:00:00Z"})
    p = CustomerProfile(customer_id='C-1001', known_device_ids=['dev-1'], typical_categories=['groceries'])
    result = FraudEngine().score(base, [base, near, far], p, [])
    assert result.behavioral.transactions_last_15m == 1
    assert result.behavioral.transactions_last_1h == 1
