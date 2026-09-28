# Model / System Card

## System

Transaction Dispute & Fraud-Triage Copilot.

## Intended use

Assist trained dispute analysts in triaging synthetic transaction disputes, gathering structured case evidence, calculating deterministic fraud risk indicators, retrieving chargeback policy, and producing a recommendation subject to deterministic gates and human review.

## Models

- Application model: configured Gemini model (`GEMINI_MODEL`), primary.
- Fallback model: configured Groq model (`GROQ_MODEL`) per the changed assessment rule.
- Evaluation judge: configured Gemini judge model (`GEMINI_JUDGE_MODEL`).

## Data

All assessment transactions, customers, statements, account records and policies are synthetic.

## Key system boundary

The LLM classifies and interprets language. Deterministic software calculates fraud features, evaluates policy, determines eligibility, applies escalation and validates release invariants.

## Limitations

Synthetic fraud patterns do not establish real-world fraud model calibration. External banking integrations, live regulatory feeds and production identity infrastructure are out of scope.

## Known failure modes

See `docs/failure-analysis.md` generated from real runs.
