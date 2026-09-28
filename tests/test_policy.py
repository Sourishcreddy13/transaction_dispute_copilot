from decimal import Decimal

from app.core.engines import DecisionEngine, FraudEngine, PolicyEngine
from app.core.rag import PolicyRAG
from app.models import Classification, CustomerProfile, PriorDispute, Transaction


def tx(cid='C-1001', tid='T') -> Transaction:
    return Transaction(
        transaction_id=tid,
        customer_id=cid,
        amount='1299',
        currency='INR',
        merchant='Metro',
        category='groceries',
        device_id='dev-1',
        timestamp='2026-01-01T00:00:00Z',
        status='settled',
        country='IN',
        channel='pos',
        card_present=True,
        authentication='pin',
        ip_country='IN',
    )


def policy_evidence(query: str) -> list[dict]:
    rag = PolicyRAG(mode='local')
    rag.index_directory('data/policy_corpus')
    return rag.search(query, k=3)


def policy_for(classification: Classification, txn: Transaction, fraud):
    if fraud.claim_abuse_score >= 0.75:
        query = (
            "abuse review repeated rejected disputes "
            "claim abuse deny investigation"
        )
    elif classification.intent == "merchant_dispute":
        query = (
            "merchant dispute chargeback network rule "
            "merchant transaction"
        )
    else:
        query = (
            "unauthorized transaction provisional credit "
            "fraud dispute"
        )

    sources = policy_evidence(query)

    return PolicyEngine().evaluate(
        classification,
        txn,
        fraud,
        rag_sources=sources,
    )


def test_unauthorized_provisional_credit():
    profile = CustomerProfile(
        customer_id='C-1001',
        home_country='IN',
        known_device_ids=['dev-1'],
        typical_categories=['groceries'],
    )
    transaction = tx()
    history = [tx('C-1001', 'H1'), tx('C-1001', 'H2'), tx('C-1001', 'H3')]
    fraud = FraudEngine().score(transaction, history, profile, [])
    classification = Classification(intent='unauthorized', confidence=1, source='llm')
    policy = policy_for(classification, transaction, fraud)
    recommendation = DecisionEngine().decide('CASE-1', classification, fraud, policy, txn=transaction)
    assert recommendation.primary_action.value == 'provisional_credit'
    assert recommendation.auto_disposition_eligible


def test_high_value_requires_review():
    profile = CustomerProfile(
        customer_id='C-1001',
        home_country='IN',
        known_device_ids=['dev-1'],
        typical_categories=['groceries'],
    )
    high = tx().model_copy(update={'amount': Decimal('60000.00')})
    history = [tx('C-1001', 'H1'), tx('C-1001', 'H2'), tx('C-1001', 'H3')]
    fraud = FraudEngine().score(high, history, profile, [])
    classification = Classification(intent='unauthorized', confidence=1, source='llm')
    policy = policy_for(classification, high, fraud)
    recommendation = DecisionEngine().decide('CASE-HV', classification, fraud, policy, txn=high)
    assert recommendation.human_review_required
    assert 'HIGH_VALUE' in recommendation.escalation_reasons


def test_high_abuse_requires_review():
    profile = CustomerProfile(
        customer_id='C-1001',
        home_country='IN',
        known_device_ids=['dev-1'],
        typical_categories=['groceries'],
    )
    transaction = tx()
    prior = [
        PriorDispute(
            dispute_id=str(i),
            customer_id='C-1001',
            transaction_id='x',
            dispute_type='x',
            outcome='rejected',
        )
        for i in range(5)
    ]
    fraud = FraudEngine().score(transaction, [tx('C-1001', 'H1'), tx('C-1001', 'H2'), tx('C-1001', 'H3')], profile, prior)
    classification = Classification(intent='unauthorized', confidence=1, source='llm')
    policy = policy_for(classification, transaction, fraud)
    recommendation = DecisionEngine().decide('CASE-ABUSE', classification, fraud, policy, txn=transaction)
    assert recommendation.human_review_required


def test_velocity_uses_actual_time_window():
    from datetime import timedelta
    base = tx()
    near = base.model_copy(update={'transaction_id': 'T-NEAR', 'timestamp': '2026-01-01T00:10:00Z'})
    far = base.model_copy(update={'transaction_id': 'T-FAR', 'timestamp': '2025-12-31T23:00:00Z'})
    profile = CustomerProfile(
        customer_id='C-1001',
        home_country='IN',
        known_device_ids=['dev-1'],
        typical_categories=['groceries'],
    )
    result = FraudEngine().score(base, [base, near, far], profile, [])
    assert result.behavioral.transactions_last_15m == 1
    assert result.behavioral.transactions_last_1h == 1
