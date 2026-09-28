import pytest

from app.core.semantic import FakeProvider, ProviderError, SemanticGateway


def test_classification_logical_budget_is_separate_from_provider_attempts():
    gateway = SemanticGateway("fake", classification_budget=2, max_provider_calls=8)
    gateway.classify("I do not recognize this transaction", run_id="RUN-BUDGET")
    gateway.classify("I do not recognize this transaction", run_id="RUN-BUDGET")
    with pytest.raises(ProviderError, match="PROV_TASK_BUDGET_EXHAUSTED"):
        gateway.classify("I do not recognize this transaction", run_id="RUN-BUDGET")


def test_fallback_records_primary_failure_and_secondary_success(monkeypatch):
    gateway = SemanticGateway("fake", classification_budget=2, max_provider_calls=8)

    def fail(_text, _context=None):
        raise ProviderError("PRIMARY_TEST_FAILURE")

    monkeypatch.setattr(gateway.primary, "classify", fail)
    result, attempts = gateway.classify("I do not recognize this transaction", run_id="RUN-FALLBACK")
    assert result.intent == "unauthorized"
    assert attempts[0]["status"] == "FAILED"
    assert attempts[-1]["status"] == "SUCCESS"
    assert gateway.last_provider == "fake-fallback"
