from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from app.models import Classification


class DisputeClassificationAgent:
    """Semantic classifier. It interprets language; it never chooses a financial action."""

    def __init__(self, semantic):
        self.semantic = semantic

    def run(
        self,
        masked_text: str,
        conversation_context: list[str] | None = None,
        claim_hints: dict[str, Any] | None = None,
        run_id: str | None = None,
    ) -> Classification:
        hints = claim_hints or {}
        hint_tx = hints.get("transaction_id")
        m = re.match(r"\s*(?:show|get)\s+transaction\s+(T-[\w-]+)\s*$", masked_text, re.I)
        if m:
            return Classification(
                intent="transaction_query",
                confidence=.99,
                source="fast_path",
                transaction_id=m.group(1).upper(),
            )

        result = self.semantic.classify(
            masked_text,
            conversation_context,
            run_id=run_id,
        )[0]
        if hint_tx:
            # Selection from the trusted banking UI is treated as a case-bound hint; MCP ownership is still enforced.
            result = result.model_copy(update={"transaction_id": hint_tx})
        if result.claimed_amount is None and hints.get("claimed_amount") is not None:
            result = result.model_copy(update={"claimed_amount": Decimal(str(hints["claimed_amount"]))})
        if result.claimed_merchant is None and hints.get("claimed_merchant"):
            result = result.model_copy(update={"claimed_merchant": str(hints["claimed_merchant"])})
        return result


class FraudScoringAgent:
    def __init__(self, fraud_engine):
        self.engine = fraud_engine

    def run(self, txn, history, profile, prior):
        return self.engine.score(txn, history, profile, prior)


class ChargebackRulesAgent:
    def __init__(self, rag, policy):
        self.rag = rag
        self.policy = policy

    def retrieve(self, query, run_id: str | None = None):
        return self.rag.search(query, run_id=run_id)
