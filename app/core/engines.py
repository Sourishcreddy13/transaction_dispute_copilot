from __future__ import annotations
from pathlib import Path
from datetime import datetime, timedelta
import hashlib,yaml,math
from decimal import Decimal
from app.models import *

class FraudEngine:
    def __init__(self):
        self.cfg = yaml.safe_load(Path('config/fraud.yaml').read_text())

    def score(self, txn, history, profile, prior):
        history = list(history)
        prior = list(prior)
        observations = [x for x in history if x.transaction_id != txn.transaction_id]
        amounts = sorted((x.amount for x in observations), key=lambda v: Decimal(v))
        median = amounts[len(amounts) // 2] if amounts else Decimal('1.00')
        median = max(Decimal(str(median)), Decimal('1.00'))

        amount_anomaly = min(float(Decimal(txn.amount) / max(median * Decimal('4'), Decimal('1'))), 1.0)
        tx_time = datetime.fromisoformat(txn.timestamp.replace('Z', '+00:00'))
        prior_events = []
        nearby_events = []
        for x in history:
            if x.transaction_id == txn.transaction_id:
                continue
            try:
                xt = datetime.fromisoformat(x.timestamp.replace('Z', '+00:00'))
            except ValueError:
                continue
            if xt <= tx_time:
                prior_events.append(xt)
            # A short burst window is checked both ways in time: near-simultaneous
            # transactions can be persisted/observed in either order (clock skew,
            # async writers), so a same-card charge 2 minutes "after" this one is
            # just as much a velocity signal as one 2 minutes before it.
            if abs(tx_time - xt) <= timedelta(minutes=15):
                nearby_events.append(xt)
        velocity_15m_count = len(nearby_events)
        velocity_1h_count = sum(1 for xt in prior_events if timedelta(minutes=0) <= (tx_time - xt) <= timedelta(hours=1))
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
        temporal_anomaly = float(txn.channel == 'ecommerce' and hour in {0,1,2,3,4,5})
        cnp_no_auth = float((not txn.card_present) and txn.authentication == 'none')

        xs = {
            'amount_anomaly': amount_anomaly,
            'velocity_anomaly': velocity_1h,
            'geographic_anomaly': geographic_anomaly,
            'merchant_anomaly': merchant_anomaly,
            'device_anomaly': device_anomaly,
            'temporal_anomaly': temporal_anomaly,
            'card_not_present_no_auth': cnp_no_auth,
        }
        score = 1 - math.prod(1 - self.cfg['weights'][k] * v for k, v in xs.items())
        hard_flags = []
        if geographic_anomaly and device_anomaly:
            hard_flags.append('GEO_DEVICE_MISMATCH')
            score = max(score, 0.78)
        if cnp_no_auth and amount_anomaly >= 0.75:
            hard_flags.append('CNP_HIGH_VALUE_NO_AUTH')
            score = max(score, 0.82)

        factors = [k for k, v in xs.items() if v >= .5] + hard_flags
        abuse = min(1.0, len(prior) / 5 + .35 * (1 if any(d.outcome == 'rejected' for d in prior) else 0))
        level = 'high' if score >= self.cfg['high_score'] else 'medium' if score >= self.cfg['medium_score'] else 'low'
        suff = 'high' if len(observations) >= 3 else 'low'

        behavioral = BehavioralSignals(
            transactions_last_15m=velocity_15m_count,
            transactions_last_1h=velocity_1h_count,
            avg_transaction_amount_30d=(sum((x.amount for x in history), Decimal('0')) / len(history)) if history else None,
            median_transaction_amount_30d=median if history else None,
        )
        device_network = DeviceNetworkSignals(
            device_known=device_known,
            ip_country=txn.ip_country,
            ip_risk_score=0.9 if txn.ip_country and txn.ip_country != profile.home_country else 0.05,
            proxy_detected=None,
            vpn_detected=None,
        )
        identity = IdentityRiskSignals(
            identity_verified=profile.identity_verified,
            account_age_days=profile.tenure_days,
        )
        case_risk = CaseRiskContext(
            triggered_scenario='UNAUTHORIZED_HIGH_RISK' if level == 'high' else None,
            linked_case_count=len(prior),
        )
        return FraudResult(
            transaction_fraud_score=round(score, 4),
            claim_abuse_score=round(abuse, 4),
            risk_level=level,
            factors=factors or ['no_strong_anomaly'],
            data_sufficiency=suff,
            behavioral=behavioral,
            device_network=device_network,
            identity=identity,
            case_risk=case_risk,
        )


class PolicyEngine:
    def __init__(self): self.cfg=yaml.safe_load(Path('config/decision_policy.yaml').read_text())
    def evaluate(self,classification,txn,fraud,evidence_complete=True,rag_sources=None):
        typ=classification.intent
        if typ=='unauthorized' and txn.status=='settled' and evidence_complete and fraud.claim_abuse_score < self.cfg['high_abuse_score']: r=self.cfg['rules'][0]
        elif typ=='merchant_dispute' and evidence_complete: r=self.cfg['rules'][1]
        elif fraud.claim_abuse_score>=self.cfg['high_abuse_score']: r=self.cfg['rules'][2]
        else: r=self.cfg['rules'][3]
        expected_name = Path(r['citation']).name
        citation = next((h['source'] for h in (rag_sources or []) if Path(h['source']).name == expected_name), r['citation'])
        return PolicyOutcome(
            rule_id=r['id'],
            eligible_actions=[Action(x) for x in r['eligible_actions']],
            primary_action=Action(r['primary_action']),
            human_review_required=bool(r['human_review']),
            citation=citation,
            matched=True,
            policy_version=self.cfg['version'],
            retrieved_sources=[h['source'] for h in (rag_sources or [])],
        )

class DecisionEngine:
    def __init__(self): self.cfg=yaml.safe_load(Path('config/decision_policy.yaml').read_text())
    def decide(self,case_id,classification,fraud,policy,txn=None,evidence_complete=True):
        reasons=[]
        if fraud.risk_level=='high': reasons.append('HIGH_TRANSACTION_FRAUD_RISK')
        if txn is not None and txn.amount >= Decimal(str(self.cfg['high_value_threshold'])): reasons.append('HIGH_VALUE')
        if fraud.data_sufficiency=='low' and fraud.transaction_fraud_score>=.45: reasons.append('LOW_DATA_SUFFICIENCY')
        if fraud.claim_abuse_score>=self.cfg['high_abuse_score']: reasons.append('HIGH_CLAIM_ABUSE_RISK')
        if classification.intent in ('ambiguous','out_of_scope'): reasons.append('AMBIGUOUS_INTENT')
        if policy.human_review_required and 'HIGH_CLAIM_ABUSE_RISK' not in reasons: reasons.append('POLICY_REVIEW')
        human=policy.human_review_required or bool(reasons)
        if policy.primary_action==Action.deny: human=True
        auto=(not human and policy.primary_action in {Action.provisional_credit,Action.chargeback,Action.investigate})
        rid='REC-'+hashlib.sha256(f'{case_id}:{classification.model_dump_json()}:{fraud.model_dump_json()}'.encode()).hexdigest()[:12]
        rationale=f"Policy {policy.rule_id} matched. Fraud risk is {fraud.risk_level} ({fraud.transaction_fraud_score:.2f})."
        if reasons: rationale += ' Escalation: '+', '.join(reasons)+'.'
        return DecisionRecommendation(recommendation_id=rid,case_id=case_id,primary_action=policy.primary_action,
            additional_actions=[Action.investigate] if 'HIGH_TRANSACTION_FRAUD_RISK' in reasons and policy.primary_action==Action.provisional_credit else [],
            human_review_required=human,escalation_reasons=reasons,citations=[policy.citation],fraud=fraud,policy=policy,
            evidence_completeness=1.0 if evidence_complete else .5,auto_disposition_eligible=auto,rationale=rationale)

class InvariantEngine:
    def validate(self,rec):
        errors=[]
        if rec.primary_action not in rec.policy.eligible_actions: errors.append('INV-001')
        if rec.primary_action==Action.provisional_credit and (rec.evidence_completeness<.7 or rec.fraud.claim_abuse_score>=.75) and not rec.human_review_required: errors.append('INV-002')
        if rec.primary_action==Action.chargeback and not rec.citations: errors.append('INV-003')
        if rec.primary_action==Action.deny and not rec.human_review_required: errors.append('INV-004')
        if 'HIGH_VALUE' in rec.escalation_reasons and not rec.human_review_required: errors.append('INV-012')
        if bool(rec.escalation_reasons)!=rec.human_review_required: errors.append('INV-005')
        if errors: raise ValueError('INVARIANT_VIOLATION:'+','.join(errors))
