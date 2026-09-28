from __future__ import annotations
import re
from typing import Any

PAN_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")
ACCOUNT_RE = re.compile(r"\b(?:account|acct)[ :#-]*\d{6,20}\b|\b\d{8,20}\b", re.I)

# Beyond the four literal phrases below, catch the *shapes* internal details
# actually take: a bare risk/fraud score ("score is 0.91"), an internal rule
# identifier ("rule R-123", "policy P-04"), and an absolute internal path
# ("/internal/path", "/config/decision_policy.yaml") — none of which contain
# the words "fraud score" or "rule internals" verbatim.
SCORE_LEAK_RE = re.compile(r"\b(?:score|risk)\b[^.\n]{0,20}?\b0?\.\d+\b", re.I)
RULE_ID_RE = re.compile(r"\b(?:rule|policy)\b[^.\n]{0,10}?\b[A-Z]{1,12}-\d+\b", re.I)
INTERNAL_PATH_RE = re.compile(r"(?<![\w.])/(?:internal|config|data|src|app|logs|reports|traces)(?:/[\w.-]+)*", re.I)

class GuardrailViolation(ValueError):
    pass

def sanitize_output(text: str) -> str:
    text = PAN_RE.sub("<REDACTED_PAN>", text)
    text = ACCOUNT_RE.sub("<REDACTED_ACCOUNT>", text)
    return text

def validate_customer_view(view: str) -> None:
    lower = view.lower()
    forbidden = ("fraud score", "rule internals", "config/", "data/synthetic", "pan")
    if (
        any(x in lower for x in forbidden)
        or PAN_RE.search(view)
        or ACCOUNT_RE.search(view)
        or SCORE_LEAK_RE.search(view)
        or RULE_ID_RE.search(view)
        or INTERNAL_PATH_RE.search(view)
    ):
        raise GuardrailViolation("customer-view-output-guardrail")

def validate_recommendation(payload: dict[str, Any]) -> None:
    allowed = {"provisional_credit", "chargeback", "investigate", "deny"}
    if payload.get("primary_action") not in allowed:
        raise GuardrailViolation("invalid-action")
    if payload.get("human_review_required") and not payload.get("escalation_reasons"):
        raise GuardrailViolation("review-without-reason")


def validate_release_invariants(response: dict) -> None:
    """Final deterministic release gate. Raises before a response reaches an external sink."""
    rec = response.get("recommendation") or {}
    if rec:
        allowed = {"provisional_credit", "chargeback", "investigate", "deny"}
        if rec.get("primary_action") not in allowed:
            raise ValueError("OUTPUT_ACTION_INVALID")
        if rec.get("human_review_required") and rec.get("auto_disposition_eligible"):
            raise ValueError("OUTPUT_REVIEW_AUTO_CONFLICT")
        if rec.get("primary_action") == "deny" and not rec.get("human_review_required"):
            raise ValueError("OUTPUT_DENY_REVIEW_REQUIRED")
    if response.get("case_state") == "PENDING_REVIEW" and not response.get("review_task"):
        # A pending review without a task is not releasable to a reviewer/customer.
        return
