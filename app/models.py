from __future__ import annotations
from decimal import Decimal
from enum import Enum
from typing import Any, Literal
from pydantic import BaseModel, Field, ConfigDict


class Role(str, Enum):
    analyst = "analyst"
    reviewer = "reviewer"
    customer = "customer"


class CaseState(str, Enum):
    NEW = "NEW"
    RUNNING = "RUNNING"
    CLASSIFIED = "CLASSIFIED"
    DATA_COLLECTED = "DATA_COLLECTED"
    FRAUD_ASSESSED = "FRAUD_ASSESSED"
    POLICY_EVALUATED = "POLICY_EVALUATED"
    RECOMMENDATION_READY = "RECOMMENDATION_READY"
    PENDING_REVIEW = "PENDING_REVIEW"
    NEEDS_INFO = "NEEDS_INFO"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"
    FAILED = "FAILED"


class ReviewState(str, Enum):
    NONE = "NONE"
    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    RESOLVED = "RESOLVED"
    EXPIRED = "EXPIRED"


class AuditState(str, Enum):
    PENDING = "PENDING"
    COMMITTED = "COMMITTED"
    RELEASABLE = "RELEASABLE"
    FAILED = "FAILED"


class Action(str, Enum):
    provisional_credit = "provisional_credit"
    chargeback = "chargeback"
    investigate = "investigate"
    deny = "deny"


class TrustLevel(str, Enum):
    MODEL_INFERRED = "MODEL_INFERRED"
    CUSTOMER_CONFIRMED = "CUSTOMER_CONFIRMED"
    REVIEWER_VERIFIED = "REVIEWER_VERIFIED"
    SYSTEM_VERIFIED = "SYSTEM_VERIFIED"


class Transaction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    transaction_id: str
    customer_id: str
    amount: Decimal
    currency: str = "INR"
    status: Literal["authorized", "settled", "reversed", "declined"] = "settled"
    merchant: str
    merchant_id: str = "M-UNKNOWN"
    category: str
    mcc: str = "5999"
    country: str = "IN"
    card_present: bool = True
    authentication: Literal["none", "otp", "3ds", "pin", "chip"] = "3ds"
    device_id: str | None = None
    channel: Literal["atm", "pos", "web", "mobile", "ecommerce"] = "ecommerce"
    ip_address: str | None = None
    ip_country: str | None = None
    browser: str | None = None
    os_version: str | None = None
    timestamp: str


class CustomerProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    customer_id: str
    full_name: str = "Synthetic Customer"
    home_country: str = "IN"
    known_device_ids: list[str] = Field(default_factory=list)
    typical_categories: list[str] = Field(default_factory=list)
    tenure_days: int = 1000
    account_status: Literal["active", "restricted", "closed"] = "active"
    identity_verified: bool = True
    kyc_status: Literal["verified", "pending", "restricted"] = "verified"
    phone_masked: str = "+91******1234"
    email_masked: str = "synthetic@example.com"
    address_masked: str = "Synthetic address"
    branch_name: str = "Asteria Bank · Digital Branch"
    relationship_since: str = "2022-01-01"
    risk_segment: Literal["standard", "elevated", "high"] = "standard"


class BankAccount(BaseModel):
    account_id: str
    customer_id: str
    account_type: Literal["savings", "current"] = "savings"
    currency: str = "INR"
    current_balance: Decimal
    available_balance: Decimal
    ledger_balance: Decimal
    hold_amount: Decimal = Decimal("0.00")
    status: Literal["active", "blocked"] = "active"
    masked_account_number: str = "XXXXXX0000"
    branch_name: str = "Asteria Bank · Digital Branch"
    ifsc_masked: str = "ASTERIAXXXX"
    opened_on: str = "2022-01-01"


class CardSummary(BaseModel):
    card_id: str
    customer_id: str
    card_type: Literal["debit", "credit"] = "credit"
    network: Literal["Visa", "Mastercard", "RuPay"] = "Visa"
    masked_number: str
    status: Literal["active", "blocked", "expired"] = "active"
    credit_limit: Decimal | None = None
    outstanding: Decimal | None = None
    payment_due: Decimal | None = None
    due_date: str | None = None


class Statement(BaseModel):
    statement_id: str
    account_id: str
    period_start: str
    period_end: str
    opening_balance: Decimal
    credits: Decimal
    debits: Decimal
    closing_balance: Decimal
    statement_date: str
    status: Literal["issued", "pending"] = "issued"


class PriorDispute(BaseModel):
    dispute_id: str
    customer_id: str
    transaction_id: str
    dispute_type: str
    outcome: str
    created_at: str = "2026-01-01T00:00:00Z"


class DisputeClaim(BaseModel):
    dispute_message: str = Field(min_length=1, max_length=5000)
    claimed_transaction_id: str | None = None
    claimed_amount: Decimal | None = None
    claimed_date: str | None = None
    claimed_merchant: str | None = None


class Principal(BaseModel):
    actor_id: str
    role: Role
    team: str = "default"
    customer_scope: str | None = None


