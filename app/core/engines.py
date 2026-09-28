from __future__ import annotations

import hashlib
import math
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from app.models import (
    Action,
    BehavioralSignals,
    CaseRiskContext,
    Classification,
    DecisionRecommendation,
    DeviceNetworkSignals,
    FraudResult,
    IdentityRiskSignals,
    PolicyOutcome,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load_yaml(name: str) -> dict[str, Any]:
    path = PROJECT_ROOT / "config" / name
    return yaml.safe_load(path.read_text(encoding="utf-8"))


class FraudEngine:
    def __init__(self) -> None:
        self.cfg = _load_yaml("fraud.yaml")
        weights = self.cfg["weights"]
        if "velocity_15m_anomaly" not in weights:
            weights["velocity_15m_anomaly"] = 0.10

    @staticmethod
    def _history_window(history: list[Any], tx_time: datetime, days: int) -> list[Any]:
        cutoff = tx_time - timedelta(days=days)
        return [
            item
            for item in history
            if cutoff
            <= datetime.fromisoformat(item.timestamp.replace("Z", "+00:00"))
            <= tx_time
        ]

    def score(self, txn: Any, history: list[Any], profile: Any, prior: list[Any]) -> FraudResult:
        history = list(history)
        prior = list(prior)
        observations = [x for x in history if x.transaction_id != txn.transaction_id]
        tx_time = datetime.fromisoformat(txn.timestamp.replace("Z", "+00:00"))

        history_30d = [
            x
            for x in self._history_window(history, tx_time, 30)
            if x.transaction_id != txn.transaction_id
        ]
        amounts = sorted((x.amount for x in history_30d), key=Decimal)
        median = amounts[len(amounts) // 2] if amounts else Decimal("1.00")
        median = max(Decimal(str(median)), Decimal("1.00"))

        amount_anomaly = min(
            float(Decimal(txn.amount) / max(median * Decimal("4"), Decimal("1"))),
            1.0,
        )

        prior_events: list[datetime] = []
        nearby_events: list[datetime] = []
        for item in observations:
            try:
                event_time = datetime.fromisoformat(item.timestamp.replace("Z", "+00:00"))
            except ValueError:
                continue
            if event_time <= tx_time:
                prior_events.append(event_time)
            # Symmetric 15-minute burst detection tolerates ordering/clock skew.
            if abs(tx_time - event_time) <= timedelta(minutes=15):
                nearby_events.append(event_time)

        velocity_15m_count = len(nearby_events)
        velocity_1h_count = sum(
            1
            for event_time in prior_events
            if timedelta(0) <= tx_time - event_time <= timedelta(hours=1)
        )
        velocity_15m = min(velocity_15m_count / 5, 1.0)
        velocity_1h = min(velocity_1h_count / 10, 1.0)

        geographic_anomaly = float(
            txn.country != profile.home_country
            or (txn.ip_country is not None and txn.ip_country != profile.home_country)
        )
        merchant_anomaly = float(txn.category not in profile.typical_categories)
        device_known = txn.device_id is not None and txn.device_id in profile.known_device_ids
        device_anomaly = float(txn.device_id is not None and not device_known)
        hour = int(txn.timestamp[11:13]) if len(txn.timestamp) >= 13 else 12
        temporal_anomaly = float(txn.channel == "ecommerce" and hour in {0, 1, 2, 3, 4, 5})
        cnp_no_auth = float((not txn.card_present) and txn.authentication == "none")

        xs = {
            "amount_anomaly": amount_anomaly,
            "velocity_anomaly": velocity_1h,
            "velocity_15m_anomaly": velocity_15m,
            "geographic_anomaly": geographic_anomaly,
            "merchant_anomaly": merchant_anomaly,
            "device_anomaly": device_anomaly,
            "temporal_anomaly": temporal_anomaly,
            "card_not_present_no_auth": cnp_no_auth,
        }
        score = 1 - math.prod(
            1 - self.cfg["weights"].get(key, 0.0) * value for key, value in xs.items()
        )

        hard_flags: list[str] = []
        if geographic_anomaly and device_anomaly:
            hard_flags.append("GEO_DEVICE_MISMATCH")
            score = max(score, 0.78)
        if cnp_no_auth and amount_anomaly >= 0.75:
            hard_flags.append("CNP_HIGH_VALUE_NO_AUTH")
            score = max(score, 0.82)

        factors = [key for key, value in xs.items() if value >= 0.5] + hard_flags
        abuse = min(
            1.0,
            len(prior) / 5
            + 0.35 * (1 if any(d.outcome == "rejected" for d in prior) else 0),
        )
        level = (
            "high"
            if score >= self.cfg["high_score"]
            else "medium"
            if score >= self.cfg["medium_score"]
            else "low"
        )
        suff = "high" if len(observations) >= 3 else "low"

        behavioral = BehavioralSignals(
            transactions_last_15m=velocity_15m_count,
            transactions_last_1h=velocity_1h_count,
            avg_transaction_amount_30d=(
                sum((x.amount for x in history_30d), Decimal("0")) / len(history_30d)
                if history_30d
                else None
            ),
            median_transaction_amount_30d=median if history_30d else None,
        )
        device_network = DeviceNetworkSignals(
            device_known=device_known,
            ip_country=txn.ip_country,
            ip_risk_score=0.9
            if txn.ip_country and txn.ip_country != profile.home_country
            else 0.05,
            proxy_detected=None,
            vpn_detected=None,
        )
        identity = IdentityRiskSignals(
            identity_verified=profile.identity_verified,
            account_age_days=profile.tenure_days,
        )
        case_risk = CaseRiskContext(
            triggered_scenario="UNAUTHORIZED_HIGH_RISK" if level == "high" else None,
            linked_case_count=len(prior),
        )
        return FraudResult(
            transaction_fraud_score=round(score, 4),
            claim_abuse_score=round(abuse, 4),
            risk_level=level,
            factors=factors or ["no_strong_anomaly"],
            data_sufficiency=suff,
            behavioral=behavioral,
            device_network=device_network,
            identity=identity,
            case_risk=case_risk,
        )


class PolicyEngine:
    def __init__(self) -> None:
        self.cfg = _load_yaml("decision_policy.yaml")

    def evaluate(
        self,
        classification: Classification,
        txn: Any,
        fraud: FraudResult,
        evidence_complete: bool = True,
        rag_sources: list[dict[str, Any]] | None = None,
    ) -> PolicyOutcome:
        dispute_type = classification.intent
        if (
            dispute_type == "unauthorized"
            and txn.status == "settled"
            and evidence_complete
            and fraud.claim_abuse_score < self.cfg["high_abuse_score"]
        ):
            rule = self.cfg["rules"][0]
        elif dispute_type == "merchant_dispute" and evidence_complete:
            rule = self.cfg["rules"][1]
        elif fraud.claim_abuse_score >= self.cfg["high_abuse_score"]:
            rule = self.cfg["rules"][2]
        else:
            rule = self.cfg["rules"][3]

        sources = rag_sources or []
        expected_name = Path(rule["citation"]).name
        matching_hit = next(
            (
                hit
                for hit in sources
                if Path(str(hit.get("source", ""))).name == expected_name
            ),
            None,
        )
        matched = matching_hit is not None

        return PolicyOutcome(
            rule_id=rule["id"],
            eligible_actions=[Action(value) for value in rule["eligible_actions"]],
            primary_action=Action(rule["primary_action"]),
            human_review_required=bool(rule["human_review"]),
            citation=matching_hit["source"] if matching_hit else rule["citation"],
            matched=matched,
            policy_version=self.cfg["version"],
            retrieved_sources=[str(hit.get("source")) for hit in sources],
        )


class DecisionEngine:
    def __init__(self) -> None:
        self.cfg = _load_yaml("decision_policy.yaml")

    def decide(
        self,
        case_id: str,
        classification: Classification,
        fraud: FraudResult,
        policy: PolicyOutcome,
        txn: Any | None = None,
        evidence_complete: bool = True,
    ) -> DecisionRecommendation:
        if not policy.matched:
            raise ValueError("POLICY_EVIDENCE_NOT_RETRIEVED")

        reasons: list[str] = []
        if fraud.risk_level == "high":
            reasons.append("HIGH_TRANSACTION_FRAUD_RISK")
        if txn is not None and txn.amount >= Decimal(str(self.cfg["high_value_threshold"])):
            reasons.append("HIGH_VALUE")
        if fraud.data_sufficiency == "low" and fraud.transaction_fraud_score >= 0.45:
            reasons.append("LOW_DATA_SUFFICIENCY")
        if fraud.claim_abuse_score >= self.cfg["high_abuse_score"]:
            reasons.append("HIGH_CLAIM_ABUSE_RISK")
        if classification.intent in ("ambiguous", "out_of_scope"):
            reasons.append("AMBIGUOUS_INTENT")
        if policy.human_review_required and "HIGH_CLAIM_ABUSE_RISK" not in reasons:
            reasons.append("POLICY_REVIEW")

        human = policy.human_review_required or bool(reasons) or policy.primary_action == Action.deny
        auto = (
            not human
            and policy.primary_action
            in {Action.provisional_credit, Action.chargeback, Action.investigate}
        )
        recommendation_id = "REC-" + hashlib.sha256(
            f"{case_id}:{classification.model_dump_json()}:{fraud.model_dump_json()}".encode()
        ).hexdigest()[:12]
        rationale = (
            f"Policy {policy.rule_id} matched and was retrieved from the committed policy corpus. "
            f"Fraud risk is {fraud.risk_level} ({fraud.transaction_fraud_score:.2f})."
        )
        if reasons:
            rationale += " Escalation: " + ", ".join(reasons) + "."

        return DecisionRecommendation(
            recommendation_id=recommendation_id,
            case_id=case_id,
            primary_action=policy.primary_action,
            additional_actions=[
                Action.investigate
            ]
            if "HIGH_TRANSACTION_FRAUD_RISK" in reasons
            and policy.primary_action == Action.provisional_credit
            else [],
            human_review_required=human,
            escalation_reasons=reasons,
            citations=[policy.citation] if policy.matched else [],
            fraud=fraud,
            policy=policy,
            evidence_completeness=1.0 if evidence_complete else 0.5,
            auto_disposition_eligible=auto,
            rationale=rationale,
        )


class InvariantEngine:
    def validate(self, recommendation: DecisionRecommendation) -> None:
        errors: list[str] = []
        if not recommendation.policy.matched:
            errors.append("INV-006")
        if recommendation.primary_action not in recommendation.policy.eligible_actions:
            errors.append("INV-001")
        if (
            recommendation.primary_action == Action.provisional_credit
            and (
                recommendation.evidence_completeness < 0.7
                or recommendation.fraud.claim_abuse_score >= 0.75
            )
            and not recommendation.human_review_required
        ):
            errors.append("INV-002")
        if recommendation.primary_action == Action.chargeback and not recommendation.citations:
            errors.append("INV-003")
        if recommendation.primary_action == Action.deny and not recommendation.human_review_required:
            errors.append("INV-004")
        if "HIGH_VALUE" in recommendation.escalation_reasons and not recommendation.human_review_required:
            errors.append("INV-012")
        if bool(recommendation.escalation_reasons) != recommendation.human_review_required:
            errors.append("INV-005")
        if errors:
            raise ValueError("INVARIANT_VIOLATION:" + ",".join(errors))
