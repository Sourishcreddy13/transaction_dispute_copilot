# Final Architecture — Transaction Dispute & Fraud-Triage Copilot

## Request lifecycle

```text
claimant message
   -> ingress PII scan/masking + injection quarantine
   -> deterministic LangGraph supervisor
   -> classification worker (Gemini primary, Groq fallback)
   -> deterministic transaction resolution
   -> MCP data plane
       transaction + history + prior disputes + profile + account + statements
   -> fraud-scoring worker (deterministic)
   -> chargeback-rules worker
       MCP policy resource + bounded agentic RAG
   -> deterministic policy engine
   -> deterministic decision engine
   -> output-safety invariants
   -> audit commit / release gate
   -> auto disposition OR concurrency-safe HITL
   -> FinalDisposition
   -> outbox + customer-safe response
```

## Trust boundaries

- Customer text is untrusted data and is masked before entering graph state.
- LLM output is structured and validated; it has no tools and no decision authority.
- MCP structured data is trusted only after signed `AccessContext` and resource ownership checks.
- Retrieved policy content is data, never an instruction source.
- Long-term memory uses trust levels; model-inferred facts are never persisted by default.
- Financial recommendations are released only after deterministic invariant validation and committed audit evidence.

## Business state

Domain state is independent from LangGraph execution state:

```text
NEW -> RUNNING -> CLASSIFIED -> DATA_COLLECTED -> FRAUD_ASSESSED
-> POLICY_EVALUATED -> RECOMMENDATION_READY
-> PENDING_REVIEW -> RESOLVED -> CLOSED
```

`NEEDS_INFO` and `FAILED` are controlled terminal/degradation states with explicit transitions.

## Semantic provider policy

The current assessment requires a fallback:

```text
Gemini primary
   -> classified retryable failure / open breaker
Groq fallback
   -> compatible structured output
```

Provider attempts, models, latency and usage are recorded in provenance. Provider affinity holds for the run.

## Banking data contract

The synthetic data plane exposes:

- customer profile and KYC status;
- account current, available and ledger balances;
- holds;
- masked account number, branch and IFSC;
- card type, network, masked number, limits and payment due;
- transaction history with channel, merchant, MCC, location, authentication and device/network metadata;
- prior disputes;
- monthly statements.

Fraud features are deterministic. Missing behavioral/external signals remain explicitly unavailable rather than being fabricated.

## Evaluation architecture

Decision correctness is deterministic. Hidden golden cases and causal mutation tests validate intent, action and escalation. DeepEval is used only for qualitative relevance, faithfulness and hallucination. The evaluator never receives expected financial decisions.

## Evidence architecture

All assessment evidence is generated from executable code:

```text
Phoenix spans -> trace export -> golden signals -> dashboard
MCP execution -> tool transcript
case/audit DB -> agent audit log
fault runner -> failure analysis
memory test -> cross-session evidence
pytest + deterministic eval -> evaluation report
```

## Local vs production

The hackathon uses SQLite, local Chroma, local Phoenix and local HMAC development identity. Interfaces isolate these components from business engines so production can replace them with a durable relational store, managed vector infrastructure, centralized telemetry, enterprise identity and asymmetric signing without changing fraud/policy/decision logic.