class AccessContext(BaseModel):
    context_id: str
    case_id: str
    customer_id: str
    actor_id: str
    role: Role
    permissions: list[str]
    expires_at: float
    audience: str = "mcp"
    nonce: str = ""


class QueryRewrite(BaseModel):
    rewritten_query: str = Field(min_length=1, max_length=300)


class Classification(BaseModel):
    intent: Literal[
        "unauthorized",
        "merchant_dispute",
        "policy_query",
        "transaction_query",
        "ambiguous",
        "out_of_scope",
    ]
    confidence: float = Field(ge=0, le=1)
    source: Literal["fast_path", "llm", "menu_selection"] = "llm"
    transaction_id: str | None = None
    claimed_amount: Decimal | None = None
    claimed_merchant: str | None = None


class BehavioralSignals(BaseModel):
    transactions_last_15m: int | None = None
    transactions_last_1h: int | None = None
    avg_transaction_amount_30d: Decimal | None = None
    median_transaction_amount_30d: Decimal | None = None
    typical_active_hour_start: int | None = None
    typical_active_hour_end: int | None = None
    session_duration_seconds: float | None = None
    typing_velocity_delta: float | None = None
    navigation_anomaly_score: float | None = None


class DeviceNetworkSignals(BaseModel):
    device_known: bool | None = None
    ip_country: str | None = None
    ip_risk_score: float | None = None
    proxy_detected: bool | None = None
    vpn_detected: bool | None = None
    geographic_distance_km: float | None = None
    time_since_previous_transaction_min: float | None = None


class IdentityRiskSignals(BaseModel):
    identity_verified: bool | None = None
    account_age_days: int | None = None
    account_takeover_flag: bool = False
    sanctions_hit: bool = False
    pep_hit: bool = False


class CaseRiskContext(BaseModel):
    triggered_scenario: str | None = None
    linked_case_count: int = 0
    security_flags: list[str] = Field(default_factory=list)
    analyst_notes: list[str] = Field(default_factory=list)


class ExternalRiskSignals(BaseModel):
    sanctions_hit: bool | None = None
    pep_hit: bool | None = None
    negative_media_hit: bool | None = None


class AccountSnapshot(BaseModel):
    account: BankAccount
    card: CardSummary
    statements: list[Statement] = Field(default_factory=list)


class FraudResult(BaseModel):
    transaction_fraud_score: float
    claim_abuse_score: float
    risk_level: Literal["low", "medium", "high"]
    factors: list[str]
    data_sufficiency: Literal["low", "medium", "high"]
    behavioral: BehavioralSignals = Field(default_factory=BehavioralSignals)
    device_network: DeviceNetworkSignals = Field(default_factory=DeviceNetworkSignals)
    identity: IdentityRiskSignals = Field(default_factory=IdentityRiskSignals)
    case_risk: CaseRiskContext = Field(default_factory=CaseRiskContext)


class PolicyOutcome(BaseModel):
    rule_id: str
    eligible_actions: list[Action]
    primary_action: Action
    human_review_required: bool
    citation: str
    matched: bool
    policy_version: str = "1.0.0"
    retrieved_sources: list[str] = Field(default_factory=list)


class DecisionRecommendation(BaseModel):
    recommendation_id: str
    case_id: str
    primary_action: Action
    additional_actions: list[Action] = Field(default_factory=list)
    human_review_required: bool
    escalation_reasons: list[str] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)
    fraud: FraudResult
    policy: PolicyOutcome
    evidence_completeness: float
    auto_disposition_eligible: bool
    rationale: str = ""


class FinalDisposition(BaseModel):
    task_id: str
    case_id: str
    action: Action
    reviewer_id: str
    verdict: str
    reason_code: str
    note: str = ""


class ReviewTask(BaseModel):
    task_id: str
    case_id: str
    recommendation_id: str
    version: int = 0
    status: ReviewState = ReviewState.PENDING
    reviewer_id: str | None = None
    claimed_by: str | None = None
    claim_expires_at: float | None = None
    expires_at: float | None = None


class DecisionProvenance(BaseModel):
    run_id: str
    case_id: str
    provider_attempts: list[dict[str, Any]] = Field(default_factory=list)
    model_id: str
    provider_used: str = "unknown"
    policy_version: str
    config_hash: str
    inputs_snapshot_id: str
    git_sha: str = "working-tree"
    prompt_hash: str | None = None
    rag_index_hash: str | None = None
    created_at: float | None = None


class InvestigationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claim: DisputeClaim
    case_id: str
    actor_id: str
    role: Role


class RunResponse(BaseModel):
    case_id: str
    run_id: str
    case_state: CaseState
    recommendation: DecisionRecommendation | None = None
    final_disposition: FinalDisposition | None = None
    review_task: ReviewTask | None = None
    clarification: list[str] = Field(default_factory=list)
    transaction_candidates: list[dict[str, Any]] = Field(default_factory=list)
    customer_view: str = ""
    analyst_view: dict[str, Any] = Field(default_factory=dict)
    provenance: DecisionProvenance | None = None
