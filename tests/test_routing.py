import asyncio
from types import SimpleNamespace

from src.graph import CopilotGraph


def test_supervisor_routes_classification_first():
    g = object.__new__(CopilotGraph)
    result = asyncio.run(g.supervisor({"case_state": "CLASSIFIED", "classification": None}))
    assert result["route"] == "classification_agent"


def test_supervisor_routes_needs_info_to_finalize():
    g = object.__new__(CopilotGraph)
    result = asyncio.run(g.supervisor({"case_state": "NEEDS_INFO"}))
    assert result["route"] == "finalize"


def test_supervisor_routes_failed_to_finalize():
    g = object.__new__(CopilotGraph)
    result = asyncio.run(g.supervisor({"case_state": "FAILED"}))
    assert result["route"] == "finalize"


def test_supervisor_short_circuits_on_injection_flag():
    """The injection_flag check runs before every other branch below it -- this
    is the routing half of CTRL-GUARD-001 (see docs/controls.md): a flagged
    customer message must never reach a tool-calling or decision node, no
    matter how far along the case otherwise is."""
    g = object.__new__(CopilotGraph)
    result = asyncio.run(
        g.supervisor({"case_state": "CLASSIFIED", "injection_flag": True, "classification": object()})
    )
    assert result["route"] == "finalize"
    assert "SECURITY_FLAG" in result["errors"]


def test_supervisor_routes_ambiguous_intent_to_finalize_needs_info():
    g = object.__new__(CopilotGraph)
    classification = SimpleNamespace(intent="ambiguous")
    result = asyncio.run(g.supervisor({"case_state": "CLASSIFIED", "classification": classification}))
    assert result["route"] == "finalize"
    assert result["case_state"] == "NEEDS_INFO"


def test_supervisor_routes_out_of_scope_intent_to_finalize_needs_info():
    g = object.__new__(CopilotGraph)
    classification = SimpleNamespace(intent="out_of_scope")
    result = asyncio.run(g.supervisor({"case_state": "CLASSIFIED", "classification": classification}))
    assert result["route"] == "finalize"
    assert result["case_state"] == "NEEDS_INFO"


def test_supervisor_routes_policy_query_without_hits_to_chargeback_rules_agent():
    g = object.__new__(CopilotGraph)
    classification = SimpleNamespace(intent="policy_query")
    result = asyncio.run(
        g.supervisor({"case_state": "CLASSIFIED", "classification": classification, "policy_hits": None})
    )
    assert result["route"] == "chargeback_rules_agent"


def test_supervisor_routes_policy_query_with_hits_to_finalize_resolved():
    g = object.__new__(CopilotGraph)
    classification = SimpleNamespace(intent="policy_query")
    result = asyncio.run(
        g.supervisor({"case_state": "CLASSIFIED", "classification": classification, "policy_hits": ["hit"]})
    )
    assert result["route"] == "finalize"
    assert result["case_state"] == "RESOLVED"


def test_supervisor_routes_missing_transaction_to_case_data_agent():
    g = object.__new__(CopilotGraph)
    classification = SimpleNamespace(intent="unauthorized")
    result = asyncio.run(g.supervisor({"case_state": "CLASSIFIED", "classification": classification}))
    assert result["route"] == "case_data_agent"


def test_supervisor_routes_missing_fraud_to_fraud_scoring_agent():
    g = object.__new__(CopilotGraph)
    classification = SimpleNamespace(intent="unauthorized")
    result = asyncio.run(
        g.supervisor(
            {"case_state": "CLASSIFIED", "classification": classification, "transaction": {"id": "T-1"}}
        )
    )
    assert result["route"] == "fraud_scoring_agent"


def test_supervisor_routes_missing_policy_to_chargeback_rules_agent():
    g = object.__new__(CopilotGraph)
    classification = SimpleNamespace(intent="unauthorized")
    result = asyncio.run(
        g.supervisor(
            {
                "case_state": "CLASSIFIED",
                "classification": classification,
                "transaction": {"id": "T-1"},
                "fraud": SimpleNamespace(score=0.1),
            }
        )
    )
    assert result["route"] == "chargeback_rules_agent"


def test_supervisor_routes_missing_recommendation_to_decision_engine():
    g = object.__new__(CopilotGraph)
    classification = SimpleNamespace(intent="unauthorized")
    result = asyncio.run(
        g.supervisor(
            {
                "case_state": "CLASSIFIED",
                "classification": classification,
                "transaction": {"id": "T-1"},
                "fraud": SimpleNamespace(score=0.1),
                "policy": SimpleNamespace(citation="RULE-1"),
            }
        )
    )
    assert result["route"] == "decision_engine"


def test_supervisor_routes_human_review_required_without_resume_to_human_review():
    g = object.__new__(CopilotGraph)
    classification = SimpleNamespace(intent="unauthorized")
    result = asyncio.run(
        g.supervisor(
            {
                "case_state": "CLASSIFIED",
                "classification": classification,
                "transaction": {"id": "T-1"},
                "fraud": SimpleNamespace(score=0.9),
                "policy": SimpleNamespace(citation="RULE-1"),
                "recommendation": SimpleNamespace(human_review_required=True),
            }
        )
    )
    assert result["route"] == "human_review"


def test_supervisor_routes_human_review_resume_to_disposition_commit():
    g = object.__new__(CopilotGraph)
    classification = SimpleNamespace(intent="unauthorized")
    result = asyncio.run(
        g.supervisor(
            {
                "case_state": "CLASSIFIED",
                "classification": classification,
                "transaction": {"id": "T-1"},
                "fraud": SimpleNamespace(score=0.9),
                "policy": SimpleNamespace(citation="RULE-1"),
                "recommendation": SimpleNamespace(human_review_required=True),
                "review_resume": True,
            }
        )
    )
    assert result["route"] == "disposition_commit"


def test_supervisor_routes_fully_resolved_case_to_finalize():
    g = object.__new__(CopilotGraph)
    classification = SimpleNamespace(intent="unauthorized")
    result = asyncio.run(
        g.supervisor(
            {
                "case_state": "CLASSIFIED",
                "classification": classification,
                "transaction": {"id": "T-1"},
                "fraud": SimpleNamespace(score=0.1),
                "policy": SimpleNamespace(citation="RULE-1"),
                "recommendation": SimpleNamespace(human_review_required=False),
            }
        )
    )
    assert result["route"] == "finalize"
    assert result["case_state"] == "RESOLVED"
