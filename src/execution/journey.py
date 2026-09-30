from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Any, Literal


JourneyStatus = Literal["executed", "paused", "skipped", "failed"]


NODE_LABELS: dict[str, str] = {
    "case_authorize": "Case Authorization",
    "ingress": "Secure Intake & Context",
    "supervisor": "Workflow Routing",
    "classification_agent": "Dispute Classification",
    "case_data_agent": "Evidence Collection",
    "fraud_scoring_agent": "Fraud Assessment",
    "chargeback_rules_agent": "Policy Verification",
    "decision_engine": "Decision Engine",
    "human_review": "Human Review",
    "disposition_commit": "Review Decision",
    "finalize": "Finalization & Audit",
}

NODE_DESCRIPTIONS: dict[str, str] = {
    "case_authorize": "Checks whether the current analyst is authorized to work on the case.",
    "ingress": "Protects the submitted message and prepares trusted case context.",
    "supervisor": "Determines which investigation stage should run next.",
    "classification_agent": "Identifies the type of dispute from the customer's statement.",
    "case_data_agent": "Retrieves the disputed transaction and supporting customer evidence.",
    "fraud_scoring_agent": "Calculates transaction and claim-abuse risk indicators.",
    "chargeback_rules_agent": "Retrieves and verifies the policy that applies to the dispute.",
    "decision_engine": "Combines evidence, risk and policy into a controlled recommendation.",
    "human_review": "Pauses the workflow when an authorized reviewer must decide the outcome.",
    "disposition_commit": "Applies the reviewer's approved disposition to the case.",
    "finalize": "Validates release controls and records the final case event.",
}

NODE_ORDER = list(NODE_LABELS)


@dataclass
class JourneyEvent:
    node_id: str
    label: str
    status: JourneyStatus
    started_at: float | None = None
    completed_at: float | None = None
    input_summary: str | None = None
    output_summary: str | None = None
    error: str | None = None

    @property
    def duration_ms(self) -> float | None:
        if self.started_at is None or self.completed_at is None:
            return None
        return round((self.completed_at - self.started_at) * 1000, 2)


class ExecutionJourney:
    """Safe, presentation-oriented projection of one real LangGraph execution."""

    def __init__(self, run_id: str):
        self.run_id = run_id
        self.events: list[JourneyEvent] = []
        self.selected_edges: list[dict[str, str]] = []
        self.input_parameters: dict[str, Any] = {}

    def set_inputs(self, input_parameters: dict[str, Any]) -> None:
        """Store only safe, client-facing input metadata; never raw customer text."""
        self.input_parameters = dict(input_parameters)

    def start(self, node_id: str, input_summary: str | None = None) -> None:
        if self.events:
            previous = self.events[-1].node_id
            if previous != node_id:
                edge = {"source": previous, "target": node_id}
                if edge not in self.selected_edges:
                    self.selected_edges.append(edge)

        self.events.append(
            JourneyEvent(
                node_id=node_id,
                label=NODE_LABELS.get(node_id, node_id),
                status="executed",
                started_at=perf_counter(),
                input_summary=input_summary,
            )
        )

    def complete(self, node_id: str, output_summary: str | None = None) -> None:
        for event in reversed(self.events):
            if event.node_id == node_id and event.completed_at is None:
                event.completed_at = perf_counter()
                event.output_summary = output_summary
                return

    def pause(self, node_id: str, output_summary: str | None = None) -> None:
        for event in reversed(self.events):
            if event.node_id == node_id and event.completed_at is None:
                event.status = "paused"
                event.completed_at = perf_counter()
                event.output_summary = output_summary
                return

    def fail(self, node_id: str, error: Exception) -> None:
        for event in reversed(self.events):
            if event.node_id == node_id and event.completed_at is None:
                event.status = "failed"
                event.completed_at = perf_counter()
                event.error = f"{type(error).__name__.upper()}_FAILED"
                return

    def _node_view(self, node_id: str) -> dict[str, Any]:
        events = [event for event in self.events if event.node_id == node_id]
        if not events:
            return {
                "id": node_id,
                "label": NODE_LABELS[node_id],
                "description": NODE_DESCRIPTIONS[node_id],
                "status": "skipped",
                "execution_count": 0,
                "duration_ms": None,
                "input_summary": None,
                "output_summary": None,
                "error": None,
            }

        latest = events[-1]
        durations = [event.duration_ms for event in events if event.duration_ms is not None]
        return {
            "id": node_id,
            "label": NODE_LABELS[node_id],
            "description": NODE_DESCRIPTIONS[node_id],
            "status": latest.status,
            "execution_count": len(events),
            "duration_ms": round(sum(durations), 2) if durations else None,
            "input_summary": latest.input_summary,
            "output_summary": latest.output_summary,
            "error": latest.error,
        }

    def to_dict(self) -> dict[str, Any]:
        executed_path: list[str] = []
        for event in self.events:
            if event.node_id not in executed_path:
                executed_path.append(event.node_id)

        return {
            "run_id": self.run_id,
            "input_parameters": self.input_parameters,
            "nodes": [self._node_view(node_id) for node_id in NODE_ORDER],
            "selected_edges": list(self.selected_edges),
            "executed_path": executed_path,
        }


