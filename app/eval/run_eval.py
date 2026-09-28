from __future__ import annotations

import copy
import json
from decimal import Decimal
from pathlib import Path

from app.core.engines import DecisionEngine, FraudEngine, PolicyEngine
from app.core.rag import PolicyRAG
from app.models import Classification, PriorDispute, Transaction
from app.workflow import Copilot

ROOT = Path(__file__).resolve().parents[2]


def evaluate_cases(cop: Copilot, cases: list[dict], suite_name: str) -> list[dict]:
    results: list[dict] = []
    for item in cases:
        cid = cop.db.create_case(item["customer_id"], "analyst:A-001", item.get("transaction_id"))
        out = cop.run(cid, "analyst:A-001", item["text"])
        actual_action = out.recommendation.primary_action.value if out.recommendation else "needs_info"
        actual_review = bool(out.recommendation and out.recommendation.human_review_required)
        actual_intent = (out.analyst_view.get("classification") or {}).get("intent")
        expected_review = bool(item.get("expected_human_review", False))
        action_ok = actual_action == item["expected_action"]
        review_ok = actual_review == expected_review
        intent_ok = actual_intent == item.get("expected_intent", actual_intent)
        results.append({
            "suite": suite_name,
            "id": item["id"],
            "case_id": cid,
            "expected_intent": item.get("expected_intent"),
            "actual_intent": actual_intent,
            "expected_action": item["expected_action"],
            "actual_action": actual_action,
            "expected_human_review": expected_review,
            "actual_human_review": actual_review,
            "action_pass": action_ok,
            "review_pass": review_ok,
            "intent_pass": intent_ok,
            "pass": action_ok and review_ok and intent_ok,
            "provider_attempts": out.provenance.provider_attempts if out.provenance else [],
            "policy_citation": out.recommendation.policy.citation if out.recommendation else None,
            "customer_view": out.customer_view,
        })
    return results


def mutation_results() -> list[dict]:
    """Causal mutation checks using the deterministic local policy retriever; no LLM is used."""
    from app.core.data_plane import DataPlane

    data = DataPlane()
    p = data.profiles["C-1001"]
    base = data.txns["T-1007"]
    engine = FraudEngine()
    decision = DecisionEngine()
    policy = PolicyEngine()
    retriever = PolicyRAG(mode="local")
    retriever.index_directory("data/policy_corpus")
    outputs: list[dict] = []

    high = base.model_copy(update={"amount": Decimal("60000.00")})
    fraud = engine.score(high, [base], p, [])
    classification = Classification(intent="unauthorized", confidence=1, source="llm")
    rag_hits = retriever.search("unauthorized transaction provisional credit settled", k=3)
    po = policy.evaluate(classification, high, fraud, rag_sources=rag_hits)
    rec = decision.decide("M-HIGH", Classification(intent="unauthorized", confidence=1, source="llm"), fraud, po, txn=high)
    outputs.append({"id": "M-HIGH-VALUE", "assertion": "HIGH_VALUE in escalation_reasons", "pass": "HIGH_VALUE" in rec.escalation_reasons})

    new_device = base.model_copy(update={"device_id": "unknown-device"})
    fraud2 = engine.score(new_device, [base], p, [])
    outputs.append({"id": "M-NEW-DEVICE", "assertion": "device_known is False", "pass": fraud2.device_network.device_known is False})

    prior = [PriorDispute(dispute_id=str(i), customer_id="C-1001", transaction_id="x", dispute_type="x", outcome="rejected") for i in range(5)]
    fraud3 = engine.score(base, [base], p, prior)
    outputs.append({"id": "M-ABUSE", "assertion": "claim_abuse_score >= 0.75", "pass": fraud3.claim_abuse_score >= 0.75})
    return outputs


def run() -> dict:
    cop = Copilot()
    public = json.loads((ROOT / "data/eval/cases.json").read_text(encoding="utf-8"))
    hidden = json.loads((ROOT / "data/eval/hidden.json").read_text(encoding="utf-8"))
    results = evaluate_cases(cop, public, "public") + evaluate_cases(cop, hidden, "hidden")
    mutations = mutation_results()

    total = len(results)
    report = {
        "deterministic_evaluation": {
            "cases": total,
            "pass_rate": sum(r["pass"] for r in results) / total if total else 0.0,
            "action_accuracy": sum(r["action_pass"] for r in results) / total if total else 0.0,
            "review_accuracy": sum(r["review_pass"] for r in results) / total if total else 0.0,
            "intent_accuracy": sum(r["intent_pass"] for r in results) / total if total else 0.0,
            "results": results,
        },
        "mutation_evaluation": {
            "cases": len(mutations),
            "pass_rate": sum(x["pass"] for x in mutations) / len(mutations) if mutations else 0.0,
            "results": mutations,
        },
        "evaluation_design": {
            "decision_correctness_authority": "deterministic reference assertions",
            "hidden_cases": "evaluated from data/eval/hidden.json; expected labels never enter application prompts",
            "mutation_cases": "causal feature mutations; no LLM judge",
            "llm_judge_authority": "qualitative only: hallucination, faithfulness, answer relevance",
            "expected_decision_hidden_from_judge": True,
        },
    }
    out_path = ROOT / "reports/eval_report.json"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))
    return report


if __name__ == "__main__":
    run()