def input_summary(node_id: str, state: dict[str, Any]) -> str:
    summaries = {
        "case_authorize": "Case ID + analyst authorization context",
        "ingress": "Customer dispute statement + claim fields",
        "supervisor": "Current case state + completed investigation stages",
        "classification_agent": "Masked customer statement + prior conversation context",
        "case_data_agent": "Transaction selection + dispute context",
        "fraud_scoring_agent": "Transaction + recent activity + customer/account signals",
        "chargeback_rules_agent": "Dispute intent + masked dispute context",
        "decision_engine": "Fraud assessment + verified policy + transaction evidence",
        "human_review": "Recommendation requiring authorized human review",
        "disposition_commit": "Reviewer-approved case disposition",
        "finalize": "Recommendation/review state + release controls",
    }
    return summaries.get(node_id, "Case workflow state")


def output_summary(node_id: str, result: dict[str, Any]) -> str | None:
    if node_id == "case_authorize":
        return "Case authorization accepted" if result.get("authorized") else "Case authorization denied"

    if node_id == "ingress":
        return "Input isolated; context prepared; memory recalled"

    if node_id == "supervisor":
        return f"Next stage: {result.get('route', 'unknown')}"

    if node_id == "classification_agent":
        value = result.get("classification")
        intent = getattr(value, "intent", None)
        confidence = getattr(value, "confidence", None)
        if intent:
            return f"Intent: {intent}; confidence: {confidence:.2f}" if isinstance(confidence, (int, float)) else f"Intent: {intent}"

    if node_id == "case_data_agent":
        count = len(result.get("history", []))
        source = result.get("selected_transaction_source", "unknown")
        return f"Transaction evidence retrieved; history records: {count}; source: {source}"

    if node_id == "fraud_scoring_agent":
        value = result.get("fraud")
        score = getattr(value, "transaction_fraud_score", None)
        abuse = getattr(value, "claim_abuse_score", None)
        risk = getattr(value, "risk_level", None)
        if score is not None and abuse is not None:
            return f"Fraud risk: {str(risk).lower()}; score: {float(score):.2f}; claim abuse: {float(abuse):.2f}"

    if node_id == "chargeback_rules_agent":
        policy = result.get("policy")
        rule_id = getattr(policy, "rule_id", None)
        matched = getattr(policy, "matched", None)
        sources = len(result.get("policy_hits", []))
        if rule_id:
            return f"Policy: {rule_id}; matched: {bool(matched)}; sources retrieved: {sources}"
        return f"Policy sources retrieved: {sources}"

    if node_id == "decision_engine":
        recommendation = result.get("recommendation")
        action = getattr(recommendation, "primary_action", None)
        review = getattr(recommendation, "human_review_required", None)
        if action is not None:
            action_value = getattr(action, "value", action)
            return f"Recommendation: {action_value}; human review: {'yes' if review else 'no'}"

    if node_id == "human_review":
        return "Workflow paused awaiting authorized reviewer"

    if node_id == "disposition_commit":
        disposition = result.get("disposition") or {}
        action = disposition.get("action") if isinstance(disposition, dict) else None
        return f"Reviewer disposition: {action}" if action else "Reviewer disposition committed"

    if node_id == "finalize":
        case_state = result.get("case_state", "unknown")
        release_ok = result.get("audit_state") == "RELEASABLE" and case_state != "FAILED"
        return f"Case state: {case_state}; release gate: {'passed' if release_ok else 'blocked'}"

    return None
